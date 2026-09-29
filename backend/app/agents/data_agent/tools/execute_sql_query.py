"""Tool: runs read-only SQL against the connected Postgres database.

Validation (sql_guard) is one layer; the DB connection itself is a second, independent layer -
`conn.read_only = True` puts every statement in a READ ONLY transaction at the Postgres level,
and a statement_timeout stops a runaway query (e.g. `SELECT pg_sleep(...)`, which is a legal
read and passes validation) from hanging the agent. The DSN itself should point at a role with
only SELECT grants (see docs/PLAN.md) - least privilege in case both of the above ever have a gap.
"""
from __future__ import annotations

from typing import Annotated

import pandas as pd
import psycopg
from agent_framework import tool

from backend.app import config
from .results import finish_query
from .sql_guard import SQLValidationError, validate_readonly_sql


@tool
def execute_sql_query(
    sql: Annotated[str, "A single read-only SELECT statement, Postgres SQL dialect."],
) -> str:
    """Run a read-only SQL query against the connected Postgres database. Call list_data_sources
    first to get exact table/column names - do not guess either."""
    if not config.POSTGRES_DSN:
        return "Error: no database is configured. Only file sources are available right now."

    try:
        validated = validate_readonly_sql(sql, dialect="postgres")
    except SQLValidationError as e:
        return f"Rejected: {e}"

    try:
        with psycopg.connect(config.POSTGRES_DSN, connect_timeout=5) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {config.POSTGRES_STATEMENT_TIMEOUT_MS}")
                cur.execute(validated.sql)
                columns = [d.name for d in cur.description] if cur.description else []
                rows = cur.fetchmany(config.MAX_ROWS_RETURNED + 1)
    except psycopg.Error as e:
        return f"Error running query: {e}"

    # Fetching stopped at cap + 1 rows, so the true total is only known if the result was shorter.
    return finish_query(
        pd.DataFrame(rows, columns=columns),
        sql=validated.sql,
        exact_total=len(rows) <= config.MAX_ROWS_RETURNED,
    )
