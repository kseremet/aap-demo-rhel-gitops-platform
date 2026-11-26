"""Offline regression tests for rejected plans and atomic GitHub publication.

Run with pytest. Integration cases use a loopback-only fake GitHub API and a
scratch copy of the playbook, so neither real credentials nor remote writes run.
"""

import base64
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest
import yaml
from ansible.errors import AnsibleFilterError

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ao_gitops", ROOT / "playbooks/ai/filter_plugins/ao_gitops.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
COMMIT = "a" * 40
REPO = "https://github.com/example/state.git"
POLICY_REQUIREMENT = "Set net.core.somaxconn to 65535 on web-01 only."
ORIGINAL = "---\n# Preserve this policy comment.\nkernel_settings_host:\n  backlog:\n    name: net.core.somaxconn\n    value: '1024'\n  swappiness:\n    name: vm.swappiness\n    value: '10'\n"
UPDATED = ORIGINAL.replace("'1024'", "'65535'")
PATH = "host_vars/web-01/kernel_settings.yml"


def runtime_evidence(report):
    def invocation(name, args, output):
        return {
            "id": name + "-invocation",
            "result": {
                "agent_trace": {
                    "steps": [
                        {
                            "type": "tool_call",
                            "call_id": "call-0",
                            "tool_name": name,
                            "tool_input": args,
                        },
                        {
                            "type": "tool_result",
                            "call_id": "call-0",
                            "tool_name": name,
                            "status": "success",
                            "tool_output": output,
                        },
                    ]
                }
            },
        }

    return {
        "request": {"policy_requirement": POLICY_REQUIREMENT},
        "commit": invocation(
            "get_commit", {"owner": "example", "repo": "state"}, COMMIT
        ),
        "file": invocation(
            "get_file_contents",
            {"owner": "example", "repo": "state", "ref": COMMIT, "path": PATH},
            "declared backlog 1024",
        ),
        "identity": invocation(
            "get_system_information", {"host": "web-01"}, "hostname: web-01"
        ),
        "host": invocation(
            "read_file",
            {"host": "web-01", "path": "/proc/sys/net/core/somaxconn"},
            "net.core.somaxconn=65535",
        ),
    }


def blob(content):
    raw = content.encode()
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


@pytest.fixture
def handoff():
    plan = dict(
        operation="create_pr",
        owner="example",
        repository="state",
        base_branch="main",
        branch="aiops/remediation",
        read_commit=COMMIT,
        pull_request_number=None,
        pr_title="Declare the verified host policy",
        commit_message="Declare the verified host policy",
        files=[
            dict(
                path=PATH,
                expected_sha=blob(ORIGINAL),
                content=UPDATED,
                reason="Apply the verified policy only to the diagnosed host.",
                evidence_ids=["host-1", "repo-1"],
                setting_changes=[
                    dict(
                        path=["kernel_settings_host", "backlog", "value"],
                        before="1024",
                        after="65535",
                    )
                ],
            )
        ],
    )
    report = dict(
        decision="ready",
        policy_basis=dict(
            status="established",
            origin="request",
            requirement=POLICY_REQUIREMENT,
            evidence_ids=[],
        ),
        conclusion_status="confirmed",
        blocking_findings=[],
        resolved_findings=[],
        incident="Investigate web-01 policy mismatch.",
        target_host="web-01",
        repository_context=dict(
            owner="example",
            repository="state",
            base_branch="main",
            head_branch=None,
            read_commit=COMMIT,
            pull_request_number=None,
        ),
        root_cause="The active host declaration differs from the approved web-01 policy.",
        scope="web-01 only; web-02 and the fleet baseline are unchanged.",
        reconciliation="site.yml loads tasks/prepare_vars.yml, merges baseline/group/host, then calls kernel_settings.",
        recommendation="Declare the approved host-specific backlog value.",
        evidence=[
            dict(
                id="host-1",
                tool="read_file",
                tool_output_excerpt="net.core.somaxconn=65535",
                source="web-01 /proc/sys/net/core/somaxconn",
                observation="65535 observed",
            ),
            dict(
                id="repo-1",
                tool="get_file_contents",
                tool_output_excerpt="declared backlog 1024",
                source=PATH,
                observation="1024 declared at the pinned commit",
            ),
        ],
        alternatives=[
            "Fleet-wide tuning is unnecessary for this host-specific policy."
        ],
        validation_plan=[
            "Run syntax checks, then merge and reconcile.",
            "Verify web-01 and unaffected hosts read-only.",
        ],
        limitations=["No reconciliation or CI has run during investigation."],
    )
    review = dict(
        approved=True,
        summary="The exact scoped edit is consumed by the existing role.",
        blocking_findings=[],
        reviewed_paths=[PATH],
    )
    return plan, report, review


