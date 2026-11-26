"""Check workflow contracts and dry-run routing with AO's real expression resolver.

The routing checks use canned agent responses, not live LLM calls. The optional
AO source checks run when this artifact is inside the workbench checkout.
"""

from collections import defaultdict
from copy import deepcopy
from graphlib import TopologicalSorter
import inspect
import runpy
import json
from pathlib import Path
import sys
from uuid import uuid4

import jsonschema
import pytest
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT.parents[2] / "references/syntara/backend/src"
WORKFLOWS = list((ROOT / "workflows/automation-orchestrator").glob("*.yaml"))


@pytest.fixture(scope="module", autouse=True)
def initialize_ansible_plugins():
    if not any(type(f).__name__ == "_AnsibleCollectionFinder" for f in sys.meta_path):
        init_plugin_loader()


def example(spec):
    if "enum" in spec:
        return spec["enum"][0]
    kind = spec.get("type", "null")
    if isinstance(kind, list):
        return None if "null" in kind else example({**spec, "type": kind[0]})
    if kind == "object":
        return {k: example(spec["properties"][k]) for k in spec.get("required", [])}
    if kind == "array":
        return [example(spec["items"]) for _ in range(spec.get("minItems", 0))]
    return {"string": "unknown", "integer": 1, "boolean": False, "null": None}[kind]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_schema_contracts_and_graph(path):
    workflow = yaml.safe_load(path.read_text())
    nodes = {n["id"]: n for n in workflow["nodes"]}
    graph = {n: set() for n in nodes}
    graph["trigger_eda"] = set()
    destinations = defaultdict(list)
    for edge in workflow["edges"]:
        assert edge["from"] in graph and edge["to"] in graph
        if edge.get("to_port") != "iterate":
            graph[edge["to"]].add(edge["from"])
        destinations[edge["from"]].append(edge["to"])
    assert len(list(TopologicalSorter(graph).static_order())) == len(graph)
    for name, node in nodes.items():
        if node["type"] in ("switch", "condition"):
            # AO marks non-taken destinations skipped. Sharing destinations
            # directly across two ports would skip the taken branch too.
            assert len(destinations[name]) == len(set(destinations[name]))
        if node["type"] == "agentic":
            schema = node["parameters"]["response_schema"]
            jsonschema.Draft7Validator.check_schema(schema)
            jsonschema.validate(example(schema), schema)
            prompt = nodes["input_" + name]["parameters"]["body"]["template"]
            assert json.dumps(schema, separators=(",", ":")) not in prompt
            assert (
                "Response contract (also enforced as the response schema):"
                not in prompt
            )
            assert "Final handoff:" in prompt
            assert "configured response schema" in prompt
            assert node["parameters"]["llm_model_id"] == "__AO_LLM_MODEL_ID__"
            assert node["settings"]["continue_on_failure"] is False
    assert nodes["publish_proposal"]["settings"]["retry_policy"]["max_retries"] == 0
    assert set(nodes["publish_proposal"]["parameters"]["extra_vars"]) == {
        "ao_plan",
        "ao_report",
        "ao_review",
        "ao_execution_id",
        "ao_evidence",
        "ao_evidence_review",
    }


