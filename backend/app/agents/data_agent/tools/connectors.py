"""Data source discovery, kept pluggable on purpose.

Today there are two connectors: a folder of csv/xlsx files, and one local Postgres database.
Later there will be more databases and more file locations (the docs/PLAN.md "dataset-agnostic"
requirement). Adding one of those is: write a class implementing `DataSourceConnector.list_sources`,
add an instance to `CONNECTORS` in config.py. Nothing that calls `list_all_sources` needs to change.
"""
from __future__ import annotations

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobClient, BlobServiceClient

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


def _is_blob_location(location: str) -> bool:
    return ".blob.core.windows.net/" in location


def _read_blob_dataframe(blob_url: str, reader, nrows: int | None = None) -> pd.DataFrame:
    # DefaultAzureCredential tries, in order: env vars, managed identity (this is what actually
    # fires once deployed to the Web App), then falls back to `az login` locally for testing.
    client = BlobClient.from_blob_url(blob_url, credential=DefaultAzureCredential())
    data = client.download_blob().readall()
    return reader(io.BytesIO(data), nrows=nrows)


def read_source_dataframe(source: DataSource) -> pd.DataFrame:
    """Read a file-backed DataSource (local or blob) fully into a DataFrame. Used by
    execute_table_code - the one place that needs the real, full file, not just a sample."""
    if _is_blob_location(source.location):
        suffix = Path(source.location.rsplit("/", 1)[-1]).suffix.lower()
        return _read_blob_dataframe(source.location, _READERS[suffix])
    return _READERS[Path(source.location).suffix.lower()](source.location)


@dataclass
class BlobFileConnector(DataSourceConnector):
    """Scans one Azure Blob Storage container for .csv/.xlsx/.xls blobs - the cloud equivalent
    of LocalFileConnector, added alongside it (not replacing it) so the sample data/ folder
    baked into the image and user-uploaded blobs both show up as sources once deployed."""

    account_url: str  # https://<account>.blob.core.windows.net
    container_name: str
    sample_rows: int = 50

    def list_sources(self) -> list[DataSource]:
        try:
            credential = DefaultAzureCredential()
            container = BlobServiceClient(
                account_url=self.account_url, credential=credential
            ).get_container_client(self.container_name)
            blob_names = [b.name for b in container.list_blobs()]
        except Exception:
            return []  # storage unreachable/misconfigured right now - not fatal, just no blob sources

        sources = []
        for blob_name in blob_names:
            suffix = Path(blob_name).suffix.lower()
            reader = _READERS.get(suffix)
            if reader is None:
                continue
            blob_url = f"{self.account_url}/{self.container_name}/{blob_name}"
            try:
                sample = _read_blob_dataframe(blob_url, reader, nrows=self.sample_rows)
            except Exception:
                continue  # unreadable/corrupt blob: skip it, don't break the whole listing
            sources.append(
                DataSource(
                    name=Path(blob_name).stem,
                    kind=_KIND_BY_SUFFIX[suffix],
                    tool=EXECUTE_TABLE_CODE,
                    columns={c: str(t) for c, t in sample.dtypes.items()},
                    row_count=None,
                    location=blob_url,
                )
            )
        return sources


def list_all_sources(connectors: list[DataSourceConnector]) -> list[DataSource]:
    sources: list[DataSource] = []
    for connector in connectors:
        sources.extend(connector.list_sources())
    return sources
