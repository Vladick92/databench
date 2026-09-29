"""What a successful query hands back, split two ways.

- The model gets the whole result if it is small (up to config.CHART_MIN_ROWS rows). For a bigger
  one it gets a compact profile instead of rows: the total, each column's type and range, and the
  first row. A big table in its context would blow Groq's free-tier limit of 8k tokens per minute,
  and a model that is shown rows pastes them into its answer whatever its instructions say.
- The workflow gets every retrieved row (up to config.MAX_ROWS_RETURNED) through a per-request
  capture, so the statistician's plotting code sees the real data without it ever passing
  through an LLM prompt.

Both execute_* tools end with `finish_query`; nothing else formats or records results.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

import pandas as pd

from backend.app import config

_CLIP_CELL_CHARS = 200  # long text cells (chat messages, descriptions) would dominate the output
_MAX_PROFILE_COLUMNS = 30


@dataclass(frozen=True)
class QueryResult:
    df: pd.DataFrame  # at most config.MAX_ROWS_RETURNED rows
    sql: str
    total_rows: int | None  # exact count of matching rows, or None when we stopped fetching at the cap
    truncated: bool  # more rows matched than were retrieved


_capture: ContextVar[list[QueryResult] | None] = ContextVar("query_results", default=None)


def start_capture() -> list[QueryResult]:
    """Start recording query results for the current request and return the list they land in.

    Sync tools run on worker threads via asyncio.to_thread, which copies the caller's context, so
    the tools see this variable and append to this very list. Nothing resets it afterwards: every
    request runs in its own context, which is discarded when the request ends.
    """
    results: list[QueryResult] = []
    _capture.set(results)
    return results


def _unique_columns(names) -> list[str]:
    """A join can return two columns with the same name; DuckDB renames the second (id, id_1),
    Postgres does not. Everything downstream indexes columns by name, so make them unique here."""
    unique: list[str] = []
    for name in map(str, names):
        candidate, i = name, 0
        while candidate in unique:
            i += 1
            candidate = f"{name}_{i}"
        unique.append(candidate)
    return unique


def _clip(value):
    if isinstance(value, str) and len(value) > _CLIP_CELL_CHARS:
        return value[:_CLIP_CELL_CHARS] + "…"
    return value


def _number(v) -> str:
    v = float(v)
    return str(int(v)) if v.is_integer() else f"{v:.4f}".rstrip("0").rstrip(".")


def _profile_line(name: str, s: pd.Series) -> str:
    if s.dtype == object:  # Postgres hands back Decimal (numeric) and date objects
        as_number = pd.to_numeric(s, errors="coerce")
        if s.notna().any() and as_number.notna().sum() == s.notna().sum():
            s = as_number
    if s.dropna().empty:
        return f"- {name}: all empty"
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return f"- {name}: number, {_number(s.min())} to {_number(s.max())}"
    if pd.api.types.is_datetime64_any_dtype(s):
        return f"- {name}: date/time, {s.min():%Y-%m-%d} to {s.max():%Y-%m-%d}"
    values = s.dropna().astype(str)
    if values.nunique() == len(values):  # ids, names: listing examples would only burn tokens
        return f"- {name}: text, all {len(values)} values unique"
    common = ", ".join(str(_clip(v))[:30] for v in values.value_counts().head(3).index)
    return f"- {name}: text, {values.nunique()} distinct (most common: {common})"


def _summary(df: pd.DataFrame, total: int | None, truncated: bool) -> str:
    cap = config.MAX_ROWS_RETURNED
    if not truncated:
        size = f"{len(df)} rows in total"
    elif total is not None:
        size = f"{total} rows matched; only the first {cap} were retrieved"
    else:
        size = f"more than {cap} rows matched; only the first {cap} were retrieved"
    lines = [f"{size}. Too many to list: answer in two or three sentences of prose, no table, no mention of charts. Columns:"]
    lines += [_profile_line(str(c), df[c]) for c in df.columns[:_MAX_PROFILE_COLUMNS]]
    if len(df.columns) > _MAX_PROFILE_COLUMNS:
        lines.append(f"- ... and {len(df.columns) - _MAX_PROFILE_COLUMNS} more columns")
    lines.append("First row: " + ", ".join(f"{c}={_clip(v)}" for c, v in df.iloc[0].items()))
    return "\n".join(lines) + "\n"


def finish_query(df: pd.DataFrame, *, sql: str, exact_total: bool) -> str:
    """Cap, record and render one successful result; returns the text the model sees.

    `df` is everything the query produced (DuckDB) or everything fetched (Postgres, which stops at
    cap + 1 rows). `exact_total` says whether len(df) is the true number of matching rows.
    """
    cap = config.MAX_ROWS_RETURNED
    truncated = len(df) > cap
    kept = df.head(cap).reset_index(drop=True).set_axis(_unique_columns(df.columns), axis=1)
    total = len(df) if exact_total else None

    sink = _capture.get()
    if sink is not None:
        sink.append(QueryResult(df=kept, sql=sql, total_rows=total, truncated=truncated))

    if len(kept) > config.CHART_MIN_ROWS:
        return _summary(kept, total, truncated)
    return f"{len(kept)} rows:\n" + kept.map(_clip).to_csv(index=False, float_format="%.10g")
