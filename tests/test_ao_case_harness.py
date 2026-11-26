"""Prove immutable evidence packaging and measured generic incident proposals."""

from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import sys

import pytest
from ansible.errors import AnsibleFilterError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "files/ao"), str(ROOT / "playbooks/ai/filter_plugins")]
import ao_case as collect
import harness_case as harness
import ao_evidence as validator
import ao_gitops as writer
from test_ao_gitops_writer import COMMIT, REPO, handoff, runtime_evidence


def snapshot():
    context = collect.request_context(
        {"target_host": "web-01"}, REPO, "incident", ["web-01"]
    )
    contents = {
        "site.yml": "---\n- hosts: all\n  tasks: []\n",
        "group_vars/all/kernel_settings.yml": 'kernel_settings_baseline:\n  somaxconn:\n    name: net.core.somaxconn\n    value: "1024"\n',
        "group_vars/web_servers/kernel_settings.yml": "# inactive 65535 example\n",
        "tasks/prepare_vars.yml": "# consumed merge\n",
    }
    tree = {"truncated": False, "tree": []}
    responses = []
    for path, text in contents.items():
        raw = text.encode()
        sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        item = {"path": path, "sha": sha, "size": len(raw), "type": "blob"}
        tree["tree"].append(item)
        responses.append(
            {
                "item": item,
                "status": 200,
                "json": {
                    "encoding": "base64",
                    "sha": sha,
                    "content": base64.b64encode(raw).decode(),
                },
            }
        )
    return (
        collect.repository_snapshot(context, {"sha": COMMIT}, tree, responses),
        tree,
        responses,
    )


def foundation():
    data, _, _ = snapshot()
    return collect.finish_collection(
        data,
        {
            "ansible_hostname": "web-01",
            "ansible_env": {"SECRET": "never export"},
            "ansible_lvm": {
                "vgs": {"vg_data": {"free_g": "10.00"}},
                "lvs": {"lv_data": {"vg": "vg_data", "size_g": "2.00"}},
            },
            "ansible_mounts": [
                {
                    "mount": "/data",
                    "device": "/dev/mapper/vg_data-lv_data",
                    "fstype": "xfs",
                    "size_total": 2 * 1024**3,
                    "size_available": 100 * 1024**2,
                }
            ],
        },
        ["web_servers"],
        123,
    )


def test_collector_reads_baseline_and_keeps_selected_measurements_only():
    data = foundation()
    assert "group_vars/all/kernel_settings.yml" in data["repository_files"]
    assert data["host_facts"]["lvm"]["vgs"]["vg_data"]["free_g"] == "10.00"
    assert "never export" not in json.dumps(data)
    assert data["packet"]["collection_complete"] is True


@pytest.mark.parametrize(
    "failure", ["truncated", "oversize", "missing_file", "changed_blob"]
)
def test_incomplete_or_changed_snapshots_fail_closed(failure):
    data, tree, responses = snapshot()
    if failure == "truncated":
        tree["truncated"] = True
    if failure == "oversize":
        tree["tree"][0]["size"] = 131073
    if failure == "missing_file":
        responses.pop()
    if failure == "changed_blob":
        responses[0]["json"]["content"] = base64.b64encode(b"changed").decode()
    context = collect.request_context(
        {"target_host": "web-01"}, REPO, "incident", ["web-01"]
    )
    with pytest.raises(AnsibleFilterError):
        collect.repository_snapshot(context, {"sha": COMMIT}, tree, responses)


@pytest.mark.parametrize(
    "event",
    [
        {"target_host": "all"},
        {"target_host": "web-*"},
        {"target_host": "other"},
        {"repository": "attacker/repo", "target_host": "web-01"},
    ],
)
def test_collection_never_follows_event_to_another_repo_or_inventory_pattern(event):
    with pytest.raises(AnsibleFilterError):
        collect.request_context(event, REPO, "incident", ["web-01"])


def test_monitoring_symptoms_do_not_require_a_policy_directive():
    assert (
        collect.request_context(
            {"description": "web-01 /data is 95% full"},
            REPO,
            "incident",
            ["web-01", "db-01"],
        )["target_host"]
        == "web-01"
    )
    assert (
        collect.request_context(
            {"description": "web-01 and db-01"}, REPO, "incident", ["web-01", "db-01"]
        )["target_host"]
        is None
    )


