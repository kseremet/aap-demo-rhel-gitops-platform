"""Validate claims against runtime tool results, without network or host access.

Agent-written citations and tools_used alone do not establish provenance. Match
exact excerpts or typed JSON fields to successful call/result pairs emitted by
AO's trace recorder.
"""

import hashlib
import copy
import json
import re
from urllib.parse import urlparse

LINUX_TOOLS = {
    "get_system_information",
    "read_file",
    "list_files",
    "list_directories",
    "get_service_status",
    "get_service_logs",
    "get_journal_logs",
    "read_log_file",
    "list_services",
    "get_process_info",
    "list_processes",
    "get_listening_ports",
    "get_network_connections",
    "get_network_interfaces",
    "get_disk_usage",
    "get_memory_information",
    "get_cpu_information",
    "get_hardware_information",
}


def successful_results(packet):
    """Walk loop aggregates as well as ordinary AO invocation wrappers."""
    results = []
    seen = set()

    def walk(value):
        if isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, dict):
            result = value if "agent_trace" in value else value.get("result")
            if isinstance(result, dict) and isinstance(result.get("agent_trace"), dict):
                # AO loop aggregates can retain only the .result namespace,
                # without the outer invocation ID. Deduplicate those runtime
                # traces by content; never descend into agent-written content.
                invocation = (
                    value.get("id")
                    or "trace:"
                    + hashlib.sha256(
                        json.dumps(result["agent_trace"], sort_keys=True).encode()
                    ).hexdigest()
                )
                if not isinstance(invocation, str) or invocation in seen:
                    return
                seen.add(invocation)
                calls = {}
                for step in result["agent_trace"].get("steps", []):
                    if not isinstance(step, dict):
                        continue
                    # A packet transports upstream claims. Reading it does not
                    # independently measure a host or verify repository facts.
                    if (
                        str(step.get("tool_name", "")).removeprefix("functions.")
                        == "read_handoff"
                    ):
                        continue
                    key = (step.get("call_id"), step.get("tool_name"))
                    if step.get("type") == "tool_call" and key[0]:
                        calls[key] = step.get("tool_input") or {}
                    elif (
                        step.get("type") == "tool_result"
                        and step.get("status") == "success"
                        and key in calls
                    ):
                        output = step.get("tool_output")
                        if not isinstance(output, str) or not output.strip():
                            continue
                        # MCP failures can be encoded inside a successful transport response.
                        if re.search(r'"isError"\s*:\s*true', output):
                            continue
                        if re.match(
                            r"\s*(TimeoutError|Error executing tool|Traceback|Permission denied)",
                            output,
                        ):
                            continue
                        try:
                            parsed = json.loads(output)
                        except (ValueError, TypeError):
                            parsed = None
                        if isinstance(parsed, dict) and (
                            parsed.get("error") or parsed.get("isError")
                        ):
                            continue
                        results.append(
                            {
                                "invocation_id": invocation,
                                "tool": key[1],
                                "input": calls[key],
                                "output": output,
                            }
                        )
                return
            for child in value.values():
                walk(child)

    walk(packet)
    return results


def matches_output(excerpt, output):
    """Accept exact text/scalars or a typed subset of a structured JSON result.

    Models can return selected JSON fields with different spacing or a closing
    brace. Compare those fields structurally rather than accepting fuzzy prose.
    """
    if (
        not isinstance(output, str)
        or not isinstance(excerpt, str)
        or not excerpt.strip()
    ):
        return False
    # AO serializes multi-block MCP text as a JSON array. Decode only the
    # transport's text elements, never recursively search metadata or join blocks.
    try:
        transported = json.loads(output)
    except (ValueError, TypeError):
        transported = None
    if (
        isinstance(transported, list)
        and transported
        and (
            all(isinstance(block, str) for block in transported)
            or any(
                isinstance(block, dict)
                and block.get("type")
                in {"text", "image", "audio", "resource", "resource_link"}
                for block in transported
            )
        )
    ):
        texts = []
        for block in transported:
            if isinstance(block, str):
                texts.append(block)
            elif (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                texts.append(block["text"])
        return any(matches_output(excerpt, text) for text in texts if text != output)
    if (
        len(excerpt.strip()) >= 8 and excerpt in output
    ) or excerpt.strip() == output.strip():
        return True
    try:
        claimed, actual = json.loads(excerpt), json.loads(output)
    except (ValueError, TypeError):
        return False

    def subset(left, right):
        if isinstance(left, dict):
            return (
                bool(left)
                and isinstance(right, dict)
                and all(k in right and subset(v, right[k]) for k, v in left.items())
            )
        return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)

    return isinstance(claimed, dict) and subset(claimed, actual)


