"""Run the actual AO model-resolution tasks against a local catalog API."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DESIRED = dict(
    id="selected-model-uuid", model_id="gpt-4.1", name="GPT 4.1", enabled=True
)


@pytest.mark.parametrize(
    "scenario,success",
    [
        ("display_label", True),
        ("later_page", True),
        ("display_collision", True),
        ("wrong_provider_id", False),
        ("disabled", False),
        ("missing", False),
        ("duplicate", False),
    ],
)
def test_exact_enabled_provider_model_selection(tmp_path, scenario, success):
    desired = dict(DESIRED)
    catalog = [desired]
    if scenario == "later_page":
        catalog = [
            dict(
                id=f"other-{i}",
                model_id=f"other-model-{i}",
                name=f"Other {i}",
                enabled=True,
            )
            for i in range(150)
        ] + [desired]
    elif scenario == "display_collision":
        catalog.append(
            dict(
                id="wrong-model", model_id="gpt-4.1-mini", name="gpt-4.1", enabled=True
            )
        )
    elif scenario == "wrong_provider_id":
        catalog = [
            dict(
                id="wrong-model", model_id="gpt-4.1-mini", name="gpt-4.1", enabled=True
            )
        ]
    elif scenario == "disabled":
        desired["enabled"] = False
    elif scenario == "missing":
        catalog = []
    elif scenario == "duplicate":
        catalog.append({**desired, "id": "duplicate-model"})
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            requests.append(query)
            models = catalog
            if "model_id[eq]" in query:
                models = [
                    m for m in models if m["model_id"] == query["model_id[eq]"][0]
                ]
            page = models[:100]
            payload = json.dumps(
                dict(resources=page, next="later-page" if len(models) > 100 else None)
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        all_tasks = yaml.safe_load(
            (ROOT / "playbooks/ao_ensure_integration.yml").read_text()
        )
        names = {
            "Resolve the LLM model record used by AO workflows",
            "Match the configured provider model identifier",
            "Require the configured AO model in the discovered model catalog",
            "Store project LLM integration and credential IDs",
        }
        tasks = [task for task in all_tasks if task["name"] in names]
        assert len(tasks) == 4
        tasks.append(
            dict(
                name="Expose selected UUID for the test",
                **{"ansible.builtin.debug": {"var": "ao_llm_model_id"}},
            )
        )
        play = [
            dict(
                name="Offline catalog resolution",
                hosts="localhost",
                gather_facts=False,
                vars=dict(
                    ao_api_base_url=f"http://127.0.0.1:{server.server_port}",
                    ao_integration_id="test-integration",
                    ao_access_token="fake-token",
                    ao_integration=dict(
                        integration_type="llm_provider", name="Test LLM"
                    ),
                    ao_integration_credential_id="fake-credential",
                    demo_ao_validate_certs=False,
                    ao_llm_model_name="gpt-4.1",
                ),
                tasks=tasks,
            )
        ]
        (tmp_path / "test.yml").write_text(yaml.safe_dump(play, sort_keys=False))
        (tmp_path / "ansible.cfg").write_text(
            "[defaults]\ninterpreter_python=auto_silent\n"
        )
        env = {
            **os.environ,
            "ANSIBLE_CONFIG": str(tmp_path / "ansible.cfg"),
            "ANSIBLE_HOME": str(tmp_path / "home"),
            "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-tmp"),
        }
        result = subprocess.run(
            [
                shutil.which("ansible-playbook"),
                "-i",
                "localhost,",
                "-c",
                "local",
                "test.yml",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert requests and requests[0].get("model_id[eq]") == ["gpt-4.1"]
        assert (result.returncode == 0) is success, result.stdout + result.stderr
        if success:
            assert DESIRED["id"] in result.stdout
        else:
            assert "must match exactly one enabled" in result.stdout
            assert '"ao_llm_model_id":' not in result.stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
