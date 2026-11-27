"""Offline image checks: no credentials, LLM requests or cluster access required."""
import platform
import sqlite3
import subprocess
from importlib.metadata import version

from crewai import Agent, Crew, LLM, Task
from dotenv import load_dotenv
from mcp import ClientSession
from openai import OpenAI
from openinference.instrumentation.crewai import CrewAIInstrumentor
from openinference.instrumentation.openai import OpenAIInstrumentor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

assert platform.machine() == "x86_64", "Build this EE on native x86_64 Linux."
assert sqlite3.sqlite_version_info >= (3, 35, 0)
assert version("crewai") == "1.15.17"
assert version("openai") == "2.54.0"
subprocess.run(["git", "--version"], check=True)
print("CrewAI EE imports, x86_64 architecture and SQLite checks passed.")
