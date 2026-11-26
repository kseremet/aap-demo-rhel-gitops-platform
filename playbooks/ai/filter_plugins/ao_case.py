"""Pure request, repository snapshot and read-only fact collection helpers."""

import base64
import hashlib
import json
import re
from urllib.parse import urlparse
from ansible.errors import AnsibleFilterError


def require(ok, message):
    if not ok:
        raise AnsibleFilterError(message)


def request_context(event, repository_url, mode, hosts, pull_request=None):
    require(isinstance(event, dict), "Event must be a JSON object")
    repo = urlparse(repository_url).path.strip("/").removesuffix(".git")
    require(
        re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo),
        "Invalid configured repository",
    )
    supplied = event.get("repository")
    if isinstance(supplied, dict):
        supplied = supplied.get("full_name")
    require(
        not supplied or supplied == repo,
        "Event repository differs from configured state repository",
    )
    owner, name = repo.split("/")
    number = (
        event.get("pull_request_number")
        or (event.get("pull_request") or {}).get("number")
        or (event.get("issue") or {}).get("number")
    )
    if mode == "pr_review":
        require(
            isinstance(number, int) and not isinstance(number, bool) and number > 0,
            "PR feedback requires its original pull request number",
        )
    context = {
        "owner": owner,
        "repository": name,
        "base_branch": event.get("base_branch", "main"),
        "head_branch": None,
        "read_commit": None,
        "pull_request_number": number if mode == "pr_review" else None,
    }
    if pull_request:
        require(pull_request.get("state") == "open", "PR must be open")
        require(
            pull_request["base"]["repo"]["full_name"] == repo
            and pull_request["head"]["repo"]["full_name"] == repo,
            "Only same-repository PR feedback is supported",
        )
        context.update(
            base_branch=pull_request["base"]["ref"],
            head_branch=pull_request["head"]["ref"],
            read_commit=pull_request["head"]["sha"],
        )
    require(
        isinstance(context["base_branch"], str)
        and re.fullmatch(r"[A-Za-z0-9_./-]+", context["base_branch"]),
        "Invalid base branch",
    )
    target = event.get("target_host")
    allowed = [h for h in hosts if h not in ("localhost", "127.0.0.1")]
    if not target and pull_request:
        marker = re.search(
            r"<!-- ao-context (\{[^\n]+\}) -->", pull_request.get("body") or ""
        )
        if marker:
            try:
                target = json.loads(marker.group(1)).get("target_host")
            except ValueError:
                pass
    if not target:
        text = json.dumps(event, ensure_ascii=False) + (
            " " + (pull_request.get("body") or "") if pull_request else ""
        )
        candidates = [
            h
            for h in allowed
            if re.search(
                r"(?<![A-Za-z0-9_.-])" + re.escape(h) + r"(?![A-Za-z0-9_.-])", text
            )
        ]
        if len(candidates) == 1:
            target = candidates[0]
    if target:
        require(
            isinstance(target, str)
            and re.fullmatch(r"[A-Za-z0-9_.-]+", target)
            and target in allowed,
            "Target must name one managed inventory host, never an inventory pattern",
        )
    return {
        "request": event,
        "repository_context": context,
        "target_host": target,
        "mode": mode,
        "pull_request": pull_request,
        "repository_url": repository_url,
    }


def collection_paths(tree, target):
    require(
        isinstance(tree, dict) and not tree.get("truncated"),
        "Repository tree is incomplete; cannot infer absence of declarations",
    )
    blobs = [item for item in tree.get("tree", []) if item.get("type") == "blob"]
    selected = []
    for item in blobs:
        path = item["path"]
        low = path.lower()
        base = low.rsplit("/", 1)[-1]
        if any(word in base for word in ("vault", "secret")) or base.startswith("."):
            continue
        core = (
            path
            in (
                "site.yml",
                "site.yaml",
                "README.md",
                "inventory",
                "inventory.ini",
                "inventory.yml",
                "inventory.yaml",
            )
            or path.startswith(("tasks/", "inventory/", "inventories/", "group_vars/"))
            or path.startswith("host_vars/" + target + "/")
        )
        role = path.startswith("roles/") and any(
            part in path for part in ("/defaults/", "/vars/", "/tasks/")
        )
        if (core or role) and (
            path.rsplit(".", 1)[-1] in ("yml", "yaml", "ini", "md")
            or path == "inventory"
        ):
            require(
                item.get("size", 0) <= 131072,
                "A required repository file exceeds the supported evidence size",
            )
            selected.append(item)
    require(
        len(selected) <= 64 and sum(i.get("size", 0) for i in selected) <= 131072,
        "Required repository snapshot is too large; narrow the managed repository, never silently omit policy files",
    )
    require(
        any(i["path"] in ("site.yml", "site.yaml") for i in selected),
        "Repository entry playbook is missing",
    )
    return sorted(selected, key=lambda i: i["path"])


