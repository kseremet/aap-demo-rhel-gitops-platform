"""Read-only proposal preparation; the writer still repeats every publication gate."""

import base64
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import yaml
from ansible.errors import AnsibleFilterError
from ansible.utils.unsafe_proxy import wrap_var


def load_helper(name, path):
    # Only fixed repository-owned modules are loaded; no model-supplied paths.
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_proposal(case, proposal, execution_id):
    root = Path(__file__).resolve().parents[3]
    harness = load_helper("ao_harness_preparation", root / "files/ao/harness_case.py")
    writer = load_helper(
        "ao_writer_preparation", Path(__file__).with_name("ao_gitops.py")
    )
    try:
        prepared = harness.compile_proposal(case, proposal, execution_id)
        if prepared["report"]["decision"] != "ready":
            return prepared
        files = case["collection"]["repository_files"]

        def response(source):
            return {
                "status": 200,
                "json": {
                    "type": "file",
                    "sha": source["sha"],
                    "encoding": "base64",
                    "content": base64.b64encode(source["content"].encode()).decode(),
                },
            }

        allowed = writer.consumed_variables(response(files["tasks/prepare_vars.yml"]))
        for item in prepared["plan"]["files"]:
            source = files.get(item["path"])
            old = (
                writer.load_variables(source["content"])
                if source and yaml.safe_load(source["content"]) is not None
                else {}
            )
            new = writer.load_variables(item["content"])
            delta = writer.differences(old, new)
            item["setting_changes"] = [
                {"path": list(path), "before": before, "after": after}
                for path, (before, after) in delta.items()
            ]
            current = response(source) if source else {"status": 404, "json": {}}
            writer.validate_file(
                item, current, allowed, prepared["report"], prepared["packet"]
            )
        # This checks only structure and exact changes, never fabricates approval.
        # The subsequent agent and final writer require real independent review.
        writer.validate_plan_contract(
            prepared["plan"], prepared["report"], case["collection"]["repository_url"]
        )
        return prepared
    except (ValueError, TypeError, KeyError, AnsibleFilterError) as exc:
        report = deepcopy(proposal.get("report", {}))
        report.update(
            decision="blocked",
            target_host=case["collection"]["target_host"],
            repository_context=case["collection"]["repository_context"],
            evidence=[],
            resolved_findings=[],
        )
        report["blocking_findings"] = list(report.get("blocking_findings") or []) + [
            "Proposal preparation rejected the candidate: " + str(exc)
        ]
        return {"report": report, "plan": None, "packet": case["packet"]}


def decode_payload(value):
    if not isinstance(value, str) or len(value) > 2 * 1024 * 1024:
        raise AnsibleFilterError("Expected a bounded encoded AO payload")
    result = json.loads(base64.b64decode(value, validate=True).decode("utf-8"))
    if not isinstance(result, dict):
        raise AnsibleFilterError("AO payload must decode to an object")
    return wrap_var(result)


def prepare_encoded_proposal(value):
    payload = decode_payload(value)
    return wrap_var(
        prepare_proposal(payload["case"], payload["proposal"], payload["execution_id"])
    )


class FilterModule:
    def filters(self):
        return {
            "ao_prepare_proposal": prepare_proposal,
            "ao_prepare_encoded_proposal": prepare_encoded_proposal,
            "ao_decode_payload": decode_payload,
        }
