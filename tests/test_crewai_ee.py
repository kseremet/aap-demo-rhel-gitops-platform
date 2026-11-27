"""Validate EE/VM selection, credential boundaries and per-job command construction."""

from pathlib import Path
import inspect
import sys

from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    if not any(type(f).__name__ == "_AnsibleCollectionFinder" for f in sys.meta_path):
        init_plugin_loader()
    loader = DataLoader()
    options = {"trusted_as_template": True} if "trusted_as_template" in inspect.signature(loader.load_from_file).parameters else {}
    return loader, loader.load_from_file(str(ROOT / name), **options)


@pytest.mark.parametrize("runtime", ["vm", "ee"])
def test_casc_selects_the_correct_playbooks_credentials_and_public_settings(runtime):
    loader, casc = load("group_vars/all/casc.yml")
    _, ee = load("group_vars/all/crewai_ee.yml")
    values = yaml.safe_load((ROOT / "group_vars/all/demo_variables.yml.example").read_text())
    values.update(demo_ai_runtime=runtime, openshift_vm_namespace="koray",
                  demo_state_scm_url="https://github.com/example/state.git",
                  demo_execution_environment_image="quay.io/example/crewai:test",
                  vault_demo_ssh_private_key="fake-key", vault_aap_token="fake-aap",
                  vault_github_webhook_secret="fake-hook", vault_ai_llm_api_key="fake-model",
                  vault_github_token="fake-github", omit="__omit__")
    templar = Templar(loader=loader, variables={**values, **ee})
    rendered = templar.template(casc)
    templates = {t["name"]: t for t in rendered["controller_templates"]}
    for name, stem in (("JT - AI Incident Investigation", "run_incident_agent"),
                       ("JT - AI PR Review", "run_pr_review_agent")):
        jt = templates[name]
        assert jt["playbook"] == f"playbooks/ai/{stem}{'_ee' if runtime == 'ee' else ''}.yml"
        if runtime == "ee":
            assert jt["credentials"] == [values["demo_ai_api_credential_name"]]
            assert jt["execution_environment"] == values["demo_execution_environment_name"]
            assert jt["extra_vars"]["demo_state_scm_url"] == values["demo_state_scm_url"]
            assert jt["extra_vars"]["openshift_vm_namespace"] == "koray"
            assert not any("token" in k or "api_key" in k for k in jt["extra_vars"])
        else:
            assert jt["credentials"] == [values["demo_machine_credential_name"]]
            assert jt["extra_vars"] == {}
    if runtime == "ee":
        kinds = templar.template(ee["crewai_ee_credential_types"])
        assert kinds[0]["injectors"]["env"]["AI_LLM_API_KEY"] == "{{ ai_llm_api_key }}"
        assert kinds[0]["injectors"]["env"]["GITHUB_TOKEN"] == "{{ github_token }}"
        fallbacks = templar.template(ee["crewai_vm_fallback_templates"])
        assert len(fallbacks) == 2
        assert all("_ee.yml" not in t["playbook"] for t in fallbacks)
        assert all(t["execution_environment"] != values["demo_execution_environment_name"] for t in fallbacks)
        assert all(t["credentials"] == [values["demo_machine_credential_name"]] for t in fallbacks)


@pytest.mark.parametrize("workflow", ["incident", "review", "verify"])
def test_ee_job_uses_argument_arrays_and_always_cleans_its_private_workspace(workflow):
    loader, tasks = load("playbooks/ai/tasks/run_crewai_ee.yml")
    block = next(t for t in tasks if "block" in t)
    command = next(t for t in block["block"] if "ansible.builtin.command" in t)
    prompt = "Inspect web-01; $(touch /tmp/never-run) 'quoted'\nsecond line"
    values = {"_crewai_workflow": workflow, "_crewai_workdir": {"path": "/tmp/job"},
              "ai_incident_prompt": prompt, **command["vars"]}
    result = Templar(loader=loader, variables=values).template(command["ansible.builtin.command"])
    if workflow == "incident":
        assert result["argv"] == ["/opt/crewai/bin/python", "/tmp/job/incident_agent.py", prompt]
    elif workflow == "review":
        assert result["argv"] == ["/opt/crewai/bin/python", "/tmp/job/pr_review_agent.py", "--event-file", "/tmp/job/review-event.json"]
    else:
        assert result["argv"] == ["/opt/crewai/bin/python", "/tmp/job/verify_crewai_ee.py"]
    assert block["always"][0]["ansible.builtin.file"]["state"] == "absent"
    git = next(t for t in block["block"] if "ansible.builtin.git" in t)
    assert "_crewai_workdir.path" in git["ansible.builtin.git"]["dest"]
    assert git["no_log"] is True
    assert command["environment"]["AI_MCP_MODE"] == "container"


def test_build_inputs_exclude_project_secrets_and_isolate_crewai_dependencies():
    directory = ROOT / "context/crewai"
    spec = yaml.safe_load((directory / "execution-environment.yml").read_text())
    assert spec["version"] == 3
    assert "ee-supported-rhel9" in spec["images"]["base_image"]["name"]
    assert spec["dependencies"]["python_interpreter"]["python_path"] == "/usr/bin/python3.12"
    builder_steps = "\n".join(spec["additional_build_steps"]["prepend_builder"])
    assert "python3.12-devel" in builder_steps and "systemd-devel" in builder_steps
    for item in spec["additional_build_files"]:
        assert (directory / item["src"]).is_file()
        assert item["src"] in {"requirements.txt", "sitecustomize.py", "smoke.py"}
    steps = "\n".join(spec["additional_build_steps"]["append_final"])
    assert "-m venv /opt/crewai" in steps
    assert "/opt/crewai/bin/python -m pip install" in steps
    assert "ansible.cfg" not in steps and "vault" not in steps
    for name in ("run_incident_agent_ee.yml", "run_pr_review_agent_ee.yml"):
        _, plays = load("playbooks/ai/" + name)
        assert plays[0]["hosts"] == "localhost"
        assert plays[0]["connection"] == "local"
        assert plays[0]["become"] is False