@pytest.mark.skipif(
    not SOURCES.exists(), reason="Optional workbench AO source checkout is absent"
)
@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_against_ao_workflow_schemas(path):
    schema_dir = SOURCES / "syntara/schemas/workflows/v2"
    resources = []
    for file in schema_dir.rglob("*.json"):
        value = json.loads(file.read_text())
        resource = Resource.from_contents(value, default_specification=DRAFT202012)
        resources.append(
            (
                "https://automation.example.com/schemas/workflows/v2/"
                + file.relative_to(schema_dir).as_posix(),
                resource,
            )
        )
        if "$id" in value:
            resources.append((value["$id"], resource))
    validator = jsonschema.Draft202012Validator(
        json.loads((schema_dir / "workflow_definition.schema.json").read_text()),
        registry=Registry().with_resources(resources),
    )
    # Validate the actual importer's from_yaml and credential/trigger injection,
    # since the version-controlled trigger intentionally has no real UUIDs.
    loader = DataLoader()
    options = (
        {"trusted_as_template": True}
        if "trusted_as_template" in inspect.signature(loader.load_from_file).parameters
        else {}
    )
    tasks = loader.load_from_file(
        str(ROOT / "playbooks/ao_import_workflow.yml"), **options
    )
    variables = {
        key: str(uuid4())
        for key in [
            "ao_llm_model_id",
            "ao_llm_credential_id",
            "ao_aap_integration_id",
            "ao_aap_credential_id",
            "ao_mcp_integration_id",
            "ao_mcp_credential_id",
            "ao_github_mcp_integration_id",
            "ao_github_mcp_credential_id",
            "ao_eda_service_account_id",
        ]
    }
    variables.update(
        ao_linux_tool_ids=[str(uuid4())],
        ao_github_tool_ids=[str(uuid4())],
        ao_readonly_tool_ids=[str(uuid4())],
        ao_handoff_tool_ids=[str(uuid4())],
    )
    variables["ao_workflow"] = {"path": str(path), "webhook_path": path.stem}
    for task in tasks[:4]:
        variables.update(
            Templar(loader=loader, variables=variables).template(
                task["ansible.builtin.set_fact"]
            )
        )
    validator.validate(variables["_ao_workflow_definition"])
    for node in variables["_ao_workflow_definition"]["nodes"]:
        params = node.get("parameters", {})
        if params.get("tool_selection_strategy") != "SELECTED":
            continue
        selected = params["tool_selections"]
        expected = set()
        if selected in (
            variables["ao_linux_tool_ids"] + variables["ao_handoff_tool_ids"],
            variables["ao_readonly_tool_ids"] + variables["ao_handoff_tool_ids"],
        ):
            expected.add(variables["ao_mcp_integration_id"])
        if selected in (
            variables["ao_github_tool_ids"] + variables["ao_handoff_tool_ids"],
            variables["ao_readonly_tool_ids"] + variables["ao_handoff_tool_ids"],
            variables["ao_linux_tool_ids"] + variables["ao_handoff_tool_ids"],
            variables["ao_handoff_tool_ids"],
        ):
            expected.add(variables["ao_github_mcp_integration_id"])
        assert {
            c["integration_id"] for c in params["integration_connections"]
        } == expected
        for connection in params["integration_connections"]:
            expected_credential = (
                variables["ao_mcp_credential_id"]
                if connection["integration_id"] == variables["ao_mcp_integration_id"]
                else variables["ao_github_mcp_credential_id"]
            )
            assert connection["credential_id"] == expected_credential


def ao_runtime():
    if not SOURCES.exists():
        pytest.skip("Optional workbench AO source checkout is absent")
    sys.path.insert(0, str(SOURCES))
    from syntara.workflows.utils.namespace_resolver import NamespaceResolver
    from syntara.workflows.workflow_engine.unified_eval import safe_eval_with_namespace

    return NamespaceResolver, safe_eval_with_namespace


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_three_roles_have_no_loop_or_hidden_reporter(path):
    w = yaml.safe_load(path.read_text())
    nodes = {n["id"]: n for n in w["nodes"]}
    assert [n["id"] for n in w["nodes"] if n["type"] == "agentic"] == [
        "investigate",
        "propose",
        "review",
    ]
    assert len(nodes) == 14
    assert not any(n["type"] in ("loop", "script") for n in nodes.values())
    assert (
        nodes["collect_evidence"]["parameters"]["job_template_name"]
        == "JT - AO Collect Evidence"
    )
    assert nodes["propose"]["parameters"]["tool_selections"] == [
        "__AO_HANDOFF_TOOL_IDS__"
    ]
    for role in ("investigate", "propose", "review"):
        assert (
            nodes[role]["parameters"]["prompt"]
            == "${input_" + role + ".result.body.prompt}"
        )
        prompt = nodes["input_" + role]["parameters"]["body"]["template"]
        assert "maintainer" in prompt.lower()
    assert (
        nodes["validate_evidence"]["parameters"]["extra_vars"]["ao_evidence"]
        == "${compile_proposal.result.body.packet}"
    )
    assert (
        nodes["publish_proposal"]["parameters"]["extra_vars"]["ao_evidence"]
        == "${compile_proposal.result.body.packet}"
    )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
