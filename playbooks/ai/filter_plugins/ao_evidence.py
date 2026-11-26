"""Validate claims against runtime tool results, without network or host access.

Agent-written citations and tools_used alone do not establish provenance. Match
exact excerpts or typed JSON fields to successful call/result pairs emitted by
AO's trace recorder.
"""

import hashlib
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
    if not isinstance(excerpt, str) or not excerpt.strip():
        return False
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
            and matches_output(excerpt, r["output"])
            and (
                r["tool"] not in {"read_file", "get_file_contents"}
                or (
                    isinstance(r["input"].get("path"), str)
                    and bool(r["input"]["path"])
                    and r["input"]["path"] in str(item.get("source", ""))
                )
            )
        ]
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
                and all(isinstance(ref, str) and ref in ids for ref in refs)
            ):
                covered.add(finding)
        if any(not isinstance(f, str) or f not in covered for f in findings):
            blockers.append(
                "A reviewer blocker lacks an explicit evidence-backed resolution."
            )
    return {
        "valid": not blockers,
        "blocking_findings": blockers,
        "successful_tool_results": len(records),
        "verified_evidence_ids": sorted(ids) if not blockers else [],
    }


class FilterModule:
    def filters(self):
        return {"ao_validate_evidence": validate_evidence}
