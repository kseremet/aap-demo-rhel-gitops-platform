"""Offline checks for the internal Phoenix deployment and short-lived job tracing."""

from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_stays_internal_and_sqlite_has_one_persistent_writer():
    text = (ROOT / "playbooks/templates/phoenix.yml.j2").read_text()
    objects = list(yaml.safe_load_all(Environment().from_string(text).render(
        openshift_vm_namespace="koray", phoenix_image="test:1", phoenix_storage_size="2Gi",
        phoenix_storage_class="",
    )))
    resources = {r["kind"]: r for r in objects}
    assert set(resources) == {"PersistentVolumeClaim", "Service", "Deployment"}
    assert resources["Service"]["spec"]["type"] == "ClusterIP"
    deployment = resources["Deployment"]["spec"]
    assert deployment["replicas"] == 1
    assert deployment["strategy"]["type"] == "Recreate"
    pod = deployment["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["volumes"][0]["persistentVolumeClaim"]["claimName"] == "phoenix-data"
    env = {e["name"]: e["value"] for e in pod["containers"][0]["env"]}
    assert env["PHOENIX_WORKING_DIR"] == "/data"
    assert env["PHOENIX_PORT"] == "6006"
    assert env["PHOENIX_TELEMETRY_ENABLED"] == "false"
    assert env["PHOENIX_ENABLE_MCP_SERVER"] == "false"


@pytest.fixture
def tracing(monkeypatch):
    spec = importlib.util.spec_from_file_location("test_tracing_helper", ROOT / "roles/ai_control/files/crewai_tracing.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    monkeypatch.setattr(helper, "_config", lambda: {
        "enabled": True, "endpoint": "http://internal/v1/traces", "project": "demo",
        "dashboard_url": "http://internal",
    })
    return helper


def test_tracing_flushes_after_closing_root_even_when_the_crew_fails(tracing, monkeypatch):
    events = []

    @contextmanager
    def root(*args, **kwargs):
        events.append("start")
        try:
            yield SimpleNamespace(get_span_context=lambda: SimpleNamespace(trace_id=123))
        finally:
            events.append("end")

    provider = SimpleNamespace(
        get_tracer=lambda name: SimpleNamespace(start_as_current_span=root),
        force_flush=lambda **kwargs: events.append("flush"),
    )
    monkeypatch.setattr(tracing, "_tracer_provider", lambda config: provider)
    with pytest.raises(ValueError, match="crew failed"):
        with tracing.trace_run("test", "run-1"):
            raise ValueError("crew failed")
    assert events == ["start", "end", "flush"]


def test_unavailable_instrumentation_does_not_block_investigation(tracing, monkeypatch):
    def unavailable(config):
        raise ImportError("optional instrumentation unavailable")
    monkeypatch.setattr(tracing, "_tracer_provider", unavailable)
    with tracing.trace_run("test", "run-2"):
        result = "investigation still runs"
    assert result == "investigation still runs"


def test_disabled_tracing_keeps_existing_verbose_mode(tracing, monkeypatch):
    monkeypatch.setattr(tracing, "_config", lambda: {"enabled": False})
    monkeypatch.setattr(tracing, "_tracer_provider", lambda config: pytest.fail("Tracing must stay disabled"))
    assert tracing.crew_verbose() is True
    with tracing.trace_run("test", "run-3"):
        pass
