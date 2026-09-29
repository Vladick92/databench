"""Formatting and dtype helpers shared by the chart code and the statistician's prompt."""
from __future__ import annotations

import numpy as np
import pandas as pd


def is_numeric(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def is_datetime(s: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(s)


def fmt_num(v) -> str:
    v = float(v)
    if v == int(v) and abs(v) < 1e15:
        return f"{int(v):,}"
    return f"{v:,.1f}" if abs(v) >= 100 else f"{v:.3g}"


def fmt_label(v) -> str:
    if isinstance(v, pd.Timestamp):
        midnight = (v.hour, v.minute, v.second) == (0, 0, 0)
        return v.strftime("%Y-%m-%d" if midnight else "%Y-%m-%d %H:%M")
    if isinstance(v, (int, float, np.integer, np.floating)):
        return fmt_num(v)
    return str(v)


def shorten(text: str, limit: int = 24) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
