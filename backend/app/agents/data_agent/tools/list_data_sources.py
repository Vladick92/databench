"""Tool: discovers what data exists across all configured connectors (files, databases, ...)."""
from __future__ import annotations

from agent_framework import tool

from backend.app import config
from .connectors import list_all_sources


@tool
def list_data_sources() -> str:
    """List every data source currently available: csv/xlsx files and database tables, with
    the exact name to use, which tool reads it, and its columns/types. Call this before writing
    any query whenever you don't already know a source's exact name and columns in this
    conversation - never guess them."""
    sources = list_all_sources(config.CONNECTORS)
    if not sources:
        return "No data sources are currently available."

    lines = []
    for s in sources:
        cols = ", ".join(f"{name} ({dtype})" for name, dtype in s.columns.items())
        lines.append(f"- {s.name!r} | kind={s.kind} | read with {s.tool} | columns: {cols}")
    return "\n".join(lines)
