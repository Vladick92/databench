"""Shared read-only SQL validator used by both execute_sql_query and execute_table_code.

One validator for both because both tools take generated SQL as input - execute_sql_query
runs it against Postgres, execute_table_code runs it (DuckDB dialect) against a file loaded
into an in-process DuckDB connection. The rules that matter (single statement, SELECT-only,
no writes) don't depend on which engine runs the query.

Deliberately NOT exposed as its own LLM-callable tool: a separate "validate_query" tool the
model could choose to call before executing would only be as safe as the model remembering to
call it. Both execute_* tools call `validate_readonly_sql` themselves before running anything.
"""
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

# Node types that indicate a write, a schema change, or an escape hatch out of pure SQL,
# wherever they appear in the parsed tree - not just at the root. A writable CTE like
# `WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x` has a Select at the root but a
# Delete nested inside, so a root-type-only check is not enough.
_FORBIDDEN_NODE_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
    exp.Copy,
    exp.Merge,
    exp.TruncateTable,
    exp.Grant,
    exp.Pragma,
)

# A query rooted in one of these is a read. Anything else (Insert, Update, Command, Pragma,
# a dialect-specific DDL node sqlglot falls back to parsing as Command, ...) is rejected.
_ALLOWED_ROOT_TYPES: tuple[type[exp.Expression], ...] = (exp.Select, exp.Union)


class SQLValidationError(ValueError):
    """Raised when generated SQL fails the read-only check. The message is safe to show the model."""


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    statement: exp.Expression


def validate_readonly_sql(sql: str, dialect: str) -> ValidatedQuery:
    """Parse `sql` under `dialect` and raise SQLValidationError unless it is a single,
    plain read (no writes, no DDL, no locking reads, no SELECT INTO, no multi-statement
    smuggling via `;` or trailing comments).
    """
    sql = sql.strip()
    if not sql:
        raise SQLValidationError("Empty query.")

    try:
        parsed = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.SqlglotError as e:
        # Covers both ParseError (malformed SQL) and TokenError (e.g. an unterminated string -
        # seen in practice from a model that trails off past the SQL into unrelated text).
        raise SQLValidationError(f"Could not parse SQL: {e}") from e

    statements = [s for s in parsed if s is not None]
    if len(statements) != 1:
        raise SQLValidationError(
            f"Exactly one SQL statement is allowed per call, got {len(statements)}. "
            "Remove any trailing `;`-separated statements or comments after the query."
        )
    stmt = statements[0]

    if not isinstance(stmt, _ALLOWED_ROOT_TYPES):
        raise SQLValidationError(
            f"Only SELECT (or UNION of SELECTs) statements are allowed, got {type(stmt).__name__}."
        )

    # SELECT ... INTO <table> creates a table - root type is still Select.
    if stmt.args.get("into"):
        raise SQLValidationError("SELECT ... INTO is not allowed: it creates a table.")

    # SELECT ... FOR UPDATE/SHARE takes row locks - not a plain read.
    if stmt.args.get("locks"):
        raise SQLValidationError("Locking reads (FOR UPDATE/FOR SHARE) are not allowed.")

    for node in stmt.walk():
        n = node[0] if isinstance(node, tuple) else node
        if isinstance(n, _FORBIDDEN_NODE_TYPES):
            raise SQLValidationError(
                f"Disallowed clause found: {type(n).__name__}. Only plain reads are allowed."
            )

    return ValidatedQuery(sql=sql, statement=stmt)
