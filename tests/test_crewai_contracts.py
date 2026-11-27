"""Offline checks for the restored CrewAI event path and shared inventory refresh."""

from copy import deepcopy
from pathlib import Path
import inspect
import sys

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def plugins():
    if not any(type(f).__name__ == "_AnsibleCollectionFinder" for f in sys.meta_path):
        init_plugin_loader()


def load(relative):
    loader = DataLoader()
    options = (
        {"trusted_as_template": True}
        if "trusted_as_template" in inspect.signature(loader.load_from_file).parameters
        else {}
    )
    return loader, loader.load_from_file(str(ROOT / relative), **options)


@pytest.mark.parametrize(
    "index,expected",
    [
        (0, "JT - AI Incident Investigation"),
        (1, "JT - AI PR Review"),
        (2, "JT - AI PR Review"),
        (3, "JT - AI PR Review"),
    ],
)
def test_issue_and_feedback_payloads_reach_the_correct_crewai_job(index, expected):
    loader, rulebooks = load("rulebooks/github_incidents.yml")
    event = {
        "action": "opened" if index == 0 else "created",
        "issue": {
            "number": 71,
            "title": "web-01 kernel drift",
            "body": "Investigate the running value.",
            "html_url": "https://github.com/example/state/issues/71",
            "pull_request": {"url": "test"},
        },
        "pull_request": {"number": 5, "head": {"ref": "aiops/test"}},
        "comment": {"body": "Please narrow the change to web-01."},
        "review": {"state": "changes_requested"},
    }
    rule = rulebooks[0]["rules"][index]
    action = Templar(
        loader=loader,
        variables={"event": {"payload": event}, "github_aap_organization": "Demo"},
    ).template(rule["action"]["run_job_template"])
    assert action["name"] == expected
    extra = action["job_args"]["extra_vars"]
    assert set(extra) == ({"ai_incident_prompt"} if index == 0 else {"pr_review_event"})
    if index == 0:
        assert "web-01 kernel drift" in extra["ai_incident_prompt"]
        assert "issues/71" in extra["ai_incident_prompt"]
        assert "Do not modify the host directly." in extra["ai_incident_prompt"]
    else:
        assert extra["pr_review_event"] == event


def test_feedback_filters_keep_approval_and_agent_comment_loop_guards():
    _, rules = load("rulebooks/github_incidents.yml")
    assert 'review.state != "approved"' in rules[0]["rules"][1]["condition"]
    for index in (2, 3):
        assert (
            'not regex("aiops-agent-review")' in rules[0]["rules"][index]["condition"]
        )
    assert "aiops/.*" in rules[0]["rules"][1]["condition"]


def test_casc_configures_only_the_crewai_path_and_retains_shared_objects():
    loader, casc = load("group_vars/all/casc.yml")
    values = yaml.safe_load(
        (ROOT / "group_vars/all/demo_variables.yml.example").read_text()
    )
    values.update(
        vault_demo_ssh_private_key="fake-key",
        vault_aap_token="fake-token",
        vault_github_webhook_secret="fake-webhook",
        demo_ssh_private_key_path="unused",
    )
    rendered = Templar(loader=loader, variables=values).template(casc)
    templates = {t["name"]: t for t in rendered["controller_templates"]}
    assert {
        "JT - AI Incident Investigation",
        "JT - AI PR Review",
        "JT - RHEL GitOps Reconciliation",
    } <= set(templates)
    assert not any("AO " in name or "Text Adapter" in name for name in templates)
    assert "controller_workflows" not in rendered
    assert [c["credential_type"] for c in rendered["controller_credentials"]] == [
        "Machine"
    ]
    assert (
        templates["JT - AI Incident Investigation"]["inventory"]
        == values["demo_ai_inventory_name"]
    )
    assert templates["JT - AI PR Review"]["ask_variables_on_launch"] is True
    extra = rendered["eda_rulebook_activations"][0]["extra_vars"]
    assert set(extra) == {
        "github_webhook_port",
        "github_webhook_secret",
        "github_incident_label",
        "github_aap_organization",
    }
    _, entry = load("playbooks/casc.yml")
    assert [p["ansible.builtin.import_playbook"] for p in entry] == ["aap_config.yml"]


@pytest.mark.parametrize("host", ["ai-01", "web-01"])
def test_live_address_refresh_preserves_inventory_and_ssh_configuration(host):
    loader, tasks = load("playbooks/refresh_inventory_addresses.yml")
    task = next(
        t
        for t in tasks
        if t["name"]
        == "Refresh Controller addresses without rewriting local generated files"
    )
    before = [
        {
            "name": host,
            "inventory": "AI Control" if host == "ai-01" else "RHEL Fleet",
            "variables": {
                "ansible_host": "old",
                "ansible_user": "cloud-user",
                "ansible_become": True,
                "ansible_ssh_common_args": "preserve-me",
            },
            "state": "present",
        },
        {"name": "external", "variables": {"ansible_host": "keep"}},
    ]
    original = deepcopy(before)
    item = {
        "item": {"key": host},
        "resources": [
            {"status": {"interfaces": [{"name": "default", "ipAddress": "10.0.0.25"}]}}
        ],
    }
    after = Templar(
        loader=loader,
        variables={"controller_hosts": before, "item": item, **task["vars"]},
    ).template(task["ansible.builtin.set_fact"])["controller_hosts"]
    expected = deepcopy(original[0])
    expected["variables"]["ansible_host"] = "10.0.0.25"
    assert after == [original[1], expected]
    assert before == original


def test_mcp_refresh_preserves_identity_and_unrelated_aliases():
    loader, tasks = load("playbooks/refresh_inventory_addresses.yml")
    task = next(
        t
        for t in tasks
        if t["name"]
        == "Refresh existing fleet aliases using the same management addresses"
    )
    old = "Host web-01\n  HostName 10.1.1.1\n  User cloud-user\n  IdentityFile /var/lib/mcp/.ssh/id_ed25519\nHost external\n  HostName 10.2.2.2\n"
    item = {
        "item": {"key": "web-01"},
        "resources": [
            {"status": {"interfaces": [{"name": "default", "ipAddress": "10.0.0.25"}]}}
        ],
    }
    after = Templar(
        loader=loader,
        variables={"_inventory_ssh_config": old, "item": item, **task["vars"]},
    ).template(task["ansible.builtin.set_fact"])["_inventory_ssh_config"]
    assert after == old.replace("10.1.1.1", "10.0.0.25")
    update = next(
        t
        for t in tasks
        if t["name"]
        == "Update only changed SSH aliases in the existing Linux MCP ConfigMap"
    )
    assert set(update["kubernetes.core.k8s"]["definition"]["data"]) == {"config"}


@pytest.mark.parametrize("name", ["incident_agent.py.j2", "pr_review_agent.py.j2"])
def test_existing_agent_templates_still_compile(name):
    loader = DataLoader()
    text = (ROOT / "roles/ai_control/templates" / name).read_text()
    try:
        from ansible.template import trust_as_template
    except ImportError:  # Ansible 2.18 predates template trust tagging.
        pass
    else:
        text = trust_as_template(text)
    rendered = Templar(
        loader=loader,
        variables={
            "ai_control_venv_path": "/home/cloud-user/.venv",
            "ai_control_app_path": "/home/cloud-user/agentic-aiops",
        },
    ).template(text)
    compile(rendered, name, "exec")
