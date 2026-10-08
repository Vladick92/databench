"""Pluggable data source discovery: the sample csv/xlsx folder, one Postgres database, the requesting user's uploads.
A new kind is a class implementing `list_sources`, added in `scope.active_connectors`; callers don't change."""
from __future__ import annotations

import io
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import psycopg

if TYPE_CHECKING:
    from backend.app.userfiles import UserFiles

# How a source's rows are actually read.
EXECUTE_SQL_QUERY = "execute_sql_query"
EXECUTE_TABLE_CODE = "execute_table_code"

_READERS = {".csv": pd.read_csv, ".xlsx": pd.read_excel, ".xls": pd.read_excel}
_KIND_BY_SUFFIX = {".csv": "csv_file", ".xlsx": "xlsx_file", ".xls": "xlsx_file"}


@dataclass(frozen=True)
class DataSource:
    name: str  # what the model uses to refer to this source, unique across all connectors
    kind: str  # "csv_file" | "xlsx_file" | "postgres_table"
    tool: str  # EXECUTE_SQL_QUERY or EXECUTE_TABLE_CODE - which tool can read this source
    columns: dict[str, str]  # column name -> dtype, as text
    row_count: int | None  # None when counting would be expensive/unknown
    location: str  # file path or "schema.table", for humans/debugging only
    # File sources only: reads the whole file. The one thing execute_table_code needs beyond the sample.
    load: Callable[[], pd.DataFrame] | None = field(default=None, compare=False, repr=False)


class DataSourceConnector(ABC):
    @abstractmethod
    def list_sources(self) -> list[DataSource]:
        """Return the sources this connector currently sees. Must not raise for an empty
        result (no files, no tables) - only for a real failure (folder missing, DB unreachable),
        which callers treat as "this connector is down", not "the whole tool call fails".
        """


@dataclass
class LocalFileConnector(DataSourceConnector):
    """Scans one folder (non-recursive) for .csv/.xlsx/.xls files."""

    data_dir: Path
    sample_rows: int = 50  # rows read to infer columns/dtypes; kept small, this is not the query

    def list_sources(self) -> list[DataSource]:
        if not self.data_dir.is_dir():
            return []
        sources = []
        for path in sorted(self.data_dir.iterdir()):
            reader = _READERS.get(path.suffix.lower())
            if reader is None or not path.is_file():
                continue
            try:
                sample = reader(path, nrows=self.sample_rows)
            except Exception:
                continue  # unreadable/corrupt file: skip it, don't break the whole listing
            sources.append(
                DataSource(
                    name=path.stem,
                    kind=_KIND_BY_SUFFIX[path.suffix.lower()],
                    tool=EXECUTE_TABLE_CODE,
                    columns={c: str(t) for c, t in sample.dtypes.items()},
                    row_count=None,
                    location=str(path),
                    load=partial(reader, path),
                )
            )
        return sources


@dataclass
class UserFilesConnector(DataSourceConnector):
    """The csv/xlsx files one user has uploaded to their own file space (a Blob container in Azure)."""

    files: UserFiles
    username: str
    sample_rows: int = 50

    def _load(self, name: str, reader) -> pd.DataFrame:
        return reader(io.BytesIO(self.files.read(self.username, name)))

    def list_sources(self) -> list[DataSource]:
        try:
            infos = self.files.list_tables(self.username)
        except Exception:
            return []  # storage unreachable/misconfigured right now - not fatal, just no uploaded sources

        sources = []
        for info in infos:
            suffix = Path(info.name).suffix.lower()
            reader = _READERS[suffix]
            try:
                sample = reader(io.BytesIO(self.files.read(self.username, info.name)), nrows=self.sample_rows)
            except Exception:
                continue  # unreadable/corrupt upload: skip it, don't break the whole listing
            sources.append(
                DataSource(
                    name=Path(info.name).stem,
                    kind=_KIND_BY_SUFFIX[suffix],
                    tool=EXECUTE_TABLE_CODE,
                    columns={c: str(t) for c, t in sample.dtypes.items()},
                    row_count=None,
                    location=f"your uploads/{info.name}",
                    load=partial(self._load, info.name, reader),
                )
            )
        return sources


@dataclass
class PostgresConnector(DataSourceConnector):
    """Introspects base tables in one schema of one Postgres database, via a read-only DSN."""

    dsn: str
    schema: str = "public"
    connect_timeout: int = 3

    def list_sources(self) -> list[DataSource]:
        try:
            with psycopg.connect(self.dsn, connect_timeout=self.connect_timeout) as conn:
                conn.read_only = True
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT table_name, column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = %s
                        ORDER BY table_name, ordinal_position
                        """,
                        (self.schema,),
                    )
                    rows = cur.fetchall()
        except psycopg.OperationalError:
            return []  # DB unreachable right now - not fatal, just nothing from this connector

        tables: dict[str, dict[str, str]] = {}
        for table_name, column_name, data_type in rows:
            tables.setdefault(table_name, {})[column_name] = data_type

        return [
            DataSource(
                name=table_name,
                kind="postgres_table",
                tool=EXECUTE_SQL_QUERY,
                columns=columns,
                row_count=None,
                location=f"{self.schema}.{table_name}",
            )
            for table_name, columns in tables.items()
        ]


def read_source_dataframe(source: DataSource) -> pd.DataFrame:
    """Read a file-backed DataSource fully into a DataFrame. Used by execute_table_code - the one
    place that needs the real, full file, not just a sample."""
    if source.load is None:
        raise ValueError(f"{source.name!r} is not a file source")
    return source.load()


def list_all_sources(connectors: list[DataSourceConnector]) -> list[DataSource]:
    """Every connector's sources; on a name clash the earlier connector wins (an upload shadows a shared sample)."""
    sources: dict[str, DataSource] = {}
    for connector in connectors:
        for source in connector.list_sources():
            sources.setdefault(source.name, source)
    return list(sources.values())