def trace_record(tool, args, output):
    identifier = (
        "ev-"
        + hashlib.sha256(
            json.dumps([tool, args, output], sort_keys=True).encode()
        ).hexdigest()[:16]
    )
    return {
        "id": identifier,
        "result": {
            "agent_trace": {
                "steps": [
                    {
                        "type": "tool_call",
                        "call_id": identifier,
                        "tool_name": tool,
                        "tool_input": args,
                    },
                    {
                        "type": "tool_result",
                        "call_id": identifier,
                        "tool_name": tool,
                        "status": "success",
                        "tool_output": output,
                    },
                ]
            }
        },
    }


def repository_snapshot(context, commit, tree, responses):
    require(
        re.fullmatch(r"[0-9a-f]{40}", commit.get("sha", "")),
        "GitHub did not return a pinned commit",
    )
    ctx = dict(context["repository_context"])
    ctx["read_commit"] = commit["sha"]
    files = {}
    packet = {"request": context["request"]}
    args = {"owner": ctx["owner"], "repo": ctx["repository"]}
    packet["commit"] = trace_record(
        "github_api_get_commit", args, json.dumps({"sha": commit["sha"]})
    )
    for response in responses:
        item = response["item"]
        data = response.get("json", {})
        require(
            response.get("status") == 200
            and data.get("encoding") == "base64"
            and data.get("sha") == item["sha"],
            "Required repository blob was not read successfully",
        )
        raw = base64.b64decode(data["content"])
        require(
            hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            == item["sha"],
            "Repository blob hash mismatch",
        )
        content = raw.decode("utf-8")
        files[item["path"]] = {"sha": item["sha"], "content": content}
        packet[item["path"]] = trace_record(
            "github_api_get_file",
            dict(args, sha=ctx["read_commit"], path=item["path"]),
            content,
        )
    require(
        set(files)
        == {i["path"] for i in collection_paths(tree, context["target_host"])},
        "Required repository snapshot is incomplete",
    )
    return dict(
        context,
        repository_context=ctx,
        repository_files=files,
        manifest_paths=[i["path"] for i in tree["tree"] if i.get("type") == "blob"],
        packet=packet,
        snapshot_complete=True,
    )


def finish_collection(snapshot, facts, inventory_groups, job_id):
    # Never export ansible_env, SSH inputs, hostvars, or controller credentials.
    keys = (
        "hostname",
        "fqdn",
        "lvm",
        "mounts",
        "devices",
        "distribution",
        "distribution_version",
        "kernel",
        "architecture",
        "memtotal_mb",
        "memfree_mb",
        "memory_mb",
        "processor_vcpus",
        "processor_count",
        "default_ipv4",
        "interfaces",
    )
    facts = {key.removeprefix("ansible_"): value for key, value in facts.items()}
    selected = {key: facts[key] for key in keys if key in facts}
    require(selected.get("hostname"), "No host identity was measured")
    result = dict(
        snapshot,
        host_facts=selected,
        inventory_groups=list(inventory_groups),
        collection_job_id=str(job_id),
    )
    packet = dict(snapshot["packet"])
    packet["host_facts"] = trace_record(
        "ansible_setup",
        {"host": snapshot["target_host"], "job_id": str(job_id)},
        json.dumps(selected, sort_keys=True),
    )
    packet["collection_complete"] = True
    packet["inventory_groups"] = list(inventory_groups)
    result["packet"] = packet
    return result


class FilterModule:
    def filters(self):
        return {
            "ao_request_context": request_context,
            "ao_collection_paths": collection_paths,
            "ao_repository_snapshot": repository_snapshot,
            "ao_finish_collection": finish_collection,
        }
