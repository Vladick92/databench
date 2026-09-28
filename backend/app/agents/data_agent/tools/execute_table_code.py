"""Tool: runs read-only SQL against a csv/xlsx file source, via an in-process DuckDB connection.

Each call loads the file fresh into a throwaway in-memory DuckDB connection and registers it
under `source_name` - simplest correct thing for small dev-sized files (see docs/PLAN.md's
"start smaller to bigger" note). Nothing is written back to the file: even if a write slipped
past validate_readonly_sql, it would land in a connection that's closed and discarded right after.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

import duckdb
import pandas as pd
from agent_framework import tool

from backend.app import config
from .connectors import EXECUTE_TABLE_CODE, DataSource, list_all_sources
from .sql_guard import SQLValidationError, validate_readonly_sql

_READERS = {".csv": pd.read_csv, ".xlsx": pd.read_excel, ".xls": pd.read_excel}


def _find_source(name: str) -> DataSource | None:
    for source in list_all_sources(config.CONNECTORS):
        if source.name == name and source.tool == EXECUTE_TABLE_CODE:
            return source
    return None


def _format_result(df: pd.DataFrame) -> str:
    total = len(df)
    truncated = df.head(config.MAX_ROWS_RETURNED)
    text = truncated.to_csv(index=False)
    if total > len(truncated):
        text += f"... ({total} rows total, showing first {len(truncated)})\n"
    return text


@tool
def execute_table_code(
    source_name: Annotated[str, "Exact file source name from list_data_sources, e.g. 'customers'."],
    sql: Annotated[str, "A single read-only SELECT statement, DuckDB SQL dialect, over `source_name` as the table."],
) -> str:
    """Run a read-only SQL query against a csv/xlsx file source. Call list_data_sources first
    to get the exact source name and its columns - do not guess either."""
    source = _find_source(source_name)
    if source is None:
        return f"Error: no file source named '{source_name}'. Call list_data_sources to see what's available."

    try:
        validated = validate_readonly_sql(sql, dialect="duckdb")
    except SQLValidationError as e:
        return f"Rejected: {e}"

    reader = _READERS[Path(source.location).suffix.lower()]
    try:
        df = reader(source.location)
    except Exception as e:
        return f"Error reading {source.location}: {e}"

    con = duckdb.connect(":memory:")
    try:
        con.register(source_name, df)
        result = con.execute(validated.sql).fetchdf()
    except Exception as e:
        return f"Error running query: {e}"
    finally:
        con.close()

    return _format_result(result)
