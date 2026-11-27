"""Optional Phoenix tracing for both VM entrypoints; no change to crew execution."""

from contextlib import contextmanager
import json
from pathlib import Path
import sys

CONFIG_PATH = Path(__file__).with_name("tracing.json")
_provider = None


def _config():
    if not CONFIG_PATH.is_file():
        return {"enabled": False}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def crew_verbose():
    """Keep AAP output short when the detailed execution is available in Phoenix."""
    return not _config().get("enabled", False)


def _tracer_provider(config):
    global _provider
    if _provider is None:
        from openinference.instrumentation.crewai import CrewAIInstrumentor
        from openinference.instrumentation.openai import OpenAIInstrumentor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({
            "service.name": "rhel-gitops-crewai",
            "openinference.project.name": config["project"],
        }))
        # Export finished tool/LLM spans during the run, rather than buffering the
        # complete crew until exit. Use a separate provider from CrewAI telemetry.
        provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=config["endpoint"], timeout=5),
            schedule_delay_millis=1000,
            max_export_batch_size=64,
        ))
        CrewAIInstrumentor().instrument(tracer_provider=provider)
        OpenAIInstrumentor().instrument(tracer_provider=provider)
        _provider = provider
    return _provider


@contextmanager
def trace_run(workflow, run_id):
    config = _config()
    if not config.get("enabled", False):
        yield
        return
    try:
        provider = _tracer_provider(config)
    except Exception as exc:
        # An observability dependency must not prevent incident investigation.
        print(f"Phoenix tracing unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        yield
        return

    tracer = provider.get_tracer(__name__)
    try:
        with tracer.start_as_current_span(workflow, attributes={
            "openinference.span.kind": "CHAIN",
            "aiops.workflow": workflow,
            "aiops.run_id": run_id,
        }) as span:
            print(
                f"Phoenix: {config['dashboard_url']} | project: {config['project']} | "
                f"trace: {span.get_span_context().trace_id:032x}", flush=True,
            )
            yield
            from opentelemetry.trace import Status, StatusCode
            span.set_status(Status(StatusCode.OK))
    finally:
        # The scripts are short-lived AAP jobs; export the final report/root span
        # before process exit, including when the crew raises an exception.
        try:
            provider.force_flush(timeout_millis=5000)
        except Exception as exc:
            print(f"Phoenix trace flush failed: {type(exc).__name__}: {exc}", file=sys.stderr)