@pytest.mark.parametrize(
    "decision,valid,approved,blockers,port",
    [
        ("ready", True, True, [], "case_0"),
        ("ready", False, True, [], "default"),
        ("ready", True, False, [], "default"),
        ("ready", True, True, ["missing capacity"], "default"),
        ("blocked", False, True, [], "default"),
        ("no_change", True, False, [], "case_1"),
        ("no_change", False, False, [], "default"),
        ("no_change", True, False, ["unproven diagnosis"], "default"),
    ],
)
def test_real_ao_publication_routing(path, decision, valid, approved, blockers, port):
    _, evaluate = ao_runtime()
    w = yaml.safe_load(path.read_text())
    gate = next(n for n in w["nodes"] if n["id"] == "route_assessment")["parameters"]
    namespace = {
        "validate_evidence": {
            "artifacts": {"ao_evidence_validation": {"valid": valid}}
        },
        "compile_proposal": {"result": {"body": {"report": {"decision": decision}}}},
        "review": {
            "result": {"content": {"approved": approved, "blocking_findings": blockers}}
        },
    }
    matched = next(
        (c["port"] for c in gate["cases"] if evaluate(c["condition"], namespace)),
        gate["default_port"],
    )
    assert matched == port


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_visual_editor_roundtrip_preserves_gate_semantics(path, tmp_path):
    import shutil, subprocess

    node = shutil.which("node")
    if not node or not SOURCES.exists():
        pytest.skip("Node and AO source required")
    _, evaluate = ao_runtime()
    source = SOURCES.parents[1] / "frontend/packages/syntara-ui/src/utils/expressions"
    w = yaml.safe_load(path.read_text())
    cases = next(n for n in w["nodes"] if n["id"] == "route_assessment")["parameters"][
        "cases"
    ]
    output = subprocess.run(
        [
            node,
            str(ROOT / "tests/ao_expression_roundtrip.mjs"),
            str(source),
            str(tmp_path),
        ],
        input=json.dumps([c["condition"] for c in cases]),
        text=True,
        capture_output=True,
        check=True,
    )
    for case, result in zip(cases, json.loads(output.stdout), strict=True):
        assert result["once"] == result["twice"]
        for decision in ("ready", "no_change", "blocked"):
            for valid in (True, False):
                for approved in (True, False):
                    for blockers in ([], ["unproven"]):
                        ns = {
                            "validate_evidence": {
                                "artifacts": {
                                    "ao_evidence_validation": {"valid": valid}
                                }
                            },
                            "compile_proposal": {
                                "result": {"body": {"report": {"decision": decision}}}
                            },
                            "review": {
                                "result": {
                                    "content": {
                                        "approved": approved,
                                        "blocking_findings": blockers,
                                    }
                                }
                            },
                        }
                        assert evaluate(case["condition"], ns) == evaluate(
                            result["twice"], ns
                        )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_frontend_accepts_all_workflow_variable_references(path, tmp_path):
    import shutil
    import subprocess

    if not SOURCES.exists():
        pytest.skip("Optional workbench AO source checkout is absent")
    node = shutil.which("node")
    if not node:
        pytest.skip("Optional Node 24+ runtime is absent")
    major = int(
        subprocess.check_output([node, "--version"], text=True)
        .strip()
        .lstrip("v")
        .split(".")[0]
    )
    if major < 24:
        pytest.skip("Optional frontend validation check requires Node 24+")
    source = (
        SOURCES.parents[1] / "frontend/packages/syntara-ui/src/routes/builder/utils"
    )
    workflow = yaml.safe_load(path.read_text())
    broken = deepcopy(workflow)
    follow_up = next(n for n in broken["nodes"] if n["id"] == "investigate")
    follow_up["parameters"]["prompt"] += "\n${loop.index}\n${loop.item}"
    completed = subprocess.run(
        [
            node,
            str(ROOT / "tests/ao_variable_references.mjs"),
            str(source),
            str(tmp_path),
        ],
        input=json.dumps([workflow, broken]),
        text=True,
        capture_output=True,
        check=True,
    )
    valid_errors, regression_errors = json.loads(completed.stdout)
    assert valid_errors == []
    assert any(
        'node or trigger "loop" does not exist' in error["message"]
        for error in regression_errors
    )


def test_visual_editor_preserves_all_switch_branch_ports(tmp_path):
    import shutil, subprocess

    node = shutil.which("node")
    if not node or not SOURCES.exists():
        pytest.skip("Node and optional AO source required")
    source = (
        SOURCES.parents[1] / "frontend/packages/syntara-ui/src/routes/builder/utils"
    )
    definitions = [yaml.safe_load(p.read_text()) for p in WORKFLOWS]
    completed = subprocess.run(
        [node, str(ROOT / "tests/ao_edge_roundtrip.mjs"), str(source), str(tmp_path)],
        input=json.dumps(definitions),
        text=True,
        capture_output=True,
        check=True,
    )
    for definition, result in zip(definitions, json.loads(completed.stdout)):
        for before, after in zip(definition["edges"], result["edges"]):
            if before["from"] == "route_assessment":
                assert before == after
        assert result["invalidNamedPorts"] == [None, None, None]