def response(content=ORIGINAL):
    return dict(
        status=200,
        json=dict(
            type="file",
            encoding="base64",
            sha=blob(content),
            content=base64.b64encode(content.encode()).decode(),
        ),
    )


def test_valid_change_preserves_existing_settings(handoff):
    plan, report, review = handoff
    result = MODULE.validate_plan(plan, report, review, REPO)
    assert result == plan and result is not plan
    entry = MODULE.validate_file(
        result["files"][0], response(), ["kernel_settings_host"]
    )
    assert entry["content"] == UPDATED
    assert entry["mode"] == "100644"


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "etc/sysctl.d/custom.yml",
        "roles/ssh/defaults/main.yml",
        "tcp_backlog_config.yaml",
        "/etc/sysctl.conf",
        "host_vars/../kernel.yml",
        "host_vars/web-02/kernel_settings.yml",
    ],
)
def test_rejects_unconnected_or_out_of_scope_paths(handoff, path):
    plan, report, review = handoff
    plan["files"][0]["path"] = path
    review["reviewed_paths"] = [path]
    with pytest.raises(AnsibleFilterError):
        MODULE.validate_plan(plan, report, review, REPO)


@pytest.mark.parametrize(
    "kind",
    [
        "unapproved",
        "blocker",
        "hypothesis",
        "no_change",
        "wrong_repo",
        "wrong_commit",
        "missing_evidence",
    ],
)
def test_only_confirmed_reviewed_plans_are_accepted(handoff, kind):
    plan, report, review = handoff
    if kind == "unapproved":
        review["approved"] = False
    if kind == "blocker":
        review["blocking_findings"] = ["Unsupported policy"]
    if kind == "hypothesis":
        report["conclusion_status"] = "hypothesis"
    if kind == "no_change":
        report["decision"] = "no_change"
    if kind == "wrong_repo":
        plan["repository"] = "other"
    if kind == "wrong_commit":
        report["repository_context"]["read_commit"] = "b" * 40
    if kind == "missing_evidence":
        plan["files"][0]["evidence_ids"] = ["invented"]
    with pytest.raises(AnsibleFilterError):
        MODULE.validate_plan(plan, report, review, REPO)


@pytest.mark.parametrize(
    "kind",
    [
        "stale",
        "missing",
        "truncated",
        "delete",
        "unrelated",
        "noop",
        "comments",
        "duplicate",
    ],
)
def test_rejects_stale_or_destructive_file_replacements(handoff, kind):
    item = handoff[0]["files"][0]
    current = response()
    if kind == "stale":
        current["json"]["sha"] = "b" * 40
    if kind == "missing":
        current = dict(status=404, json={})
    if kind == "truncated":
        current["json"]["content"] = base64.b64encode(b"short").decode()
    if kind == "delete":
        item["content"] = "kernel_settings_host: {}\n"
    if kind == "unrelated":
        item["content"] = UPDATED.replace("'10'", "'20'")
    if kind == "noop":
        item["content"] = ORIGINAL
    if kind == "comments":
        item["content"] = UPDATED.replace("# Preserve this policy comment.\n", "")
    if kind == "duplicate":
        item["content"] = UPDATED + "kernel_settings_host: {}\n"
    with pytest.raises(AnsibleFilterError):
        MODULE.validate_file(item, current, ["kernel_settings_host"])


def test_new_host_overlay_requires_verified_absence(handoff):
    item = handoff[0]["files"][0]
    item["expected_sha"] = None
    item["content"] = (
        "kernel_settings_host:\n  backlog:\n    name: net.core.somaxconn\n    value: '65535'\n"
    )
    item["setting_changes"] = [
        dict(
            path=["kernel_settings_host", "backlog", "name"],
            before=None,
            after="net.core.somaxconn",
        ),
        dict(
            path=["kernel_settings_host", "backlog", "value"],
            before=None,
            after="65535",
        ),
    ]
    assert (
        MODULE.validate_file(item, dict(status=404), ["kernel_settings_host"])[
            "content"
        ]
        == item["content"]
    )
    with pytest.raises(AnsibleFilterError):
        MODULE.validate_file(item, response(), ["kernel_settings_host"])


def test_rendered_report_keeps_evidence_and_validation(handoff):
    plan, report, review = handoff
    body = MODULE.render_report(report, plan, review, "run-42")
    for value in [
        "## Evidence",
        "read_file",
        "get_file_contents",
        "65535",
        "1024",
        "## Reconciliation and Scope",
        "## Alternatives Considered",
        "## Validation Plan (Pending)",
        "run-42",
        COMMIT,
    ]:
        assert value in body
    assert "<!-- aiops-agent-review -->" in body


