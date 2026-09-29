"""The statistician's per-run state: the dataset build_plot draws from and the charts it made.

The model never receives the data as text. It only picks columns and a chart type; the tool draws
from the real DataFrame held here. Same per-request ContextVar pattern as the data agent's result
capture (data_agent/tools/results.py) - and, as there, nothing resets it: each request runs in its
own context, which is discarded when the request ends.
"""
from __future__ import annotations

import datetime as dt
from contextvars import ContextVar
from dataclasses import dataclass, field

import pandas as pd

_ISO_DATETIME = r"^\d{4}-\d{2}(-\d{2})?([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$"


@dataclass(frozen=True)
class Plot:
    title: str
    png: bytes


@dataclass
class Workspace:
    df: pd.DataFrame  # already type-coerced, see coerce_types
    note: str = ""  # caveat about the data itself (e.g. cut at the row cap), repeated in build_plot's output
    plots: list[Plot] = field(default_factory=list)


_current: ContextVar[Workspace | None] = ContextVar("statistician_workspace", default=None)


def coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Give text columns the type they really hold. Postgres returns Decimal and date objects and
    CSV-backed files return ISO dates as plain strings; charts need real numbers and datetimes.
    A column is converted only if every non-empty value converts."""
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if not (pd.api.types.is_string_dtype(s) or pd.api.types.is_object_dtype(s)):
            continue
        present = s.dropna()
        if present.empty:
            continue
        numeric = pd.to_numeric(s, errors="coerce")
        if numeric.notna().sum() == len(present):
            out[col] = numeric
            continue
        if isinstance(present.iloc[0], (dt.date, dt.datetime)):
            parsed = pd.to_datetime(s, errors="coerce")
        elif present.astype(str).str.match(_ISO_DATETIME).all():
            parsed = pd.to_datetime(s, errors="coerce", format="ISO8601")
        else:
            continue
        if parsed.notna().sum() == len(present):
            out[col] = parsed
    return out


def open_workspace(df: pd.DataFrame, *, note: str = "") -> Workspace:
    workspace = Workspace(df=coerce_types(df), note=note)
    _current.set(workspace)
    return workspace


def current_workspace() -> Workspace | None:
    return _current.get()
