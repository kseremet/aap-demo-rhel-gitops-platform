"""Model writes complete files; code computes typed leaf changes before review."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from ansible.errors import AnsibleFilterError

from test_ao_case_harness import compiled
from test_ao_gitops_writer import REPO, blob, handoff, runtime_evidence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "playbooks/ai/filter_plugins"))
import ao_proposal
import ao_gitops
import ao_case
import harness_case


@pytest.fixture
def candidate(handoff):
    case, raw = compiled(handoff)
    collection = case["collection"]
    path = "tasks/prepare_vars.yml"
    text = '- name: Merge existing inputs\n  ansible.builtin.set_fact:\n    result: "{{ kernel_settings_host | default({}) }}"\n'
    collection["repository_files"][path] = {"sha": blob(text), "content": text}
    collection["packet"][path] = ao_case.trace_record(
        "github_api_get_file",
        {
            "owner": "example",
            "repo": "state",
            "sha": collection["repository_context"]["read_commit"],
            "path": path,
        },
        text,
    )
    case = harness_case.prepare_case(collection, case["packet"]["investigation"])
    raw["report"]["evidence_ids"] = list(case["registry"])
    raw["report"]["proposal_basis"]["measurement_evidence_id"] = next(
        k for k, v in case["registry"].items() if v["tool"] == "read_file"
    )
    file = raw["plan"]["files"][0]
    file.update(
        content='kernel_settings_host:\n  somaxconn:\n    name: net.core.somaxconn\n    value: "65535"\n',
        reason="Preserve the measured value for host-only maintainer review.",
        evidence_ids=list(case["registry"]),
    )
    return case, raw


def test_new_mapping_is_expanded_to_exact_typed_leaves_before_review(candidate):
    case, raw = candidate
    value = ao_proposal.prepare_proposal(case, raw, "preparation-test")
    assert value["report"]["decision"] == "ready", value
    changes = value["plan"]["files"][0]["setting_changes"]
    assert changes == [
        {
            "path": ["kernel_settings_host", "somaxconn", "name"],
            "before": None,
            "after": "net.core.somaxconn",
        },
        {
            "path": ["kernel_settings_host", "somaxconn", "value"],
            "before": None,
            "after": "65535",
        },
    ]
    assert "setting_changes" not in raw["plan"]["files"][0]
    assert value["plan"]["files"][0]["expected_sha"] is None


@pytest.mark.parametrize(
    "failure", ["unknown_id", "unconsumed_variable", "noop", "lost_comment"]
)
def test_invalid_preparation_is_blocked_with_a_reason(candidate, failure):
    case, raw = candidate
    file = raw["plan"]["files"][0]
    if failure == "unknown_id":
        raw["report"]["evidence_ids"].append("E99")
    if failure == "unconsumed_variable":
        file["content"] = 'invented_namespace:\n  value: "65535"\n'
    if failure in ("noop", "lost_comment"):
        file["path"] = "group_vars/web_servers/kernel_settings.yml"
        file["content"] = (
            "# inactive 65535 example\n" if failure == "noop" else file["content"]
        )
    value = ao_proposal.prepare_proposal(case, raw, "rejected-test")
    assert value["report"]["decision"] == "blocked"
    assert value["report"]["blocking_findings"]
    assert value["plan"] is None


def test_preparation_cannot_replace_independent_approval(candidate):
    case, raw = candidate
    value = ao_proposal.prepare_proposal(case, raw, "preparation-test")
    review = {
        "approved": False,
        "blocking_findings": [],
        "summary": "Not approved",
        "reviewed_paths": [value["plan"]["files"][0]["path"]],
    }
    with pytest.raises(AnsibleFilterError, match="Independent plan review"):
        ao_gitops.validate_plan(value["plan"], value["report"], review, REPO)


def test_read_only_preparation_playbook_returns_real_artifact(tmp_path, candidate):
    case, raw = candidate
    project = tmp_path / "project"
    shutil.copytree(
        ROOT / "playbooks/ai",
        project / "playbooks/ai",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (project / "files/ao").mkdir(parents=True)
    shutil.copy(ROOT / "files/ao/harness_case.py", project / "files/ao/harness_case.py")
    extra = tmp_path / "input.json"
    extra.write_text(
        json.dumps(
            {
                "ao_preparation_b64": harness_case.encode_preparation(
                    case, raw, "offline-preparation"
                )["payload_b64"],
            }
        )
    )
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[defaults]\nretry_files_enabled=False\n")
    result = subprocess.run(
        [
            "ansible-playbook",
            "-i",
            "localhost,",
            "playbooks/ai/prepare_ao_proposal.yml",
            "-e",
            "@" + str(extra),
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "ANSIBLE_CONFIG": str(cfg),
            "ANSIBLE_SHOW_CUSTOM_STATS": "true",
            "ANSIBLE_NOCOLOR": "true",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"ao_proposal"' in result.stdout
    assert '"decision": "ready"' in result.stdout
    assert "changed=0" in result.stdout
    assert "kernel_settings_host | default({})" in result.stdout
