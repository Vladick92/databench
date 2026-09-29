"""Chart drawing for build_plot. Pure functions: a DataFrame and a PlotSpec in, PNG bytes and a
fact sheet out - no agent-framework imports, so it can be tested (and reused) on its own.

The fact sheet is what the model writes its summary from: numbers computed here from the full
data (extremes, trend, shares, correlation), so the summary never rests on a preview or a guess.

Draws with matplotlib's object-oriented Figure API, not pyplot: pyplot keeps global state and a
GUI backend, and the framework runs sync tools on worker threads.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

from .format import fmt_label, fmt_num, is_datetime, is_numeric, shorten

MAX_CATEGORIES = 20  # bars / boxes drawn on a category axis; the largest are kept
MAX_POINTS = 120  # bars drawn when x is a number or a date: every point matters there, none is dropped early
MAX_SERIES = 8  # colours (hue groups) drawn; the largest are kept
CHART_TYPES = ("line", "bar", "hbar", "scatter", "histogram", "box")
AGGREGATIONS = ("sum", "mean", "median", "count", "max", "min")

_PALETTE = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2", "#B279A2", "#FF9DA6", "#9D755D"]
_ADDITIVE = ("sum", "count")  # aggregations for which "share of total" is meaningful


class PlotError(ValueError):
    """The request can't be drawn. The message goes back to the model verbatim, so it says how to fix the call."""


@dataclass(frozen=True)
class PlotSpec:
    chart_type: str
    x: str = ""
    y: str = ""
    hue: str = ""
    agg: str = "sum"
    sort: str = "value"  # bar/hbar: "value" = largest first, "none" = keep the row order
    title: str = ""
    x_label: str = ""
    y_label: str = ""
    top_n: int = 0  # bar/hbar/box: categories to draw; 0 = as many as the axis allows


@dataclass(frozen=True)
class RenderedPlot:
    title: str
    png: bytes
    facts: list[str] = field(default_factory=list)


# --- small helpers ---------------------------------------------------------------------------


def _compact(v: float, _pos=None) -> str:
    """Axis tick text: 1500 -> 1.5K, 2_000_000 -> 2M."""
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= limit:
            return f"{v / limit:.3g}{suffix}"
    return f"{v:g}"


def _column(df: pd.DataFrame, name: str, role: str) -> str:
    """Resolve the model's column name (exact, then case-insensitive) or say which columns exist."""
    if name in df.columns:
        return name
    by_lower = {str(c).lower(): c for c in df.columns}
    if name.lower() in by_lower:
        return by_lower[name.lower()]
    raise PlotError(f"{role} column '{name}' does not exist. Columns: {', '.join(map(str, df.columns))}")


def _numeric_column(df: pd.DataFrame, name: str, role: str) -> str:
    col = _column(df, name, role)
    if not is_numeric(df[col]):
        raise PlotError(f"{role} column '{col}' is not numeric (type {df[col].dtype}); pick a numeric column.")
    return col


def _drop_missing(df: pd.DataFrame, columns: list[str], required: list[str]) -> tuple[pd.DataFrame, str | None]:
    columns = list(dict.fromkeys(columns))
    data = df[columns].dropna(subset=required)
    if data.empty:
        raise PlotError(f"no rows left: every row has a missing value in {', '.join(required)}.")
    dropped = len(df) - len(data)
    return data, (f"Note: {dropped} rows with a missing value in {', '.join(required)} were left out." if dropped else None)


def _style(ax, title: str, x_label: str, y_label: str, grid_axis: str = "y") -> None:
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold", pad=12)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis=grid_axis, color="black", alpha=0.12, linewidth=0.8)
    ax.set_axisbelow(True)


def _strength(r: float) -> str:
    size = abs(r)
    word = "very weak or no" if size < 0.2 else "weak" if size < 0.4 else "moderate" if size < 0.6 else "strong" if size < 0.8 else "very strong"
    return f"{word} {'positive' if r > 0 else 'negative'}" if size >= 0.2 else word


def _trend(values: np.ndarray) -> str:
    if len(values) < 3 or np.ptp(values) == 0:
        return "flat" if len(values) >= 3 else "too few points"
    r = float(np.corrcoef(np.arange(len(values)), values)[0, 1])
    return f"rising (r={r:.2f})" if r >= 0.6 else f"falling (r={r:.2f})" if r <= -0.6 else f"no clear direction (r={r:.2f})"


