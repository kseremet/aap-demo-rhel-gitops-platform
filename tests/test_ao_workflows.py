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
            prompt = nodes["handoff_" + name]["parameters"]["environment"][
                "PROMPT_TEMPLATE"
            ]
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
            variables["ao_linux_tool_ids"],
            variables["ao_readonly_tool_ids"],
        ):
            expected.add(variables["ao_mcp_integration_id"])
        if selected in (
            variables["ao_github_tool_ids"],
            variables["ao_readonly_tool_ids"],
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
@pytest.mark.parametrize(
    "decision,approved,checks,terminal",
    [
        ("ready", True, 0, "publish_proposal"),
        ("ready", True, 1, "publish_proposal"),
        ("ready", True, 4, "publish_proposal"),
        ("no_change", False, 0, "report_no_change"),
        ("blocked", False, 2, "report_blocked"),
        ("ready", False, 1, "report_rejected_plan"),
    ],
)
def test_routing_and_loop_expression_resolution(
    path, decision, approved, checks, terminal
):
    NamespaceResolver, evaluate = ao_runtime()
    workflow = yaml.safe_load(path.read_text())
    nodes = {n["id"]: n for n in workflow["nodes"]}
    outgoing = defaultdict(list)
    for edge in workflow["edges"]:
        outgoing[edge["from"]].append(edge)
    resolver = NamespaceResolver()
    resolver.set_namespace(
        "trigger", {"description": "Investigate web-01", "target_host": "web-01"}
    )
    resolver.set_namespace(
        "workflow_context", {"execution": {"id": "offline-routing-test"}}
    )
    current = outgoing["trigger_eda"][0]["to"]
    loop_results = []
    trace = []
    inside_loop = False
    while True:
        assert (
            len(trace) < 100
        ), "Workflow did not terminate within its diagnostic bound"
        node = nodes[current]
        trace.append(current)
        resolver.set_context(loop_node_id="evidence_loop" if inside_loop else None)
        kind = node["type"]
        port = None
        if kind == "loop":
            params = resolver.resolve_dict(node["parameters"])
            if len(loop_results) < len(params["items"]):
                assert len(loop_results) < params["max_iterations"]
                resolver.set_namespace(
                    current,
                    {"iteration_count": len(loop_results), "iteration_results": None},
                )
                resolver.set_namespace(
                    "loop",
                    {
                        "evidence_loop": {
                            "index": len(loop_results),
                            "item": params["items"][len(loop_results)],
                        }
                    },
                )
                inside_loop, port = True, "iterate"
            else:
                # AO checks the max before asking its activity for completion;
                # leave one spare control turn beyond the four allowed checks.
                assert len(loop_results) < params["max_iterations"]
                resolver.set_namespace(
                    current,
                    {
                        "status": "completed",
                        "iteration_count": len(loop_results),
                        "iteration_results": {"follow_up_check.result": loop_results},
                    },
                )
                inside_loop, port = False, "complete"
        elif kind == "switch":
            port = node["parameters"]["default_port"]
            for case in node["parameters"]["cases"]:
                if evaluate(case["condition"], resolver.get_complete_namespace()):
                    port = case["port"]
                    break
        elif kind == "condition":
            port = (
                "true"
                if evaluate(
                    node["parameters"]["condition"], resolver.get_complete_namespace()
                )
                else "false"
            )
        else:
            params = resolver.resolve_dict(node["parameters"])
            if kind == "script":
                env = params["environment"]
                prepare = runpy.run_path(
                    str(ROOT / "files/ao/prepare_agent_prompt.py")
                )["prepare_prompt"]
                output = prepare(
                    env["PROMPT_TEMPLATE"],
                    [
                        env["HANDOFF_" + str(i)]
                        for i in range(int(env["HANDOFF_COUNT"]))
                    ],
                    env["HANDOFF_STAGE"],
                )
                resolver.set_namespace(current, output)
                current = outgoing[current][0]["to"]
                continue
            if current == "follow_up_check":
                assert f"Check index:\n{len(loop_results)}\n" in params["prompt"]
                assert f"Check {len(loop_results)}" in params["prompt"]
            if current == "validate_evidence":
                resolver.set_namespace(
                    current,
                    {
                        "artifacts": {
                            "ao_evidence_validation": {
                                "valid": decision != "blocked",
                                "blocking_findings": [],
                            }
                        }
                    },
                )
                current = outgoing[current][0]["to"]
                continue
            if kind == "aap_job_template":
                assert current == terminal
                assert params["extra_vars"]["ao_report"]["decision"] == "ready"
                assert params["extra_vars"]["ao_review"]["approved"] is True
                break
            result = example(params["response_schema"])
            if current == "review_evidence":
                result["follow_up"] = [
                    dict(
                        question=f"Check {i}",
                        reason="Missing observation",
                        success_criterion="Source-attributed result",
                    )
                    for i in range(checks)
                ]
            if current == "final_assessment":
                result["decision"] = decision
                result["conclusion_status"] = "confirmed"
            if current == "review_plan":
                result.update(
                    approved=approved,
                    blocking_findings=[] if approved else ["Unproven edit"],
                )
            jsonschema.validate(result, params["response_schema"])
            resolver.set_namespace(
                current,
                {
                    "id": str(uuid4()),
                    "result": {"content": result, "tools_used": ["get_file_contents"]},
                },
            )
            if current == "follow_up_check":
                loop_results.append({"content": deepcopy(result)})
        successors = [
            e
            for e in outgoing[current]
            if e.get("from_port") == port or "from_port" not in e
        ]
        if not successors:
            assert current == terminal
            break
        assert len(successors) == 1
        current = successors[0]["to"]
    assert trace.count("follow_up_check") == checks
    assert ("publish_proposal" in trace) == (terminal == "publish_proposal")


def test_diagnostic_budget_is_enforced_before_the_loop():
    _, evaluate = ao_runtime()
    for path in WORKFLOWS:
        workflow = yaml.safe_load(path.read_text())
        gate = next(n for n in workflow["nodes"] if n["id"] == "limit_checks")
        for count, allowed in [(0, True), (4, True), (5, False)]:
            namespace = {
                "review_evidence": {"result": {"content": {"follow_up": [{}] * count}}}
            }
            assert evaluate(gate["parameters"]["condition"], namespace) is allowed


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_visual_editor_roundtrip_preserves_gate_semantics(path, tmp_path):
    import re
    import shutil
    import subprocess

    _, evaluate = ao_runtime()
    node = shutil.which("node")
    if not node:
        pytest.skip(
            "Optional Node 24+ runtime for AO visual-editor regression is absent"
        )
    major = int(
        subprocess.check_output([node, "--version"], text=True)
        .strip()
        .lstrip("v")
        .split(".")[0]
    )
    if major < 24:
        pytest.skip("Optional visual-editor regression requires Node 24+")
    source = SOURCES.parents[1] / "frontend/packages/syntara-ui/src/utils/expressions"
    workflow = yaml.safe_load(path.read_text())
    gates = {
        n["id"]: n["parameters"]["condition"]
        for n in workflow["nodes"]
        if n["type"] == "condition"
    }
    completed = subprocess.run(
        [
            node,
            str(ROOT / "tests/ao_expression_roundtrip.mjs"),
            str(source),
            str(tmp_path),
        ],
        input=json.dumps(list(gates.values())),
        text=True,
        capture_output=True,
        check=True,
    )
    results = json.loads(completed.stdout)
    for gate, result in zip(gates, results, strict=True):
        assert result["once"] == result["twice"], result
        # Saved templates must contain paths only, never nested function syntax.
        for reference in re.findall(r"\$\{([^}]+)\}", result["twice"]):
            assert "${" not in reference and "(" not in reference, result
        condition = result["twice"]
        if gate == "limit_checks":
            for count in [0, 1, 4, 5, 6]:
                namespace = {
                    "review_evidence": {
                        "result": {"content": {"follow_up": [{}] * count}}
                    }
                }
                assert evaluate(condition, namespace) is (count <= 4)
        elif gate == "approve_plan":
            for approved, blockers in [
                (True, []),
                (False, []),
                (True, ["unproven edit"]),
            ]:
                namespace = {
                    "review_plan": {
                        "result": {
                            "content": {
                                "approved": approved,
                                "blocking_findings": blockers,
                            }
                        }
                    }
                }
                assert evaluate(condition, namespace) is (approved and not blockers)


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
    follow_up = next(n for n in broken["nodes"] if n["id"] == "follow_up_check")
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


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_missing_tool_execution_stops_each_collector(path):
    _, evaluate = ao_runtime()
    workflow = yaml.safe_load(path.read_text())
    nodes = {n["id"]: n for n in workflow["nodes"]}
    for stage in ["scope_request", "investigate_host", "map_repository"]:
        condition = nodes[stage + "_tools"]["parameters"]["condition"]
        for used, allowed in [([], False), (None, False), (["read_file"], True)]:
            assert (
                evaluate(condition, {stage: {"result": {"tools_used": used}}})
                is allowed
            )
        target = next(
            e["to"]
            for e in workflow["edges"]
            if e["from"] == stage + "_tools" and e["from_port"] == "false"
        )
        assert target == "handoff_report_" + stage + "_tools"
        reporter = next(e["to"] for e in workflow["edges"] if e["from"] == target)
        assert nodes[reporter]["parameters"]["tool_selection_strategy"] == "NONE"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
@pytest.mark.parametrize("decision", ["ready", "no_change"])
def test_neither_success_route_can_bypass_evidence_validation(path, decision):
    _, evaluate = ao_runtime()
    nodes = {n["id"]: n for n in yaml.safe_load(path.read_text())["nodes"]}
    case = next(
        c
        for c in nodes["route_assessment"]["parameters"]["cases"]
        if c["port"] == decision
    )
    for verified, certainty, blockers, allowed in [
        (True, "confirmed", [], True),
        (False, "confirmed", [], False),
        (True, "hypothesis", [], False),
        (True, "confirmed", ["Unresolved cause"], False),
    ]:
        ns = {
            "validate_evidence": {
                "artifacts": {"ao_evidence_validation": {"valid": verified}}
            },
            "final_assessment": {
                "result": {
                    "content": {
                        "decision": decision,
                        "conclusion_status": certainty,
                        "blocking_findings": blockers,
                    }
                }
            },
        }
        assert evaluate(case["condition"], ns) is allowed