def compiled(handoff):
    _, report, _ = handoff
    collection = foundation()
    invocation = runtime_evidence(report)["host"]
    invocation["result"]["agent_trace"]["steps"][-1]["tool_output"] = json.dumps(
        ["65535\n"]
    )
    case = harness.prepare_case(collection, invocation)
    raw = deepcopy(report)
    for key in (
        "incident",
        "target_host",
        "repository_context",
        "evidence",
        "resolved_findings",
        "policy_basis",
    ):
        raw.pop(key, None)
    raw["evidence_ids"] = list(case["registry"])
    host_id = next(
        key for key, value in case["registry"].items() if value["tool"] == "read_file"
    )
    raw["proposal_basis"] = {
        "kind": "adopt_observed",
        "rationale": "Preserve verified runtime value for review; future automation would restore 1024 otherwise.",
        "assumptions": ["Maintainer should confirm intended concurrency."],
        "measurement_evidence_id": host_id,
        "measurement_path": [],
        "observed_value": "65535",
        "proposed_value": "65535",
    }
    raw["capacity_assessment"] = None
    plan = {
        "pr_title": "Declare runtime tuning for review",
        "commit_message": "Propose host tuning",
        "files": [
            {
                "path": "host_vars/web-01/kernel_settings.yml",
                "content": "kernel_settings_host: {}",
                "evidence_ids": list(case["registry"]),
            }
        ],
    }
    return case, {"report": raw, "plan": plan}


def test_observed_value_can_be_proposed_without_preapproved_policy(handoff):
    case, raw = compiled(handoff)
    result = harness.compile_proposal(case, raw, "execution-123")
    verdict = validator.validate_evidence(
        result["report"], result["packet"], {"blocking_findings": []}, REPO
    )
    assert verdict["valid"], verdict
    assert result["report"]["proposal_basis"]["requires_maintainer_approval"] is True
    assert result["plan"]["files"][0]["expected_sha"] is None
    assert raw["report"].get("evidence") is None


@pytest.mark.parametrize(
    "failure", ["invented_id", "duplicate_id", "other_host_overlay", "unread_existing"]
)
def test_compiler_cannot_invent_evidence_or_file_identity(handoff, failure):
    case, raw = compiled(handoff)
    if failure == "invented_id":
        raw["report"]["evidence_ids"].append("made-up")
    if failure == "duplicate_id":
        raw["report"]["evidence_ids"] *= 2
    if failure == "other_host_overlay":
        raw["plan"]["files"][0]["path"] = "host_vars/db-01/kernel_settings.yml"
    if failure == "unread_existing":
        case["collection"]["manifest_paths"].append(raw["plan"]["files"][0]["path"])
    with pytest.raises(ValueError):
        harness.compile_proposal(case, raw, "execution")


@pytest.mark.parametrize(
    "failure", ["wrong_value", "missing_measurement", "incomplete", "review_blocker"]
)
def test_observed_proposal_does_not_bypass_measurement_or_reviewer_gates(
    handoff, failure
):
    case, raw = compiled(handoff)
    result = harness.compile_proposal(case, raw, "execution")
    review = {"blocking_findings": []}
    if failure == "wrong_value":
        result["report"]["proposal_basis"]["proposed_value"] = "100000"
    if failure == "missing_measurement":
        result["report"]["proposal_basis"]["measurement_evidence_id"] = "unknown"
    if failure == "incomplete":
        result["packet"]["collection_complete"] = False
    if failure == "review_blocker":
        review["blocking_findings"] = ["missing backing facts"]
    assert not validator.validate_evidence(
        result["report"], result["packet"], review, REPO
    )["valid"]


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "no_lvm",
        "wrong_lv",
        "wrong_mount",
        "shrink",
        "no_headroom",
        "rounded_limit",
    ],
)
def test_capacity_proposals_require_measured_mapping_and_headroom(handoff, failure):
    case, raw = compiled(handoff)
    result = harness.compile_proposal(case, raw, "execution")
    report = result["report"]
    report["proposal_basis"]["kind"] = "capacity_change"
    report["capacity_assessment"] = {
        "volume_group": "vg_data",
        "logical_volume": "lv_data",
        "mount_point": "/data",
        "proposed_size_bytes": 4 * 1024**3,
    }
    if failure == "no_lvm":
        result["packet"].pop("host_facts")
    if failure == "wrong_lv":
        report["capacity_assessment"]["logical_volume"] = "other"
    if failure == "wrong_mount":
        report["capacity_assessment"]["mount_point"] = "/"
    if failure == "shrink":
        report["capacity_assessment"]["proposed_size_bytes"] = 1024**3
    if failure == "no_headroom":
        report["capacity_assessment"]["proposed_size_bytes"] = 20 * 1024**3
    if failure == "rounded_limit":
        report["capacity_assessment"]["proposed_size_bytes"] = 12 * 1024**3
    verdict = validator.validate_evidence(
        report, result["packet"], {"blocking_findings": []}, REPO
    )
    assert verdict["valid"] is (failure is None), verdict


