"""Small shared harness: immutable evidence registry and proposal packaging."""

import copy
import hashlib
import json
import re
from ao_evidence import successful_results, REPO_READ_TOOLS, COMMIT_TOOLS


def require(condition, message):
    if not condition:
        raise ValueError(message)


def evidence_registry(packet, context, host):
    entries = {}
    for record in successful_results(packet):
        args = record["input"]
        tool = record["tool"]
        if (
            tool in REPO_READ_TOOLS
            and args.get("owner") == context["owner"]
            and args.get("repo") == context["repository"]
            and any(args.get(k) == context["read_commit"] for k in ("sha", "ref"))
        ):
            source = f"repo://{context['owner']}/{context['repository']}@{context['read_commit']}/{args['path']}"
        elif args.get("host") == host:
            source = "host:" + host + (":" + args["path"] if args.get("path") else "")
        elif tool in COMMIT_TOOLS:
            source = f"repo://{context['owner']}/{context['repository']}@{context['read_commit']}"
        else:
            continue
        raw = record["output"]
        excerpt = raw
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
                excerpt = max(parsed, key=len, default=raw)
        except ValueError:
            pass
        identifier = (
            "ev-"
            + hashlib.sha256(
                json.dumps([tool, args, raw], sort_keys=True).encode()
            ).hexdigest()[:16]
        )
        entries[identifier] = {
            "id": identifier,
            "tool": tool,
            "source": source,
            "observation": "Successful read-only result: " + tool + " from " + source,
            "tool_output_excerpt": excerpt[:8192],
        }
    return entries


def prepare_case(collection, investigation):
    require(
        isinstance(collection, dict) and collection.get("snapshot_complete") is True,
        "Complete pinned repository/host collection is required",
    )
    packet = copy.deepcopy(collection["packet"])
    packet["investigation"] = investigation
    context = collection["repository_context"]
    host = collection["target_host"]
    registry = evidence_registry(packet, context, host)
    result = investigation.get("result", {}).get("content", {})
    return {
        "collection": collection,
        "packet": packet,
        "registry": registry,
        "view": {
            "request": collection["request"],
            "repository_context": context,
            "target_host": host,
            "repository_files": collection["repository_files"],
            "host_facts": collection["host_facts"],
            "inventory_groups": collection["inventory_groups"],
            "investigation": result,
            "evidence_registry": registry,
        },
    }


def compile_proposal(case, proposal, execution_id, branch_prefix="aiops/"):
    require(isinstance(proposal, dict), "Proposal must be an object")
    report = copy.deepcopy(proposal["report"])
    collection = case["collection"]
    context = collection["repository_context"]
    selected = report.pop("evidence_ids", [])
    require(
        isinstance(selected, list)
        and len(selected) == len(set(selected))
        and all(x in case["registry"] for x in selected),
        "Proposal may select only unique measured evidence IDs",
    )
    report.update(
        incident=collection["request"].get("incident_id")
        or collection["request"].get("summary")
        or "Fleet incident",
        target_host=collection["target_host"],
        repository_context=context,
        evidence=[copy.deepcopy(case["registry"][key]) for key in selected],
        resolved_findings=[],
    )
    basis = report.get("proposal_basis")
    if isinstance(basis, dict):
        basis["requires_maintainer_approval"] = True
        basis["evidence_ids"] = selected
    gaps = case["view"].get("investigation", {}).get("critical_gaps", [])
    if gaps:
        report["blocking_findings"] = list(
            dict.fromkeys(report.get("blocking_findings", []) + gaps)
        )
        report["decision"] = "blocked"
    raw = proposal.get("plan")
    plan = None
    if report.get("decision") == "ready":
        require(isinstance(raw, dict), "Ready requires a complete plan")
        plan = copy.deepcopy(raw)
        update = collection["mode"] == "pr_review"
        plan.update(
            operation="update_pr" if update else "create_pr",
            owner=context["owner"],
            repository=context["repository"],
            base_branch=context["base_branch"],
            read_commit=context["read_commit"],
            pull_request_number=context["pull_request_number"],
            branch=(
                context["head_branch"]
                if update
                else branch_prefix
                + "proposal-"
                + re.sub("[^A-Za-z0-9_-]", "", str(execution_id))[:40]
            ),
        )
        require(
            plan["branch"] and plan["branch"] != plan["base_branch"],
            "Proposal must retain a separate PR branch",
        )
        for file in plan.get("files", []):
            path = file["path"]
            existing = collection["repository_files"].get(path)
            require(
                existing
                or (
                    path not in collection["manifest_paths"]
                    and path.startswith("host_vars/" + collection["target_host"] + "/")
                ),
                "Edit requires a fully collected source file or absent target-host overlay",
            )
            file["expected_sha"] = existing["sha"] if existing else None
            require(
                file.get("evidence_ids") and set(file["evidence_ids"]) <= set(selected),
                "Each file must cite selected verified evidence",
            )
    return {"report": report, "plan": plan, "packet": case["packet"]}


def terminal_result(proposal, review, validation):
    report = proposal["report"]
    verdict = validation.get("ao_evidence_validation", {})
    findings = list(
        dict.fromkeys(
            report.get("blocking_findings", [])
            + review.get("blocking_findings", [])
            + verdict.get("blocking_findings", [])
        )
    )
    no_change = (
        verdict.get("valid") is True
        and report.get("decision") == "no_change"
        and not findings
    )
    if not no_change and review.get("approved") is not True and not findings:
        findings.append(
            "Independent review did not approve publication: "
            + review.get("summary", "No approval explanation returned.")
        )
    return {
        "outcome": "no_change" if no_change else "blocked",
        "summary": report.get("recommendation", "Review the collected evidence."),
        "blocking_findings": findings,
        "next_steps": report.get("validation_plan", []),
    }
