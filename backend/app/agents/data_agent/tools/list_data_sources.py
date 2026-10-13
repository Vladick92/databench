"""Tool: discovers what data exists across all configured connectors (files, databases, ...)."""
from __future__ import annotations

from collections import Counter

from agent_framework import tool

from .connectors import list_all_sources
from .scope import active_connectors


@tool
def list_data_sources() -> str:
    """List every data source currently available: csv/xlsx files and database tables, with
    the exact name to use, which tool reads it, and its columns/types. Call this before writing
    any query whenever you don't already know a source's exact name and columns in this
    conversation - never guess them."""
    sources = list_all_sources(active_connectors())
    if not sources:
        return "No data sources are currently available."

    lines = []
    for s in sources:
        cols = ", ".join(f"{name} ({dtype})" for name, dtype in s.columns.items())
        lines.append(f"- {s.name!r} | kind={s.kind} | read with {s.tool} | columns: {cols}")

    counts = Counter(s.name for s in sources)
    shared = sorted(name for name, n in counts.items() if n > 1)
    if shared:  # a file and a database table can have the same name: they are different data
        lines.append(
            "Note: " + ", ".join(repr(n) for n in shared) + " exist as both a file and a database table. "
            "They are different data: use the tool of the kind you mean, and say which one you used."
        )
    return "\n".join(lines)