@pytest.mark.parametrize(
    "failure", [None, "fleet_scope", "wrong_lv", "mismatched_size", "percentage"]
)
def test_storage_yaml_edit_is_bound_to_measured_resource(handoff, failure):
    case, raw = compiled(handoff)
    result = harness.compile_proposal(case, raw, "execution")
    report = result["report"]
    report["proposal_basis"]["kind"] = "capacity_change"
    report["capacity_assessment"] = {
        "volume_group": "vg_data",
        "logical_volume": "lv_data",
        "mount_point": "/data",
        "proposed_size_bytes": 4 * 1024**3,
    }
    source = "storage_baseline_pools:\n  data_pool:\n    name: vg_data\n    volumes:\n      data_vol:\n        name: lv_data\n        mount_point: /data\n        size: 2g\n"
    result["packet"]["group_vars/all/storage.yml"] = collect.trace_record(
        "github_api_get_file",
        {
            "owner": "example",
            "repo": "state",
            "sha": COMMIT,
            "path": "group_vars/all/storage.yml",
        },
        source,
    )
    proposed = {
        "storage_host_pools": {"data_pool": {"volumes": {"data_vol": {"size": "4g"}}}}
    }
    path = ("storage_host_pools", "data_pool", "volumes", "data_vol", "size")
    item = {"path": "host_vars/web-01/storage.yml"}
    if failure == "fleet_scope":
        item["path"] = "group_vars/all/storage.yml"
    if failure == "wrong_lv":
        report["capacity_assessment"]["logical_volume"] = "other"
    if failure == "mismatched_size":
        report["capacity_assessment"]["proposed_size_bytes"] = 5 * 1024**3
    if failure == "percentage":
        proposed["storage_host_pools"]["data_pool"]["volumes"]["data_vol"][
            "size"
        ] = "100%"
    if failure:
        with pytest.raises(AnsibleFilterError):
            writer.validate_storage_edit(item, path, proposed, report, result["packet"])
    else:
        writer.validate_storage_edit(item, path, proposed, report, result["packet"])


def test_feedback_keeps_original_pr_and_target_marker(handoff):
    event = {
        "pull_request_number": 8,
        "description": "Please adjust the proposed headroom.",
    }
    pr = {
        "state": "open",
        "body": '<!-- ao-context {"target_host":"web-01"} -->',
        "base": {"repo": {"full_name": "example/state"}, "ref": "main"},
        "head": {
            "repo": {"full_name": "example/state"},
            "ref": "aiops/original",
            "sha": COMMIT,
        },
    }
    context = collect.request_context(event, REPO, "pr_review", ["web-01"], pr)
    assert context["target_host"] == "web-01"
    case, raw = compiled(handoff)
    case["collection"].update(
        mode="pr_review", repository_context=context["repository_context"]
    )
    result = harness.compile_proposal(case, raw, "new-execution")
    assert result["plan"]["branch"] == "aiops/original"
    assert result["plan"]["pull_request_number"] == 8


def test_scope_concerns_can_be_resolved_by_a_narrow_proposal_but_review_stays_strict(
    handoff,
):
    case, raw = compiled(handoff)
    case["view"]["investigation"] = {
        "critical_gaps": ["Peer workload equivalence is unknown"]
    }
    raw["report"]["scope"] = "web-01 only; do not change peers without measurements"
    result = harness.compile_proposal(case, raw, "execution")
    assert result["report"]["decision"] == "ready"
    assert validator.validate_evidence(
        result["report"], result["packet"], {"blocking_findings": []}, REPO
    )["valid"]
    assert not validator.validate_evidence(
        result["report"],
        result["packet"],
        {"blocking_findings": ["The actual proposed scope is not justified"]},
        REPO,
    )["valid"]


