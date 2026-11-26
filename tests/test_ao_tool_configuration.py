"""Regress tool allowlists and stale SSH aliases using actual Ansible templates."""

from pathlib import Path
import sys

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]


def test_tool_selection_excludes_disabled_unavailable_and_unrelated_tools():
    if not any(type(f).__name__ == "_AnsibleCollectionFinder" for f in sys.meta_path):
        init_plugin_loader()
    loader = DataLoader()
    tasks = loader.load_from_file(
        str(ROOT / "playbooks/ao_ensure_tools.yml"), trusted_as_template=True
    )

    def tool(name, **kwargs):
        return {
            "id": name,
            "name": name,
            "enabled": True,
            "status": "available",
            **kwargs,
        }

    catalogs = [
        [
            tool("get_system_information"),
            tool("read_file"),
            tool("write_file"),
            tool("get_service_logs", enabled=False),
            tool("get_service_status", status="missing"),
        ],
        [
            tool("get_commit"),
            tool("get_file_contents"),
            tool("pull_request_read"),
            tool("delete_file"),
        ],
    ]
    variables = {
        "_ao_tool_catalogs": {"results": [{"json": {"resources": c}} for c in catalogs]}
    }
    for task in tasks:
        if "ansible.builtin.set_fact" in task:
            variables.update(
                Templar(loader=loader, variables=variables).template(
                    task["ansible.builtin.set_fact"]
                )
            )
    assert variables["ao_linux_tool_ids"] == ["get_system_information", "read_file"]
    assert variables["ao_github_tool_ids"] == [
        "get_commit",
        "get_file_contents",
        "pull_request_read",
    ]
    assert len(variables["ao_readonly_tool_ids"]) == 5


def test_live_address_refresh_preserves_ssh_identity_and_other_aliases():
    if not any(type(f).__name__ == "_AnsibleCollectionFinder" for f in sys.meta_path):
        init_plugin_loader()
    loader = DataLoader()
    tasks = loader.load_from_file(
        str(ROOT / "playbooks/ao_refresh_mcp_hosts.yml"), trusted_as_template=True
    )
    task = next(
        t for t in tasks if t["name"] == "Update managed alias addresses from live VMIs"
    )
    before = "Host *\n  User cloud-user\n  IdentityFile /var/lib/mcp/.ssh/id_ed25519\nHost web-01\n  HostName 10.128.1.31\nHost other\n  HostName 10.1.2.3\n"
    variables = {
        "_ao_mcp_ssh_config": before,
        "item": {
            "item": {"key": "web-01"},
            "resources": [
                {
                    "status": {
                        "interfaces": [{"name": "default", "ipAddress": "10.130.0.234"}]
                    }
                }
            ],
        },
    }
    variables.update(task["vars"])
    result = Templar(loader=loader, variables=variables).template(
        task["ansible.builtin.set_fact"]
    )
    assert result["_ao_mcp_ssh_config"] == before.replace("10.128.1.31", "10.130.0.234")
