"""Read-only cutover check. No remediation or PR-publishing tools are available."""
import json
import os
import platform
import sqlite3
import subprocess
from uuid import uuid4

from crewai import Agent, Crew, Process, Task
from crewai.tools import tool
from crewai_tracing import trace_run
import incident_agent as incident
import pr_review_agent  # Verify both production entrypoints import in this EE.


def verify_model_tool_call(factory, entrypoint):
    """Require an executed repository read and consumption of its fresh result."""
    nonce = str(uuid4())
    observations = []

    @tool("read_verification_repository")
    def read_verification_repository() -> str:
        """Read the state repository root and return a fresh verification receipt."""
        listing = json.loads(incident.list_state_files.run(relative_path="."))
        assert listing["entries"], "The state repository is empty"
        observations.append(listing)
        # The receipt is deliberately absent from the prompt: a guessed answer
        # or a fabricated tool call cannot satisfy the readiness assertion.
        return json.dumps({"receipt": nonce, "entries": listing["entries"]})

    llm = factory()
    agent = Agent(role=f"{entrypoint} tool-calling verifier",
                  goal="Read the repository using the provided tool and echo its receipt",
                  backstory="Perform one read-only integration check.",
                  tools=[read_verification_repository], llm=llm,
                  cache=False, max_iter=4, verbose=False)
    task = Task(description=("Call read_verification_repository to read the repository. "
                             "Then reply READY followed by the exact receipt returned "
                             "by that tool. Do not invent a receipt."),
                expected_output="READY followed by the tool's receipt", agent=agent)
    reply = Crew(agents=[agent], tasks=[task], process=Process.sequential,
                 cache=False, verbose=False).kickoff()
    assert observations, f"{entrypoint}: the model did not execute the repository tool"
    assert nonce in str(reply) and "READY" in str(reply).upper(), (
        f"{entrypoint}: the model did not consume the tool result")
    return {"entrypoint": entrypoint, "model_api": incident.LLM_API,
            "tool_calls": len(observations), "tool_result_consumed": True}


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
        # Exercise both production LLM constructors with native function tools.
        model_checks = [verify_model_tool_call(factory, entrypoint)
                        for entrypoint, factory in (("incident", incident._llm),
                                                    ("pr_review", pr_review_agent._llm))]
        head = subprocess.check_output(
            ["git", "-C", str(incident.STATE_REPO_PATH), "rev-parse", "HEAD"], text=True,
        ).strip()
        print(json.dumps({"verified": True, "run_id": run_id, "architecture": platform.machine(),
                          "uid": os.geteuid(), "state_revision": head,
                          "authenticated_github_read": True, "external_mcp_tools": len(catalog),
                          "read_only_host": "web-01", "configured_model": incident.LLM_MODEL,
                          "model_reply": "READY", "model_tool_checks": model_checks,
                          "both_entrypoints_imported": True}))


if __name__ == "__main__":
    main()
