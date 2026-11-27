"""Read-only cutover check. No remediation or PR-publishing tools are available."""
import json
import os
import platform
import sqlite3
import subprocess
from uuid import uuid4

from crewai import Agent, Crew, Process, Task
from crewai_tracing import trace_run
import incident_agent as incident
import pr_review_agent  # Verify both production entrypoints import in this EE.


def main():
    assert platform.machine() == "x86_64"
    assert sqlite3.sqlite_version_info >= (3, 35, 0)
    assert incident.MCP_MODE == "container"
    run_id = str(uuid4())
    with trace_run("ee-integration-verification", run_id):
        listing = json.loads(incident.list_state_files.run(relative_path="."))
        assert listing["entries"], "The fresh repository checkout is empty"
        owner, repo = incident._github_repo()
        incident._github_request("GET", f"/repos/{owner}/{repo}")
        catalog = incident.mcp_client.tools()
        observation = incident.call_linux_read_tool.run(
            tool_name="read_file",
            arguments_json=json.dumps({"host": "web-01", "path": "/etc/os-release"}),
        )
        assert "Red Hat" in observation, "Read-only web-01 OS observation failed"
        # Test the exact model configuration with a small response and no tools.
        agent = Agent(role="EE connectivity verifier", goal="Reply READY only",
                      backstory="Perform one connectivity check, without tools.",
                      tools=[], llm=incident._llm(), verbose=False)
        task = Task(description="Reply with the single word READY.",
                    expected_output="READY", agent=agent)
        reply = Crew(agents=[agent], tasks=[task], process=Process.sequential,
                     verbose=False).kickoff()
        assert "READY" in str(reply).upper(), "The configured model did not confirm readiness"
        head = subprocess.check_output(
            ["git", "-C", str(incident.STATE_REPO_PATH), "rev-parse", "HEAD"], text=True,
        ).strip()
        print(json.dumps({"verified": True, "run_id": run_id, "architecture": platform.machine(),
                          "uid": os.geteuid(), "state_revision": head,
                          "authenticated_github_read": True, "external_mcp_tools": len(catalog),
                          "read_only_host": "web-01", "configured_model": incident.LLM_MODEL,
                          "model_reply": "READY", "both_entrypoints_imported": True}))


if __name__ == "__main__":
    main()