def test_unused_namespace_is_rejected_even_in_an_allowed_file(handoff):
    item = handoff[0]["files"][0]
    with pytest.raises(AnsibleFilterError, match="namespace is not consumed"):
        MODULE.validate_file(item, response(), ["a_different_role_input"])


def test_consuming_variables_are_discovered_from_the_actual_loader():
    source = """- ansible.builtin.set_fact:
    kernel_settings_sysctl: '{{ _merged.values() | list }}'
  vars:
    _base: '{{ kernel_settings_baseline | default({}) }}'
    _group: '{{ kernel_settings_group | default({}) }}'
    _host: '{{ kernel_settings_host | default({}) }}'
"""
    assert MODULE.consumed_variables(response(source)) == [
        "kernel_settings_baseline",
        "kernel_settings_group",
        "kernel_settings_host",
    ]


def run_writer(
    tmp_path,
    handoff,
    *,
    invalid_second=False,
    stale_branch=False,
    revision=False,
    scm_checkout=False,
    encoded=False,
    bridge=False,
):
    plan, report, review = deepcopy(handoff)
    calls = []
    if revision:
        plan.update(operation="update_pr", pull_request_number=5)
        report["repository_context"].update(
            head_branch=plan["branch"], pull_request_number=5
        )
    if invalid_second:
        second = deepcopy(plan["files"][0])
        second["path"] = "host_vars/web-01/second.yml"
        second["content"] = UPDATED.replace("'10'", "'20'")
        plan["files"].append(second)
        review["reviewed_paths"].append(second["path"])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append(("GET", self.path, None))
            if "/contents/tasks/prepare_vars.yml" in self.path:
                loader = "- ansible.builtin.set_fact:\n    kernel_settings_sysctl: '{{ _merged.values() | list }}'\n  vars:\n    _base: '{{ kernel_settings_host | default({}) }}'\n"
                value = response(loader)["json"]
            elif "/contents/" in self.path:
                value = response()["json"]
            elif "/git/ref/" in self.path:
                value = dict(object=dict(sha="b" * 40 if stale_branch else COMMIT))
            elif "/git/commits/" in self.path:
                value = dict(tree=dict(sha="c" * 40))
            elif "/pulls/5" in self.path:
                value = dict(
                    state="open",
                    base=dict(ref="main"),
                    head=dict(
                        ref=plan["branch"],
                        sha=COMMIT,
                        repo=dict(full_name="example/state"),
                    ),
                )
            else:
                raise AssertionError(self.path)
            self.respond(200, value)

        def mutate(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path.endswith("/auth/token"):
                from urllib.parse import parse_qs

                calls.append((self.command, self.path, parse_qs(raw.decode())))
                self.respond(200, {"access_token": "fake-ao-token"})
                return
            data = json.loads(raw)
            calls.append((self.command, self.path, data))
            if "/webhooks/eda/" in self.path:
                self.respond(202, {"accepted": True})
                return
            self.respond(
                200 if self.command == "PATCH" else 201,
                dict(sha="d" * 40, html_url="https://github.com/example/state/pull/5"),
            )

        do_POST = mutate
        do_PATCH = mutate

        def respond(self, status, value):
            payload = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        shutil.copytree(
            ROOT / "playbooks/ai",
            tmp_path / "playbooks/ai",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        if not scm_checkout:
            (tmp_path / "group_vars/all").mkdir(parents=True)
            (tmp_path / "group_vars/all/demo_variables.yml").write_text("{}\n")
            (tmp_path / "vault.yml").write_text("{}\n")
        extra = dict(
            ao_plan=plan,
            ao_report=report,
            ao_review=review,
            ao_evidence=runtime_evidence(report),
            ao_evidence_review={"blocking_findings": []},
            github_api_token="fake-offline-token",
            demo_state_scm_url=REPO,
            demo_ai_github_api_url=f"http://127.0.0.1:{server.server_port}",
        )
        if bridge:
            extra.update(
                ao_event_payload={"target_host": "web-01", "summary": "Test incident"},
                ao_webhook_base_url=f"http://127.0.0.1:{server.server_port}",
                ao_webhook_path="rhel-gitops-incident",
                ao_webhook_client_id="fake-client",
                ao_webhook_client_secret="fake-secret",
                aap_validate_certs=False,
            )
        if encoded:
            keys = [
                "ao_plan",
                "ao_report",
                "ao_review",
                "ao_evidence",
                "ao_evidence_review",
            ]
            payload = {key: extra.pop(key) for key in keys}
            payload["ao_execution_id"] = "offline-encoded"
            extra["ao_payload_b64"] = base64.b64encode(
                json.dumps(payload).encode()
            ).decode()
        (tmp_path / "input.json").write_text(json.dumps(extra))
        (tmp_path / "ansible.cfg").write_text(
            "[defaults]\ninterpreter_python=auto_silent\n"
        )
        env = {
            **os.environ,
            "ANSIBLE_CONFIG": str(tmp_path / "ansible.cfg"),
            "ANSIBLE_HOME": str(tmp_path / "ansible-home"),
            "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-tmp"),
            "ANSIBLE_NOCOLOR": "1",
        }
        completed = subprocess.run(
            [
                shutil.which("ansible-playbook"),
                "-i",
                "localhost,",
                "-c",
                "local",
                (
                    "playbooks/ai/forward_event_to_ao.yml"
                    if bridge
                    else "playbooks/ai/run_ao_gitops_action.yml"
                ),
                "-e",
                "@input.json",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        return completed, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("revision", [False, True])
def test_writer_publishes_all_changes_as_one_commit(tmp_path, handoff, revision):
    completed, calls = run_writer(tmp_path, handoff, revision=revision)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    writes = [(method, path, data) for method, path, data in calls if method != "GET"]
    assert sum(path.endswith("/git/commits") for _, path, _ in writes) == 1
    assert not any("/contents/" in path for _, path, _ in writes)
    tree = next(data for _, path, data in writes if path.endswith("/git/trees"))
    assert tree["base_tree"] == "c" * 40
    assert tree["tree"][0]["content"] == UPDATED
    pr = next(data for _, path, data in writes if "/pulls" in path)
    assert "## Evidence" in pr["body"] and "## Validation Plan (Pending)" in pr["body"]
    if revision:
        ref = next(
            data
            for method, path, data in writes
            if method == "PATCH" and "/git/refs/" in path
        )
        assert ref["force"] is False
        comment = next(data for _, path, data in writes if path.endswith("/comments"))
        assert "<!-- aiops-agent-review -->" in comment["body"]
    else:
        ref = next(data for _, path, data in writes if path.endswith("/git/refs"))
        assert ref["ref"].startswith("refs/heads/aiops/remediation-")


@pytest.mark.parametrize("failure", ["invalid_second", "stale_branch"])
def test_preflight_failure_performs_no_github_mutations(tmp_path, handoff, failure):
    completed, calls = run_writer(tmp_path, handoff, **{failure: True})
    assert completed.returncode != 0
    assert calls and all(method == "GET" for method, _, _ in calls)


def test_writer_uses_the_existing_platform_state_repository_setting():
    variables = yaml.safe_load(
        (ROOT / "group_vars/all/demo_variables.yml.example").read_text()
    )
    casc = yaml.safe_load((ROOT / "group_vars/all/casc.yml").read_text())
    template = next(
        t for t in casc["controller_templates"] if t["name"] == "JT - AO GitOps Action"
    )
    assert "demo_state_scm_url" in variables
    assert template["extra_vars"]["demo_state_scm_url"] == "{{ demo_state_scm_url }}"
    assert "demo_project_scm_url" not in template["extra_vars"]


@pytest.mark.parametrize("revision", [False, True])
def test_aap_scm_checkout_needs_no_ignored_local_files(tmp_path, handoff, revision):
    completed, calls = run_writer(
        tmp_path, handoff, revision=revision, scm_checkout=True
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not (tmp_path / "vault.yml").exists()
    assert not (tmp_path / "group_vars/all/demo_variables.yml").exists()
    assert any(
        "/pulls" in path and method in ("POST", "PATCH") for method, path, _ in calls
    )


def test_eda_bridge_runs_from_a_clean_scm_checkout(tmp_path, handoff):
    completed, calls = run_writer(tmp_path, handoff, scm_checkout=True, bridge=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert [path for _, path, _ in calls] == [
        "/api/v1/auth/token",
        "/api/v1/webhooks/eda/rhel-gitops-incident",
    ]
    assert calls[-1][2] == {"target_host": "web-01", "summary": "Test incident"}
    casc = yaml.safe_load((ROOT / "group_vars/all/casc.yml").read_text())
    template = next(
        t for t in casc["controller_templates"] if t["name"] == "JT - AO Event Bridge"
    )
    assert template["ask_variables_on_launch"] is True
    assert template["extra_vars"]["aap_validate_certs"] == "{{ aap_validate_certs }}"


def test_encoded_writer_keeps_model_jinja_literal_and_rechecks_gates(tmp_path, handoff):
    handoff[1]["root_cause"] += " Literal template example: {{ 41 + 1 }}."
    completed, calls = run_writer(tmp_path, handoff, encoded=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    pr = next(
        data
        for method, path, data in calls
        if method != "GET" and path.endswith("/pulls")
    )
    assert "{{ 41 + 1 }}" in pr["body"]
