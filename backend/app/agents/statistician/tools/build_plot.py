"""Tool: draws one chart of the query result and stores it in the workspace for the workflow to show.

The model chooses the chart and the columns; the data itself never passes through it (see
workspace.py). What comes back is a fact sheet computed from the full data, which is what the
model writes its summary from.
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal

from agent_framework import tool

from .charts import MAX_CATEGORIES, MAX_POINTS, PlotError, PlotSpec, render_plot
from .workspace import Plot, current_workspace

log = logging.getLogger(__name__)

MAX_PLOTS = 2


@tool
def build_plot(
    chart_type: Annotated[
        Literal["line", "bar", "hbar", "scatter", "histogram", "box"],
        "line: values over an ordered x such as time. bar / hbar (horizontal, for long labels): compare "
        "categories. scatter: two numeric columns. histogram: distribution of one numeric column (x). "
        "box: spread of the numeric y, optionally per x group.",
    ],
    x: Annotated[str, "Column for the x axis, or the category column, or the column to bin for a histogram."] = "",
    y: Annotated[
        str,
        "Numeric column for the y axis. bar / hbar: leave empty to count rows per x. histogram: optional "
        "count column, for rows that are already counts per value of x.",
    ] = "",
    hue: Annotated[str, "Optional low-cardinality category column: one colour per value (line, bar, hbar, scatter)."] = "",
    agg: Annotated[
        Literal["sum", "mean", "median", "count", "max", "min"],
        "How to combine rows that share the same x (and hue). Only used by line / bar / hbar with y set.",
    ] = "sum",
    sort: Annotated[
        Literal["value", "none"],
        "bar / hbar only: 'value' puts the largest category first; 'none' keeps the row order "
        "(use it for categories with a natural order such as months or age groups).",
    ] = "value",
    title: Annotated[str, "Chart title: what is shown and its scope (period, filter)."] = "",
    x_label: Annotated[str, "x axis label in plain words, with units if known. Empty = column name."] = "",
    y_label: Annotated[str, "y axis label in plain words, with units if known. Empty = column name."] = "",
    top_n: Annotated[
        int,
        f"bar / hbar / box: how many categories to draw, largest first. 0 (default) = as many as fit: "
        f"{MAX_CATEGORIES} categories, or {MAX_POINTS} points when x is a number or a date.",
    ] = 0,
) -> str:
    """Draw one chart of the query result and show it to the user. Returns facts computed from the
    full data (extremes, trend, shares, correlation, caveats) to base the written summary on."""
    workspace = current_workspace()
    if workspace is None:
        return "Error: there is no query result to plot."
    if len(workspace.plots) >= MAX_PLOTS:
        return f"Error: the limit of {MAX_PLOTS} charts is reached. Write the summary now."

    spec = PlotSpec(
        chart_type=chart_type, x=x, y=y, hue=hue, agg=agg, sort=sort,
        title=title, x_label=x_label, y_label=y_label, top_n=top_n,
    )
    try:
        plot = render_plot(workspace.df, spec)
    except PlotError as e:
        return f"Error: {e}"
    except Exception as e:  # a bug or a matplotlib failure: let the model retry or give up gracefully
        log.exception("build_plot failed for %s", spec)
        return f"Error: the chart could not be drawn ({type(e).__name__}: {e}). Try different columns or another chart type."

    workspace.plots.append(Plot(title=plot.title, png=plot.png))
    lines = [f"Chart drawn: {plot.title!r} ({chart_type}). Facts from the full data - base the summary only on these:"]
    lines += [f"- {fact}" for fact in plot.facts]
    if workspace.note:
        lines.append(f"- Note: {workspace.note}")
    return "\n".join(lines)
