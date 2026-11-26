"""Check workflow contracts and dry-run routing with AO's real expression resolver.

The routing checks use canned agent responses, not live LLM calls. The optional
AO source checks run when this artifact is inside the workbench checkout.
"""

from collections import defaultdict
from copy import deepcopy
from graphlib import TopologicalSorter
import inspect
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
            assert (
                json.dumps(schema, separators=(",", ":"))
                in node["parameters"]["prompt"]
            )
            assert node["parameters"]["llm_model_id"] == "__AO_LLM_MODEL_ID__"
            assert node["settings"]["continue_on_failure"] is False
    assert nodes["publish_proposal"]["settings"]["retry_policy"]["max_retries"] == 0
    assert set(nodes["publish_proposal"]["parameters"]["extra_vars"]) == {
        "ao_plan",
        "ao_report",
        "ao_review",
        "ao_execution_id",
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
            "ao_github_mcp_integration_id",
            "ao_github_mcp_credential_id",
            "ao_eda_service_account_id",
        ]
    }
    variables["ao_workflow"] = {"path": str(path), "webhook_path": path.stem}
    for task in tasks[:4]:
        variables.update(
            Templar(loader=loader, variables=variables).template(
                task["ansible.builtin.set_fact"]
            )
        )
    validator.validate(variables["_ao_workflow_definition"])


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
        assert len(trace) < 50, "Workflow did not terminate within its diagnostic bound"
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
            if current == "review_plan":
                result.update(
                    approved=approved,
                    blocking_findings=[] if approved else ["Unproven edit"],
                )
            jsonschema.validate(result, params["response_schema"])
            resolver.set_namespace(current, {"result": {"content": result}})
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
