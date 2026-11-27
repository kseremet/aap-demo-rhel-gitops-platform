"""Use the bundled SQLite required by ChromaDB on the RHEL 9 base image."""
import sys
import pysqlite3

sys.modules["sqlite3"] = pysqlite3