def collector_claims(packet):
    """Read only actual collector handoffs, including the bounded loop outputs."""
    claims = []
    values = [packet.get(key) for key in ("investigate_host", "map_repository")]
    loop = packet.get("follow_up")
    if isinstance(loop, dict):
        values.extend(loop.get("follow_up_check.result") or [])
    for value in values:
        if not isinstance(value, dict):
            continue
        result = value.get("result", value)
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, dict) and isinstance(content.get("evidence"), list):
            claims.extend(
                item
                for item in content["evidence"]
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            )
    return {item["id"]: item for item in claims}


def request_policy_requirements(request):
    """Recognize named operator directives, never infer policy from symptoms."""
    if not isinstance(request, dict):
        return []
    requirements = []
    if isinstance(request.get("policy_requirement"), str):
        requirements.append(request["policy_requirement"])
    # A named line also survives the normal issue/comment-to-event bridge.
    for key in ("description", "body", "comment_body", "comment"):
        text = request.get(key)
        if isinstance(text, dict):
            text = text.get("body")
        if isinstance(text, str):
            requirements.extend(
                match.group(1).strip()
                for match in re.finditer(
                    r"(?im)^\s*(?:\*\*)?Policy requirement(?:\*\*)?:\s*(.+)$", text
                )
            )
    return requirements


