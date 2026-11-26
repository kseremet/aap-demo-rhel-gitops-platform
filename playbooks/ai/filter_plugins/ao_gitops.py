"""Validate AO's proposed variable edits and render a complete maintainer report.

These filters run on the controller. They never contact a host or GitHub; the
playbook supplies the files read at the reviewed commit before publishing.
"""

import base64
from collections import Counter
from copy import deepcopy
import json
import re
from urllib.parse import urlparse

from ansible.errors import AnsibleFilterError
import yaml


def require(condition, message):
    if not condition:
        raise AnsibleFilterError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def same(left, right):
    # Python considers True == 1; YAML configuration types must remain distinct.
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently discarding configuration."""


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        require(isinstance(key, str), "Variable mapping keys must be strings")
        require(key not in result, f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def load_variables(content):
    require(
        isinstance(content, str) and 0 < len(content.encode()) <= 131072,
        "Variable files must contain 1..131072 UTF-8 bytes",
    )
    try:
        # Expanding aliases while replacing a whole file can hide broad changes.
        require(
            not any(
                isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken))
                for token in yaml.scan(content)
            ),
            "YAML aliases require manual review",
        )
        result = yaml.load(content, Loader=UniqueLoader)
        require(
            isinstance(result, dict) and bool(result),
            "Expected a nonempty YAML variables mapping",
        )
        json.dumps(result, sort_keys=True)
        return result
    except (yaml.YAMLError, TypeError, ValueError) as exc:
        raise AnsibleFilterError(f"Invalid YAML variable content: {exc}") from exc


def differences(old, new, prefix=()):
    """Return changed leaves, refusing deletion of any existing mapping key."""
    result = {}
    for key in old:
        require(
            key in new,
            f"Removing existing setting is not supported: {'.'.join(prefix + (key,))}",
        )
    for key, value in new.items():
        path = prefix + (key,)
        if key not in old:
            if isinstance(value, dict):
                result.update(differences({}, value, path))
            else:
                result[path] = (None, value)
        elif isinstance(old[key], dict) and isinstance(value, dict):
            result.update(differences(old[key], value, path))
        elif not same(old[key], value):
            require(
                not isinstance(old[key], dict) and not isinstance(value, dict),
                f"Replacing a whole mapping is not supported: {'.'.join(path)}",
            )
            result[path] = (old[key], value)
    return result


def validate_plan(plan, report, review, repository_url, branch_prefix="aiops/"):
    require(isinstance(plan, dict), "AO plan must be an object")
    require(
        isinstance(report, dict) and report.get("decision") == "ready",
        "Only an evidence-supported ready assessment may write",
    )
    require(
        isinstance(review, dict)
        and review.get("approved") is True
        and review.get("blocking_findings") == [],
        "Independent plan review must approve with no blockers",
    )
    require(text(review.get("summary")), "Independent review must explain its approval")
    parsed = urlparse(repository_url)
    expected_repo = parsed.path.strip("/").removesuffix(".git")
    require(
        expected_repo == f"{plan.get('owner')}/{plan.get('repository')}",
        "Plan repository must match the configured state repository",
    )
    require(
        all(
            isinstance(plan.get(key), str)
            and re.fullmatch(r"[A-Za-z0-9_.-]+", plan[key])
            for key in ("owner", "repository")
        ),
        "Invalid repository identity",
    )
    require(
        plan.get("operation") in ("create_pr", "update_pr"),
        "Unsupported GitOps operation",
    )
    for key in ("base_branch", "branch"):
        value = plan.get(key)
        require(
            text(value)
            and not re.search(r"[\s~^:?*\[\\\x00-\x1f\x7f]", value)
            and not any(part in value for part in ("..", "@{", "//"))
            and not value.startswith(("/", ".", "-"))
            and not value.endswith(("/", ".", ".lock")),
            f"Invalid {key}",
        )
    require(
        plan["branch"].startswith(branch_prefix)
        and plan["branch"] != plan["base_branch"],
        "The proposal must use a dedicated AI PR branch",
    )
    require(
        sha(plan.get("read_commit")), "Plan must pin the inspected base/head commit"
    )
    context = report.get("repository_context", {})
    require(
        isinstance(context, dict)
        and all(
            context.get(key) == plan.get(key)
            for key in ("owner", "repository", "base_branch", "read_commit")
        ),
        "Plan identity and inspected revision must match the diagnostic assessment",
    )
    if plan["operation"] == "update_pr":
        require(
            isinstance(plan.get("pull_request_number"), int)
            and not isinstance(plan["pull_request_number"], bool)
            and plan["pull_request_number"] > 0,
            "Revision requires a positive PR number",
        )
        require(
            context.get("head_branch") == plan["branch"]
            and context.get("pull_request_number") == plan["pull_request_number"],
            "Revision must retain the PR identity established during diagnosis",
        )
    require(
        all(text(plan.get(key)) for key in ("pr_title", "commit_message")),
        "PR title and commit message are required",
    )
    for key in (
        "incident",
        "target_host",
        "root_cause",
        "scope",
        "reconciliation",
        "recommendation",
    ):
        require(text(report.get(key)), f"Assessment must explain {key}")
    require(
        report.get("conclusion_status") == "confirmed",
        "A proposed edit needs a confirmed causal explanation",
    )
    require(
        isinstance(report.get("evidence"), list) and len(report["evidence"]) >= 2,
        "Host and repository evidence are required",
    )
    ids = []
    for item in report["evidence"]:
        require(
            isinstance(item, dict)
            and all(text(item.get(k)) for k in ("id", "tool", "source", "observation")),
            "Each observation needs an evidence ID, actual tool, source, and result",
        )
        ids.append(item["id"])
    require(len(ids) == len(set(ids)), "Evidence IDs must be unique")
    for key in ("validation_plan", "alternatives", "limitations"):
        require(
            isinstance(report.get(key), list) and all(text(x) for x in report[key]),
            f"Assessment {key} must be an array of nonempty strings",
        )
    require(report["validation_plan"], "A future validation plan is required")
    files = plan.get("files")
    require(
        isinstance(files, list) and 1 <= len(files) <= 4,
        "Plan needs 1..4 scoped variable files",
    )
    paths = []
    for item in files:
        require(isinstance(item, dict), "Each planned file must be an object")
        path = item.get("path", "")
        require(
            isinstance(path, str)
            and re.fullmatch(
                r"(?:group_vars|host_vars)/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+\.ya?ml", path
            ),
            "Only direct group_vars/<group>/*.yml or host_vars/<host>/*.yml edits are supported",
        )
        if path.startswith("host_vars/"):
            require(
                path.split("/")[1] == report["target_host"],
                "Host edits must target the diagnosed host",
            )
        require(
            "expected_sha" in item
            and (item["expected_sha"] is None or sha(item["expected_sha"])),
            "Each file needs its inspected blob SHA, or null for a justified new host overlay",
        )
        if item["expected_sha"] is None:
            require(
                path.startswith(f"host_vars/{report['target_host']}/"),
                "Only a new target-host overlay may be created",
            )
        require(
            text(item.get("reason")), "Each file needs a causal and scope justification"
        )
        require(
            isinstance(item.get("evidence_ids"), list)
            and item["evidence_ids"]
            and set(item["evidence_ids"]).issubset(ids),
            "File changes must cite assessment evidence IDs",
        )
        require(
            isinstance(item.get("setting_changes"), list) and item["setting_changes"],
            "Each file must enumerate its exact YAML leaf changes",
        )
        load_variables(item.get("content"))
        paths.append(path)
    basis = report.get("proposal_basis") or {}
    if basis.get("kind") == "adopt_observed":
        require(
            any(
                isinstance(change, dict)
                and str(change.get("after")).strip()
                == str(basis.get("proposed_value")).strip()
                for item in files
                for change in item["setting_changes"]
            ),
            "Observed-value proposal must actually declare the verified proposed value",
        )
    require(len(paths) == len(set(paths)), "Duplicate file paths are not allowed")
    require(
        sorted(review.get("reviewed_paths", [])) == sorted(paths),
        "Reviewer must cover every proposed file",
    )
    return deepcopy(plan)


def consumed_variables(response):
    """Discover declared inputs of the existing repository's variable loader.

    This workflow supports the repository's existing defaulted-input merge
    pattern. A different loader requires an explicit extension of this contract.
    """
    data = response.get("json", {})
    require(
        response.get("status") == 200
        and data.get("type") == "file"
        and data.get("encoding") == "base64",
        "The pinned tasks/prepare_vars.yml loader must be readable",
    )
    tasks = yaml.safe_load(base64.b64decode(data["content"]).decode("utf-8"))
    require(
        isinstance(tasks, list), "Expected the existing variable preparation task list"
    )
    names = set()
    for task in tasks:
        if isinstance(task, dict) and "ansible.builtin.set_fact" in task:
            sources = [task["ansible.builtin.set_fact"], task.get("vars", {})]
            names.update(
                re.findall(
                    r"\b([a-z][a-z0-9_]*)\s*\|\s*default\s*\(", json.dumps(sources)
                )
            )
    require(bool(names), "No supported variable inputs were found in the pinned loader")
    return sorted(names)


def validate_file(item, response, allowed_variables, report=None, packet=None):
    """Compare the actual pinned GitHub blob with the proposed replacement."""
    status = response.get("status")
    data = response.get("json", {})
    if item["expected_sha"] is None:
        require(status == 404, "New overlay already exists; inspect it and re-plan")
        old_text, old = "", {}
    else:
        require(
            status == 200
            and data.get("type") == "file"
            and data.get("sha") == item["expected_sha"],
            "File is missing or its inspected SHA is stale; re-plan",
        )
        require(
            data.get("encoding") == "base64" and "content" in data,
            "GitHub did not return the complete file",
        )
        old_text = base64.b64decode(data["content"]).decode("utf-8")
        # GitHub blob identity protects against truncated or inconsistent responses.
        import hashlib

        raw = old_text.encode("utf-8")
        require(
            hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            == data["sha"],
            "Returned content does not match the Git blob SHA",
        )
        # A file containing only commented examples is a valid starting point.
        old = load_variables(old_text) if yaml.safe_load(old_text) is not None else {}
    new = load_variables(item["content"])
    actual = differences(old, new)
    require(bool(actual), "No effective YAML change was proposed")
    require(
        all(path[0] in allowed_variables for path in actual),
        "Changed variable namespace is not consumed by tasks/prepare_vars.yml",
    )
    declared = {}
    for change in item["setting_changes"]:
        require(
            isinstance(change, dict)
            and isinstance(change.get("path"), list)
            and change["path"]
            and all(text(x) for x in change["path"])
            and "before" in change
            and "after" in change,
            "Invalid setting change",
        )
        path = tuple(change["path"])
        require(path not in declared, "Duplicate setting change")
        declared[path] = (change["before"], change["after"])
    require(
        set(actual) == set(declared)
        and all(same(actual[k], declared[k]) for k in actual),
        "Actual YAML changes differ from the reviewed setting_changes; unrelated edits are rejected",
    )
    # A storage proposal is still only a PR, but it must not suggest shrinking
    # or resizing an LV other than the one whose backing capacity was measured.
    for path in actual:
        if path[0].startswith("storage_"):
            require(
                path[-1] == "size",
                "Only measured LV size growth is supported for automatic storage proposals",
            )
            validate_storage_edit(item, path, new, report, packet)
    # Preserve comments as well as settings; do not accept a rewritten document.
    comments = lambda value: Counter(
        line.strip() for line in value.splitlines() if line.lstrip().startswith("#")
    )
    require(
        not (comments(old_text) - comments(item["content"])),
        "Existing comments must be preserved",
    )
    return {
        "path": item["path"],
        "mode": "100644",
        "type": "blob",
        "content": item["content"],
    }


def validate_storage_edit(item, path, proposed, report, packet):
    from decimal import Decimal

    require(
        isinstance(report, dict) and isinstance(packet, dict),
        "Storage size edits require the verified capacity report and source packet",
    )
    capacity = report.get("capacity_assessment") or {}
    require(
        isinstance(capacity, dict)
        and report.get("proposal_basis", {}).get("kind") == "capacity_change",
        "Storage size edits require a capacity_change proposal",
    )
    require(
        item["path"].startswith("host_vars/" + report["target_host"] + "/"),
        "Storage growth requires target-host scope; one host's capacity cannot authorize fleet-wide resizing",
    )
    values = {}
    groups = packet.get("inventory_groups") or []
    for source, wrapper in packet.items():
        if not isinstance(source, str) or not isinstance(wrapper, dict):
            continue
        eligible = (
            source.startswith("group_vars/all/")
            or source.startswith("host_vars/" + report["target_host"] + "/")
            or any(source.startswith("group_vars/" + group + "/") for group in groups)
        )
        if not eligible or source == item["path"]:
            continue
        steps = wrapper.get("result", {}).get("agent_trace", {}).get("steps", [])
        for step in steps:
            args = step.get("tool_input", {})
            if step.get("type") == "tool_call":
                context = report["repository_context"]
                require(
                    args.get("owner") == context["owner"]
                    and args.get("repo") == context["repository"]
                    and args.get("sha") == context["read_commit"],
                    "Storage declaration is not pinned to the reviewed repository",
                )
            if step.get("type") == "tool_result" and step.get("status") == "success":
                parsed = (
                    load_variables(step["tool_output"])
                    if yaml.safe_load(step["tool_output"]) is not None
                    else {}
                )
                for key, value in parsed.items():
                    if key.startswith("storage_"):
                        require(
                            key not in values or same(values[key], value),
                            "Ambiguous storage declarations require explicit precedence evidence",
                        )
                        values[key] = value
    for key in proposed:
        require(
            key not in values or same(values[key], proposed[key]),
            "Extend the existing storage declaration in its current file; a second file must not shadow another managed mapping",
        )
    values.update(proposed)

    def merge(left, right):
        result = deepcopy(left)
        for key, value in right.items():
            result[key] = (
                merge(result[key], value)
                if key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
                else deepcopy(value)
            )
        return result

    require(
        path[0] == "storage_host_pools" and len(path) == 5 and path[2] == "volumes",
        "Storage growth must use the existing target-host storage merge contract",
    )
    effective = merge(
        merge(
            values.get("storage_baseline_pools", {}),
            values.get("storage_group_pools", {}),
        ),
        values.get("storage_host_pools", {}),
    )
    try:
        pool = effective[path[1]]
        volume = pool["volumes"][path[3]]
    except (KeyError, TypeError):
        require(
            False, "Proposed storage path does not identify an existing managed volume"
        )
    require(
        pool.get("name") == capacity.get("volume_group")
        and volume.get("name") == capacity.get("logical_volume")
        and volume.get("mount_point") == capacity.get("mount_point"),
        "Proposed variable path does not identify the measured LV/VG/mount",
    )
    match = re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)?)\s*([kmgtpe]?)(?:i?b)?",
        str(volume["size"]).strip().lower(),
    )
    require(
        match,
        "Storage size must be explicit; percentage-based changes are not supported",
    )
    size = int(
        Decimal(match[1]) * (1024 ** ("kmgtpe".index(match[2]) + 1) if match[2] else 1)
    )
    require(
        size == capacity.get("proposed_size_bytes"),
        "Planned storage size differs from the verified capacity assessment",
    )


def render_report(report, plan, review, execution):
    lines = [
        "<!-- aiops-agent-review -->",
        "<!-- ao-context "
        + json.dumps({"target_host": report["target_host"]}, separators=(",", ":"))
        + " -->",
        "## Incident",
        report["incident"],
        "## Diagnosis",
        report["root_cause"],
        "## Proposal for Maintainer Review",
        (report.get("proposal_basis") or {}).get("rationale", report["recommendation"]),
        "The proposed future state is a recommendation, not an already approved policy. Human review and merge remain required.",
        "## Evidence",
    ]
    for item in report["evidence"]:
        lines.append(
            f"- **{item['id']}** — `{item['tool']}` — {item['source']}: {item['observation']}"
        )
        excerpt = item.get("tool_output_excerpt", "")
        if text(excerpt):
            label = "Matched tool output"
            if len(excerpt) > 1000:
                label += " (first 1000 characters; complete matched excerpt retained in AAP artifacts)"
            # Keep Markdown in untrusted tool data inside a fixed code block.
            lines.extend(
                [
                    "",
                    label + ":",
                    "```text",
                    excerpt[:1000].replace("```", "` ` `"),
                    "```",
                    "",
                ]
            )
    lines.extend(
        [
            "## Reconciliation and Scope",
            report["reconciliation"],
            report["scope"],
            "## Proposed Change",
            report["recommendation"],
        ]
    )
    for item in plan["files"]:
        lines.extend([f"### `{item['path']}`", item["reason"]])
        for change in item["setting_changes"]:
            lines.append(
                f"- `{'.'.join(change['path'])}`: `{json.dumps(change['before'])}` → `{json.dumps(change['after'])}`"
            )
    for title, key in (
        ("Alternatives Considered", "alternatives"),
        ("Validation Plan (Pending)", "validation_plan"),
        ("Limitations and Open Questions", "limitations"),
    ):
        lines.append(f"## {title}")
        lines.extend(f"- {item}" for item in report[key])
        if not report[key]:
            lines.append("None reported.")
    lines.extend(
        [
            "## Automated Review",
            review["summary"],
            "This automated review does not constitute human approval. Managed hosts were not modified by this workflow.",
            "## Run Context",
            f"- AO execution: `{execution}`",
            f"- Inspected commit: `{plan['read_commit']}`",
            f"- Operation: `{plan['operation']}`",
        ]
    )
    body = "\n\n".join(lines) + "\n"
    require(len(body) <= 60000, "Report is too large for a GitHub PR body")
    return body


class FilterModule:
    def filters(self):
        return {
            "ao_validate_plan": validate_plan,
            "ao_validate_file": validate_file,
            "ao_consumed_variables": consumed_variables,
            "ao_render_report": render_report,
        }
