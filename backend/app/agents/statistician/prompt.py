"""The statistician's input: the question plus a compact profile of the result.

No rows, on purpose. build_plot draws from the full data held in the workspace, so the prompt
stays small however many rows there are, and the summary can only rest on the fact sheet build_plot
returns - not on whatever handful of rows would have been pasted here (it once cited a "peak" from
a five-row sample that other months matched too).
"""
from __future__ import annotations

import pandas as pd

from .tools.format import fmt_label, fmt_num, is_datetime, is_numeric, shorten
from .tools.workspace import Workspace

_MAX_COLUMNS = 30
_SQL_CHARS = 600


def _describe(s: pd.Series) -> str:
    missing = int(s.isna().sum())
    tail = f", {missing} missing" if missing else ""
    if s.dropna().empty:
        return "empty"
    if is_numeric(s):
        return f"number, min {fmt_num(s.min())}, median {fmt_num(s.median())}, max {fmt_num(s.max())}{tail}"
    if is_datetime(s):
        return f"date/time, {fmt_label(s.min())} to {fmt_label(s.max())}, {s.nunique()} distinct{tail}"
    counts = s.dropna().astype(str).value_counts()
    if counts.max() == 1:  # ids, names: listing examples would only burn tokens
        return f"text, all {len(counts)} values unique{tail}"
    common = ", ".join(f"{shorten(v, 20)} ({n})" for v, n in counts.head(3).items())
    return f"text, {s.nunique()} distinct; most common: {common}{tail}"


def build_prompt(question: str, workspace: Workspace, *, sql: str = "") -> str:
    df = workspace.df
    lines = [f"User question: {question}"]
    if sql:
        lines.append(f"SQL that produced the data: {sql[:_SQL_CHARS]}")
    size = f"Result: {len(df)} rows x {df.shape[1]} columns."
    lines.append(f"{size} {workspace.note}".strip())
    lines.append("Columns:")
    lines += [f"- {col}: {_describe(df[col])}" for col in df.columns[:_MAX_COLUMNS]]
    if df.shape[1] > _MAX_COLUMNS:
        lines.append(f"- ... and {df.shape[1] - _MAX_COLUMNS} more columns")
    return "\n".join(lines)