def _split_series(data: pd.DataFrame, hue: str, value: str) -> tuple[list[tuple[str, pd.DataFrame]], list[str]]:
    """One (name, rows) per hue group, largest first, at most MAX_SERIES; plus any note."""
    if not hue:
        return [("", data)], []
    groups = sorted(data.groupby(hue, sort=False, observed=True), key=lambda kv: -kv[1][value].abs().sum())
    notes = []
    if len(groups) > MAX_SERIES:
        notes.append(f"Note: '{hue}' has {len(groups)} values; only the {MAX_SERIES} largest are drawn.")
        groups = groups[:MAX_SERIES]
    return [(str(name), sub) for name, sub in groups], notes


# --- chart types -----------------------------------------------------------------------------


def _draw_line(ax, df: pd.DataFrame, spec: PlotSpec) -> tuple[str, list[str]]:
    if not spec.x or not spec.y:
        raise PlotError("a line chart needs both x and y.")
    x, y = _column(df, spec.x, "x"), _numeric_column(df, spec.y, "y")
    hue = _column(df, spec.hue, "hue") if spec.hue else ""
    if x == y:
        raise PlotError("x and y must be different columns.")
    data, note = _drop_missing(df, [x, y, hue] if hue else [x, y], [x, y])
    facts = [note] if note else []

    keys = [x] + ([hue] if hue else [])
    aggregated = bool(data.duplicated(subset=keys).any())
    data = data.groupby(keys, sort=False, observed=True)[y].agg(spec.agg).reset_index()
    if aggregated:
        facts.append(f"Note: rows sharing the same {x} were combined with {spec.agg}.")
    if is_numeric(data[x]) or is_datetime(data[x]):
        data = data.sort_values(keys)

    series, notes = _split_series(data, hue, y)
    facts += notes
    for i, (name, sub) in enumerate(series):
        ax.plot(
            sub[x], sub[y], color=_PALETTE[i], linewidth=2, label=name or None,
            marker="o" if len(sub) <= 25 else None, markersize=4,
        )
    if hue:
        ax.legend(title=hue, frameon=False, fontsize=9)
    if not (is_numeric(data[x]) or is_datetime(data[x])) and data[x].nunique() > 15:
        ax.xaxis.set_major_locator(MaxNLocator(nbins=12, integer=True))
    ax.tick_params(axis="x", labelrotation=30)
    ax.yaxis.set_major_formatter(FuncFormatter(_compact))

    for name, sub in series[:5]:
        vals, xs = sub[y].to_numpy(dtype=float), sub[x].tolist()
        first, last = vals[0], vals[-1]
        change = f"{(last - first) / abs(first) * 100:+.0f}%" if first != 0 else "n/a"
        hi, lo = int(np.argmax(vals)), int(np.argmin(vals))
        prefix = f"{name}: " if name else ""
        facts.append(
            f"{prefix}{len(vals)} points from {fmt_label(xs[0])} to {fmt_label(xs[-1])}; start {fmt_num(first)}, "
            f"end {fmt_num(last)} ({change}); peak {fmt_num(vals[hi])} at {fmt_label(xs[hi])}; "
            f"low {fmt_num(vals[lo])} at {fmt_label(xs[lo])}; trend {_trend(vals)}."
        )

    y_label = spec.y_label or (f"{y} ({spec.agg})" if aggregated else y)
    title = spec.title or f"{y} by {x}"
    _style(ax, title, spec.x_label or x, y_label)
    return title, facts