def validate_evidence(report, packet, review, repository_url):
    """Return an explicit blocked result for malformed or unproven assessments."""
    blockers = []
    if not all(isinstance(v, dict) for v in [report, packet, review]):
        return {
            "valid": False,
            "blocking_findings": ["Evidence handoffs must be objects."],
        }
    if report.get("decision") not in ["ready", "no_change"]:
        blockers.append("The assessment is blocked or has no actionable decision.")
    if report.get("conclusion_status") != "confirmed":
        blockers.append("A successful outcome requires a confirmed conclusion.")
    if report.get("blocking_findings") != []:
        blockers.append("Final assessment has unresolved or missing blocking findings.")
    context = report.get("repository_context") or {}
    if not isinstance(context, dict):
        context = {}
    expected = urlparse(str(repository_url)).path.strip("/").removesuffix(".git")
    if expected != f"{context.get('owner')}/{context.get('repository')}":
        blockers.append("Assessment repository differs from the configured repository.")
    commit = context.get("read_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        blockers.append("A verified repository commit is required.")
    records = successful_results(packet)
    owner, repo = context.get("owner"), context.get("repository")
    host = report.get("target_host")
    repo_records = [
        r
        for r in records
        if r["input"].get("owner") == owner and r["input"].get("repo") == repo
    ]
    if not any(
        r["tool"] == "get_commit" and isinstance(commit, str) and commit in r["output"]
        for r in repo_records
    ):
        blockers.append(
            "No successful get_commit result establishes the inspected commit."
        )
    pinned_reads = [
        r
        for r in repo_records
        if r["tool"] == "get_file_contents"
        and any(r["input"].get(key) == commit for key in ("ref", "sha"))
        # Reject conflicting aliases, even if one happens to contain the SHA.
        and all(r["input"].get(key) in (None, "", commit) for key in ("ref", "sha"))
    ]
    if not pinned_reads:
        blockers.append(
            "No successful repository file read is pinned to the inspected commit."
        )
    host_records = [
        r
        for r in records
        if r["tool"] in LINUX_TOOLS
        and isinstance(host, str)
        and host not in ["", "localhost", "127.0.0.1"]
        and r["input"].get("host") == host
    ]
    if not any(r["tool"] == "get_system_information" for r in host_records):
        blockers.append("No successful target-host identity measurement was returned.")
    evidence = report.get("evidence")
    evidence = evidence if isinstance(evidence, list) else []
    ids = set()
    verified_ids = set()
    # Preserve collector citations across assessment. The collector prose itself
    # is not proof: every item must still match a scoped runtime result below.
    originals = collector_claims(packet)
    host_supported = repo_supported = False
    for item in evidence:
        if not isinstance(item, dict):
            blockers.append("Malformed evidence item.")
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            blockers.append("Evidence IDs must be nonempty and unique.")
            continue
        ids.add(identifier)
        excerpt = item.get("tool_output_excerpt")
        matches = [
            r
            for r in records
            if r["tool"] == str(item.get("tool", "")).removeprefix("functions.")
            and (
                r in host_records
                or r in pinned_reads
                or (
                    r in repo_records
                    and r["tool"]
                    in {
                        "get_commit",
                        "pull_request_read",
                        "list_commits",
                        "search_code",
                    }
                )
            )
            and matches_output(excerpt, r["output"])
            and (
                r["tool"] not in {"read_file", "get_file_contents"}
                or (
                    isinstance(r["input"].get("path"), str)
                    and bool(r["input"]["path"])
                    and re.search(
                        r"(?<![A-Za-z0-9_.-])"
                        + re.escape(r["input"]["path"])
                        + r"(?=$|[@#:\s])",
                        str(item.get("source", "")),
                    )
                )
            )
        ]
        original = originals.get(identifier)
        if original and any(
            item.get(field) != original.get(field)
            for field in ("tool", "tool_output_excerpt")
        ):
            blockers.append(
                f"{identifier}: collector citation changed; preserve its tool and excerpt exactly."
            )
            matches = []
        if matches:
            verified_ids.add(identifier)
        if not matches:
            blockers.append(
                f"{identifier}: no matching successful runtime tool result/excerpt."
            )
        host_supported |= any(r in host_records for r in matches)
        repo_supported |= any(r in pinned_reads for r in matches)
    if not host_supported or not repo_supported:
        blockers.append(
            "Both target-host and pinned repository observations must support the report."
        )
    findings = review.get("blocking_findings")
    resolved = report.get("resolved_findings")
    if not isinstance(findings, list) or not isinstance(resolved, list):
        blockers.append(
            "Reviewer findings and their explicit resolutions are required."
        )
    else:
        covered = set()
        for resolution in resolved:
            if not isinstance(resolution, dict):
                continue
            refs = resolution.get("evidence_ids")
            finding = resolution.get("finding")
            if (
                isinstance(finding, str)
                and isinstance(refs, list)
                and refs
                and all(isinstance(ref, str) and ref in verified_ids for ref in refs)
            ):
                if finding not in findings:
                    blockers.append(
                        "A resolution does not name an exact reviewer finding."
                    )
                else:
                    covered.add(finding)
        if any(not isinstance(f, str) or f not in covered for f in findings):
            blockers.append(
                "A reviewer blocker lacks an explicit evidence-backed resolution."
            )
    # Desired policy must have an authority separate from the observed runtime
    # or an inactive example. Requests may carry a named policy_requirement;
    # repository requirements must quote independently verified file evidence.
    if report.get("decision") == "ready":
        basis = report.get("policy_basis") or {}
        quote = basis.get("requirement") if isinstance(basis, dict) else None
        established = (
            isinstance(quote, str)
            and bool(quote.strip())
            and basis.get("status") == "established"
        )
        if established and basis.get("origin") == "request":
            request = packet.get("request") or {}
            established = quote in request_policy_requirements(request)
        elif established and basis.get("origin") == "repository":
            refs = basis.get("evidence_ids")
            established = (
                isinstance(refs, list)
                and bool(refs)
                and all(ref in verified_ids for ref in refs)
            )
            cited = [
                item
                for item in evidence
                if isinstance(item, dict) and item.get("id") in (refs or [])
            ]
            # A commented YAML example does not establish an active requirement.
            established = established and any(
                matches_output(quote, item.get("tool_output_excerpt", ""))
                and not all(
                    line.strip() in ("", "---", "...") or line.lstrip().startswith("#")
                    for line in quote.splitlines()
                )
                and item.get("tool") == "get_file_contents"
                for item in cited
            )
        else:
            established = False
        if not established:
            blockers.append(
                "A GitOps edit requires an explicit request policy_requirement or a verified repository policy requirement; runtime values and commented examples are insufficient."
            )
    return {
        "valid": not blockers,
        "blocking_findings": blockers,
        "successful_tool_results": len(records),
        "verified_evidence_ids": sorted(verified_ids),
    }


def normalize_assessment(report, packet, review, repository_url):
    """Bind unchanged collector evidence to real sources; only restrict verdicts.

    This is formatting and fail-closed packaging, never a new diagnosis. AAP
    independently repeats the complete provenance/policy/blocker validation.
    """
    result = copy.deepcopy(report)
    if not isinstance(result, dict):
        raise ValueError("Assessment must be an object")
    originals = collector_claims(packet)
    records = successful_results(packet)
    context = result.get("repository_context") or {}
    repaired = []
    for item in result.get("evidence", []):
        if not isinstance(item, dict):
            continue
        original = originals.get(item.get("id"))
        if not original or any(
            original.get(k) != item.get(k) for k in ("tool", "tool_output_excerpt")
        ):
            continue
        sources = set()
        for record in records:
            args = record["input"]
            if record["tool"] != item.get("tool") or not matches_output(
                item.get("tool_output_excerpt"), record["output"]
            ):
                continue
            if record["tool"] in LINUX_TOOLS and args.get("host") == result.get(
                "target_host"
            ):
                sources.add(
                    "host:"
                    + args["host"]
                    + (
                        (":" + args["path"])
                        if isinstance(args.get("path"), str)
                        else ""
                    )
                )
            elif (
                record["tool"] == "get_file_contents"
                and args.get("owner") == context.get("owner")
                and args.get("repo") == context.get("repository")
                and context.get("read_commit")
                and any(args.get(k) == context["read_commit"] for k in ("sha", "ref"))
                and all(
                    args.get(k) in (None, "", context["read_commit"])
                    for k in ("sha", "ref")
                )
                and isinstance(args.get("path"), str)
            ):
                sources.add(
                    f"repo://{args['owner']}/{args['repo']}@{context['read_commit']}/{args['path']}"
                )
        if len(sources) == 1:
            source = sources.pop()
            if item.get("source") != source:
                item["source"] = source
                repaired.append(item["id"])
    verdict = validate_evidence(result, packet, review, repository_url)
    if not verdict["valid"]:
        remaining = list(result.get("blocking_findings") or [])
        resolutions = result.get("resolved_findings") or []
        covered = {
            r.get("finding")
            for r in resolutions
            if isinstance(r, dict)
            and r.get("evidence_ids")
            and all(e in verdict["verified_evidence_ids"] for e in r["evidence_ids"])
        }
        for finding in review.get("blocking_findings", []):
            if finding not in covered and finding not in remaining:
                remaining.append(finding)
        remaining += [f for f in verdict["blocking_findings"] if f not in remaining]
        result["blocking_findings"] = remaining
        result["decision"] = "blocked"
        if result.get("conclusion_status") == "confirmed":
            result["conclusion_status"] = "hypothesis"
    return {
        "report": result,
        "validation": verdict,
        "canonicalized_evidence_ids": repaired,
    }


class FilterModule:
    def filters(self):
        return {"ao_validate_evidence": validate_evidence}
