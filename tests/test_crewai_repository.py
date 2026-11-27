"""Exercise the actual template's repository helpers without LLM/network access."""

import ast
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "state"
    repo.mkdir()
    files = {
        "group_vars/all/kernel_settings.yml": "baseline:\n  net.core.somaxconn: 1024\n",
        "group_vars/web_servers/kernel_settings.yml": "# net.core.somaxconn: 65535\n",
        "site.yml": "- import_playbook: tasks/configure.yml\n",
    }
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (repo / "binary.dat").write_bytes(b"\xff\x00")
    (repo / "large.txt").write_text("x" * 131073)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    # Parse the production functions, dropping only CrewAI's registration wrapper.
    source = (ROOT / "roles/ai_control/templates/incident_agent.py.j2").read_text()
    names = {"_state_path", "list_state_files", "search_state_files", "read_state_file"}
    tree = ast.parse(source)
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in tree.body:
        node.decorator_list = []
    namespace = {"STATE_REPO_PATH": repo, "Path": Path, "json": json, "os": os, "subprocess": subprocess}
    exec(compile(tree, "repository-helpers", "exec"), namespace)
    namespace["list_state_files"].func = namespace["list_state_files"]
    return repo, SimpleNamespace(**namespace)


def test_root_and_directories_are_navigable(repository):
    _, tools = repository
    root = json.loads(tools.read_state_file("."))
    assert {"path": "group_vars", "type": "directory"} in root["entries"]
    group = json.loads(tools.list_state_files("group_vars/web_servers"))
    path = group["entries"][0]["path"]
    assert path == "group_vars/web_servers/kernel_settings.yml"
    assert "65535" in tools.read_state_file(path)
    assert not any(e["path"] == ".git" for e in root["entries"])


def test_search_finds_real_filenames_and_active_and_commented_values(repository):
    _, tools = repository
    results = json.loads(tools.search_state_files("SOMAXCONN"))
    assert {m["path"] for m in results["matches"]} == {
        "group_vars/all/kernel_settings.yml", "group_vars/web_servers/kernel_settings.yml"
    }
    assert all(m["line"] for m in results["matches"])
    assert any(m["text"].startswith("#") for m in results["matches"])
    assert results["skipped_files"] == 2
    assert not results["truncated"]
    assert json.loads(tools.search_state_files("kernel_settings"))["matches"]


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", ".git/config", "escape", "git-alias/config"])
def test_traversal_and_symlink_escapes_are_refused(repository, path):
    repo, tools = repository
    (repo.parent / "outside.txt").write_text("private data")
    (repo / "escape").symlink_to(repo.parent / "outside.txt")
    (repo / "git-alias").symlink_to(repo / ".git", target_is_directory=True)
    assert "Cannot read" in tools.read_state_file(path)
    assert "private data" not in tools.read_state_file(path)


def test_missing_files_binary_files_and_limits_are_reported(repository):
    repo, tools = repository
    assert "List its parent" in tools.read_state_file("missing.yml")
    assert "Cannot read" in tools.read_state_file("binary.dat")
    assert "128 KiB" in tools.read_state_file("large.txt")
    assert "non-empty" in tools.search_state_files(" ")
    (repo / "many.txt").write_text("match\n" * 80)
    subprocess.run(["git", "-C", str(repo), "add", "many.txt"], check=True)
    results = json.loads(tools.search_state_files("match"))
    assert len(results["matches"]) == 60
    assert results["truncated"]


def test_agent_tool_registration():
    # Check the production agent wiring, not just the helper implementations.
    for template in ("incident_agent.py.j2", "pr_review_agent.py.j2"):
        tree = ast.parse((ROOT / "roles/ai_control/templates" / template).read_text())
        agents = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name) and node.func.id == "Agent"]
        for agent in agents:
            tools = next(k.value for k in agent.keywords if k.arg == "tools")
            names = {n.id for n in tools.elts if isinstance(n, ast.Name)}
            if "read_state_file" in names:
                assert {"list_state_files", "search_state_files"} <= names