def test_prompts_use_measured_views_without_duplicating_raw_packets():
    import yaml

    for path in (ROOT / "workflows/automation-orchestrator").glob("*.yaml"):
        nodes = {n["id"]: n for n in yaml.safe_load(path.read_text())["nodes"]}
        first = nodes["input_investigate"]["parameters"]["body"]["contexts"][0]
        assert "packet" not in first
        assert "repository_files" in first and "host_facts" in first
        assert set(nodes["input_review"]["parameters"]["body"]["contexts"][0]) == {
            "report",
            "plan",
        }
        assert (
            nodes["validate_evidence"]["parameters"]["extra_vars"]["ao_payload_b64"]
            == "${package_decision.result.body.payload_b64}"
        )


@pytest.mark.parametrize("value", ["65535", "100000"])
def test_adoption_plan_must_declare_its_verified_value(handoff, value):
    plan, report, review = handoff
    report["proposal_basis"] = {"kind": "adopt_observed", "proposed_value": value}
    if value == "65535":
        writer.validate_plan(plan, report, review, REPO)
    else:
        with pytest.raises(AnsibleFilterError, match="actually declare"):
            writer.validate_plan(plan, report, review, REPO)


def test_pr_report_includes_measured_output_with_bounded_labeled_previews(handoff):
    plan, report, review = handoff
    report["evidence"][0]["tool_output_excerpt"] = "65535\n"
    report["evidence"][1]["tool_output_excerpt"] = "x" * 1500 + "```"
    rendered = writer.render_report(report, plan, review, "execution")
    import re

    assert re.search(r"```text\s+65535\s+```", rendered)
    assert (
        "first 1000 characters; complete matched excerpt retained in AAP artifacts"
        in rendered
    )
    assert "x" * 1001 not in rendered


def test_requested_kernel_keys_are_bounded_reads_not_paths_or_commands():
    assert collect.requested_sysctls(
        {"description": "Compare net.core.somaxconn and vm.swappiness."}
    ) == ["net.core.somaxconn", "vm.swappiness"]
    assert collect.requested_sysctls({"description": "/etc/shadow --all; $HOME"}) == []
    with pytest.raises(AnsibleFilterError):
        collect.requested_sysctls(
            {"description": " ".join("net.test.key" + str(i) for i in range(9))}
        )


def test_foundation_supplies_requested_sysctl_measurement_without_model_tool_choice():
    snapshot_data, _, _ = snapshot()
    snapshot_data["request"] = {"description": "Investigate net.core.somaxconn"}
    result = collect.finish_collection(
        snapshot_data,
        {"hostname": "web-01"},
        ["web_servers"],
        123,
        [
            {
                "item": "net.core.somaxconn",
                "encoding": "base64",
                "source": "/proc/sys/net/core/somaxconn",
                "content": base64.b64encode(b"65535\n").decode(),
            }
        ],
    )
    assert result["host_facts"]["sysctl"]["net.core.somaxconn"] == "65535"
    case = harness.prepare_case(result, {})
    evidence = next(
        e for e in case["registry"].values() if e["tool"] == "ansible_slurp"
    )
    assert evidence["tool_output_excerpt"] == "65535\n"
    assert len(evidence["id"]) == 3
    assert case["view"]["observed_values"] == [
        {
            "resource": "net.core.somaxconn",
            "actual_value": "65535",
            "evidence_id": evidence["id"],
            "measurement_path": [],
        }
    ]


def test_real_foundation_measurement_can_support_adoption_without_mcp_call(handoff):
    data, _, _ = snapshot()
    data["request"] = {"description": "Investigate net.core.somaxconn"}
    collection = collect.finish_collection(
        data,
        {"hostname": "web-01"},
        ["web_servers"],
        123,
        [
            {
                "item": "net.core.somaxconn",
                "encoding": "base64",
                "source": "/proc/sys/net/core/somaxconn",
                "content": base64.b64encode(b"65535\n").decode(),
            }
        ],
    )
    case = harness.prepare_case(collection, {})
    _, raw = compiled(handoff)
    ids = list(case["registry"])
    raw["report"]["evidence_ids"] = ids
    raw["report"]["proposal_basis"]["measurement_evidence_id"] = next(
        k for k, v in case["registry"].items() if v["tool"] == "ansible_slurp"
    )
    raw["plan"]["files"][0]["evidence_ids"] = ids
    result = harness.compile_proposal(case, raw, "real-foundation")
    verdict = validator.validate_evidence(
        result["report"], result["packet"], {"blocking_findings": []}, REPO
    )
    assert verdict["valid"], verdict
    assert all(
        r["tool"] != "read_handoff"
        for r in validator.successful_results(result["packet"])
    )