def _draw_bars(ax, df: pd.DataFrame, spec: PlotSpec, horizontal: bool) -> tuple[str, list[str]]:
    if not spec.x:
        raise PlotError("a bar chart needs x (the category column).")
    x = _column(df, spec.x, "x")
    y = _numeric_column(df, spec.y, "y") if spec.y else ""
    hue = _column(df, spec.hue, "hue") if spec.hue else ""
    data, note = _drop_missing(df, [x, y, hue] if hue else ([x, y] if y else [x]), [x] + ([y] if y else []))
    facts = [note] if note else []

    keys = [x] + ([hue] if hue else [])
    grouped = data.groupby(keys, sort=False, observed=True)
    value = grouped[y].agg(spec.agg) if y else grouped.size()
    additive = not y or spec.agg in _ADDITIVE
    aggregated = bool(y and data.duplicated(subset=keys).any())
    if aggregated:
        facts.append(f"Note: rows sharing the same {x} were combined with {spec.agg}.")

    table = value.unstack(hue) if hue else value.to_frame("value")
    if additive:
        table = table.fillna(0)
    if hue:
        series_totals = table.sum().sort_values(ascending=False)
        if len(series_totals) > MAX_SERIES:
            facts.append(f"Note: '{hue}' has {len(series_totals)} values; only the {MAX_SERIES} largest are drawn.")
            table = table[series_totals.index[:MAX_SERIES]]
    totals = table.sum(axis=1)
    grand_total = float(totals.sum())

    natural = is_numeric(data[x]) or is_datetime(data[x])
    limit = MAX_POINTS if natural else MAX_CATEGORIES
    n = limit if spec.top_n <= 0 else min(int(spec.top_n), limit)
    n_all = len(table)
    if n_all > n:
        table = table.loc[totals.nlargest(n).index]
        facts.append(f"Note: {n_all} categories; only the {n} largest are drawn.")
    if natural:
        table = table.sort_index()
    elif spec.sort == "value":
        table = table.loc[table.sum(axis=1).sort_values(ascending=False).index]
    totals = table.sum(axis=1)

    labels = [shorten(fmt_label(k)) for k in table.index]
    pos = np.arange(len(table))
    draw = ax.barh if horizontal else ax.bar
    if hue:
        width = 0.8 / len(table.columns)
        for i, col in enumerate(table.columns):
            offset = pos - 0.4 + width * (i + 0.5)
            draw(offset, table[col].to_numpy(dtype=float), width, color=_PALETTE[i], label=str(col))
        ax.legend(title=hue, frameon=False, fontsize=9)
    else:
        bars = draw(pos, totals.to_numpy(dtype=float), 0.7, color=_PALETTE[0])
        if len(table) <= 15:
            ax.bar_label(bars, labels=[_compact(v) for v in totals], padding=2, fontsize=8)

    crowded = len(table) > 6 or max(map(len, labels)) > 8
    step = max(1, -(-len(table) // 20))  # show at most ~20 tick labels, however many bars there are
    if horizontal:
        ax.set_yticks(pos[::step], labels[::step])
        ax.xaxis.set_major_formatter(FuncFormatter(_compact))
        ax.invert_yaxis()  # first (largest) category on top
    else:
        ax.set_xticks(pos[::step], labels[::step], rotation=30 if crowded else 0, ha="right" if crowded else "center")
        ax.yaxis.set_major_formatter(FuncFormatter(_compact))

    shown = totals.sort_values(ascending=False)
    top = [f"{fmt_label(k)}: {fmt_num(v)}" + (f" ({v / grand_total * 100:.0f}%)" if additive and grand_total else "") for k, v in shown.head(3).items()]
    low_k, low_v = shown.index[-1], shown.iloc[-1]
    facts.append(f"{len(table)} categories drawn. Highest: {'; '.join(top)}. Lowest: {fmt_label(low_k)}: {fmt_num(low_v)}.")
    if additive:
        facts.append(f"Total over all categories: {fmt_num(grand_total)}.")
    if hue:
        lead = table.sum().sort_values(ascending=False)
        facts.append("Series totals: " + "; ".join(f"{c}: {fmt_num(v)}" for c, v in lead.head(5).items()) + ".")

    if spec.y_label:
        value_name = spec.y_label
    elif not y:
        value_name = "Count"
    else:
        value_name = f"{y} ({spec.agg})" if aggregated else y
    title = spec.title or f"{value_name} by {x}"
    x_label, y_label = (value_name, spec.x_label or x) if horizontal else (spec.x_label or x, value_name)
    _style(ax, title, x_label, y_label, grid_axis="x" if horizontal else "y")
    return title, facts


def _draw_scatter(ax, df: pd.DataFrame, spec: PlotSpec) -> tuple[str, list[str]]:
    if not spec.x or not spec.y:
        raise PlotError("a scatter chart needs both x and y (two numeric columns).")
    x, y = _numeric_column(df, spec.x, "x"), _numeric_column(df, spec.y, "y")
    hue = _column(df, spec.hue, "hue") if spec.hue else ""
    if x == y:
        raise PlotError("x and y must be different columns.")
    data, note = _drop_missing(df, [x, y, hue] if hue else [x, y], [x, y])
    facts = [note] if note else []

    series, notes = _split_series(data.assign(_w=1.0), hue, "_w")
    facts += notes
    for i, (name, sub) in enumerate(series):
        ax.scatter(sub[x], sub[y], s=26, alpha=0.75, color=_PALETTE[i], edgecolors="none", label=name or None)
    if hue:
        ax.legend(title=hue, frameon=False, fontsize=9)
    ax.xaxis.set_major_formatter(FuncFormatter(_compact))
    ax.yaxis.set_major_formatter(FuncFormatter(_compact))

    if len(data) >= 3 and data[x].std() > 0 and data[y].std() > 0:
        r = float(np.corrcoef(data[x], data[y])[0, 1])
        facts.append(f"{len(data)} points; correlation r={r:.2f} ({_strength(r)} linear relationship).")
    else:
        facts.append(f"{len(data)} points; correlation not defined (too few points or no variation).")
    facts.append(f"{x} ranges {fmt_num(data[x].min())} to {fmt_num(data[x].max())}; {y} ranges {fmt_num(data[y].min())} to {fmt_num(data[y].max())}.")

    title = spec.title or f"{y} vs {x}"
    _style(ax, title, spec.x_label or x, spec.y_label or y)
    return title, facts


def _weighted_quantiles(values: np.ndarray, weights: np.ndarray, qs: list[float]) -> np.ndarray:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    position = (np.cumsum(w) - 0.5 * w) / w.sum()
    return np.interp(qs, position, v)


def _draw_histogram(ax, df: pd.DataFrame, spec: PlotSpec) -> tuple[str, list[str]]:
    """Distribution of x. y is an optional count/weight column, for rows that are already counts per value."""
    if not spec.x:
        raise PlotError("a histogram needs x (the numeric column to bin).")
    x = _numeric_column(df, spec.x, "x")
    weight = _numeric_column(df, spec.y, "y") if spec.y else ""
    needed = [x, weight] if weight else [x]
    data, note = _drop_missing(df, needed, needed)
    facts = [note] if note else []
    if spec.hue:
        facts.append("Note: hue is not used by histograms and was ignored.")
    values = data[x].to_numpy(dtype=float)
    weights = data[weight].to_numpy(dtype=float) if weight else np.ones(len(values))
    if (weights < 0).any() or weights.sum() == 0:
        raise PlotError(f"the count column '{weight}' must be non-negative and not all zero.")
    total = float(weights.sum())

    if weight:  # numpy can't pick a bin count for weighted data: Sturges' rule on the total weight
        bins = int(np.clip(np.ceil(np.log2(max(total, 2))) + 1, 5, 40))
    else:
        bins = int(np.clip(len(np.histogram_bin_edges(values, bins="auto")) - 1, 5, 40))
    counts, edges = np.histogram(values, bins=bins, weights=weights)
    ax.hist(edges[:-1], bins=edges, weights=counts, color=_PALETTE[0], edgecolor="white")
    ax.xaxis.set_major_formatter(FuncFormatter(_compact))
    ax.yaxis.set_major_formatter(FuncFormatter(_compact))

    q1, med, q3 = _weighted_quantiles(values, weights, [0.25, 0.5, 0.75])
    iqr = q3 - q1
    outliers = float(weights[(values < q1 - 1.5 * iqr) | (values > q3 + 1.5 * iqr)].sum()) if iqr > 0 else 0.0
    mean = float(np.average(values, weights=weights))
    std = float(np.sqrt(np.average((values - mean) ** 2, weights=weights)))
    skew = "roughly symmetric" if std == 0 or abs(mean - med) < 0.1 * std else ("right-skewed (long tail of high values)" if mean > med else "left-skewed (long tail of low values)")
    top = int(np.argmax(counts))
    what = f"{fmt_num(total)} values" if not weight else f"{fmt_num(total)} in total ({weight}) over {len(values)} distinct {x} values"
    facts.append(f"{what}; mean {fmt_num(mean)}, median {fmt_num(med)}, std {fmt_num(std)}, middle half {fmt_num(q1)} to {fmt_num(q3)}, range {fmt_num(values.min())} to {fmt_num(values.max())}.")
    facts.append(
        f"Most common range: {fmt_num(edges[top])} to {fmt_num(edges[top + 1])} ({counts[top] / total * 100:.0f}% of the total); "
        f"distribution is {skew}; {fmt_num(outliers)} outliers by the 1.5*IQR rule."
    )

    title = spec.title or f"Distribution of {x}"
    _style(ax, title, spec.x_label or x, spec.y_label or (weight or "Count"))
    return title, facts


def _draw_box(ax, df: pd.DataFrame, spec: PlotSpec) -> tuple[str, list[str]]:
    if not spec.y:
        raise PlotError("a box plot needs y (the numeric column); x is an optional grouping column.")
    y = _numeric_column(df, spec.y, "y")
    x = _column(df, spec.x, "x") if spec.x else ""
    data, note = _drop_missing(df, [x, y] if x else [y], [x, y] if x else [y])
    facts = [note] if note else []
    if spec.hue:
        facts.append("Note: hue is not used by box plots and was ignored.")

    if x:
        n = MAX_CATEGORIES if spec.top_n <= 0 else min(int(spec.top_n), MAX_CATEGORIES)
        groups = list(data.groupby(x, sort=False, observed=True)[y])
        groups.sort(key=lambda kv: -len(kv[1]))
        if len(groups) > n:
            facts.append(f"Note: {len(groups)} groups; only the {n} largest are drawn.")
            groups = groups[:n]
        if spec.sort == "value":
            groups.sort(key=lambda kv: -kv[1].median())
        labels = [shorten(fmt_label(k)) for k, _ in groups]
        arrays = [g.to_numpy(dtype=float) for _, g in groups]
    else:
        labels, arrays = [y], [data[y].to_numpy(dtype=float)]

    box = ax.boxplot(arrays, tick_labels=labels, patch_artist=True, widths=0.6, flierprops={"markersize": 3, "alpha": 0.5})
    for patch in box["boxes"]:
        patch.set(facecolor=_PALETTE[0], alpha=0.55, edgecolor="#333333")
    for median in box["medians"]:
        median.set(color="#222222", linewidth=1.6)
    ax.yaxis.set_major_formatter(FuncFormatter(_compact))
    if len(labels) > 6 or max(map(len, labels)) > 8:
        ax.tick_params(axis="x", labelrotation=30)
        for label in ax.get_xticklabels():
            label.set_ha("right")

    outliers = 0
    for arr in arrays:
        q1, q3 = np.percentile(arr, [25, 75])
        iqr = q3 - q1
        outliers += int(((arr < q1 - 1.5 * iqr) | (arr > q3 + 1.5 * iqr)).sum()) if iqr > 0 else 0
    ranked = sorted(zip(labels, arrays), key=lambda la: -float(np.median(la[1])))
    for label, arr in ranked[:5]:
        q1, med, q3 = np.percentile(arr, [25, 50, 75])
        facts.append(f"{label}: n={len(arr)}, median {fmt_num(med)}, middle half {fmt_num(q1)} to {fmt_num(q3)}, range {fmt_num(arr.min())} to {fmt_num(arr.max())}.")
    facts.append(f"{outliers} outliers in total by the 1.5*IQR rule.")

    title = spec.title or (f"{y} by {x}" if x else f"Spread of {y}")
    _style(ax, title, spec.x_label or x, spec.y_label or y)
    return title, facts


def render_plot(df: pd.DataFrame, spec: PlotSpec) -> RenderedPlot:
    if spec.chart_type not in CHART_TYPES:
        raise PlotError(f"unknown chart_type '{spec.chart_type}'. Use one of: {', '.join(CHART_TYPES)}.")
    if spec.agg not in AGGREGATIONS:
        raise PlotError(f"unknown agg '{spec.agg}'. Use one of: {', '.join(AGGREGATIONS)}.")

    fig = Figure(figsize=(9, 5), dpi=120, layout="constrained")
    ax = fig.subplots()
    drawers = {
        "line": _draw_line,
        "bar": lambda a, d, s: _draw_bars(a, d, s, horizontal=False),
        "hbar": lambda a, d, s: _draw_bars(a, d, s, horizontal=True),
        "scatter": _draw_scatter,
        "histogram": _draw_histogram,
        "box": _draw_box,
    }
    title, facts = drawers[spec.chart_type](ax, df, spec)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    return RenderedPlot(title=title, png=buffer.getvalue(), facts=facts)
