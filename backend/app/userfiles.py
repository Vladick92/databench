"""Each user's private file space (tables and chart PNGs): a Blob container per user in Azure, a folder per user locally."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

TABLE_SUFFIXES = (".csv", ".xlsx", ".xls")
CHARTS_DIR = "charts"  # inside a user's space; not a table, never listed as one

# Valid exactly when "user-<username>" is a legal Azure container name.
_USERNAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
USERNAME_MIN, USERNAME_MAX = 3, 30


def username_problem(username: str) -> str | None:
    """Why `username` can't be used, or None if it can."""
    if not USERNAME_MIN <= len(username) <= USERNAME_MAX:
        return f"Username must be {USERNAME_MIN}-{USERNAME_MAX} characters."
    if not _USERNAME.fullmatch(username):
        return "Username can only have lowercase letters, digits and single hyphens (not at the start or end)."
    return None


def container_name(username: str) -> str:
    if username_problem(username):
        raise ValueError(f"not a valid username: {username!r}")
    return f"user-{username}"


def table_file_name(raw: str | None) -> str:
    """The name an uploaded table is stored under. Raises ValueError with a message for the user."""
    name = (raw or "").strip()
    if not name.lower().endswith(TABLE_SUFFIXES):
        raise ValueError(f"Only {', '.join(TABLE_SUFFIXES)} files are accepted.")
    if len(name) > 100 or name.startswith(".") or any(c in name for c in "/\\") or any(ord(c) < 32 for c in name):
        raise ValueError("Use a plain file name: no folders, at most 100 characters.")
    return name


@dataclass(frozen=True)
class FileInfo:
    name: str
    size: int


class UserFiles(Protocol):
    def ensure(self, username: str) -> None:
        """Create the user's space. Safe to call again."""

    def list_tables(self, username: str) -> list[FileInfo]:
        """The user's csv/xlsx files (not the charts), by name."""

    def read(self, username: str, name: str) -> bytes:
        """Raises FileNotFoundError."""

    def write(self, username: str, name: str, data: bytes) -> None:
        """Creates the space if it is missing, replaces a file of the same name."""

    def delete(self, username: str, name: str) -> bool:
        """False if there was no such file."""


class LocalUserFiles:
    def __init__(self, root: Path):
        self._root = root

    def _dir(self, username: str) -> Path:
        return self._root / container_name(username)

    def _path(self, username: str, name: str) -> Path:
        base = self._dir(username).resolve()
        path = (base / name).resolve()
        if base not in path.parents:
            raise ValueError(f"not a path inside the user's space: {name!r}")
        return path

    def ensure(self, username: str) -> None:
        self._dir(username).mkdir(parents=True, exist_ok=True)

    def list_tables(self, username: str) -> list[FileInfo]:
        folder = self._dir(username)
        if not folder.is_dir():
            return []
        return [
            FileInfo(p.name, p.stat().st_size)
            for p in sorted(folder.iterdir())
            if p.is_file() and p.suffix.lower() in TABLE_SUFFIXES
        ]

    def read(self, username: str, name: str) -> bytes:
        return self._path(username, name).read_bytes()

    def write(self, username: str, name: str, data: bytes) -> None:
        path = self._path(username, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def delete(self, username: str, name: str) -> bool:
        try:
            self._path(username, name).unlink()
        except FileNotFoundError:
            return False
        return True


class BlobUserFiles:
    def __init__(self, service: BlobServiceClient):
        self._service = service

    def _container(self, username: str):
        return self._service.get_container_client(container_name(username))

    def ensure(self, username: str) -> None:
        try:
            self._container(username).create_container()
        except ResourceExistsError:
            pass

    def list_tables(self, username: str) -> list[FileInfo]:
        try:
            blobs = list(self._container(username).list_blobs())
        except ResourceNotFoundError:  # nothing was ever stored: the container is created on first write
            return []
        return [
            FileInfo(b.name, b.size)
            for b in blobs
            if "/" not in b.name and b.name.lower().endswith(TABLE_SUFFIXES)
        ]

    def read(self, username: str, name: str) -> bytes:
        try:
            return self._container(username).download_blob(name).readall()
        except ResourceNotFoundError:
            raise FileNotFoundError(name) from None

    def write(self, username: str, name: str, data: bytes) -> None:
        container = self._container(username)
        try:
            container.upload_blob(name, data, overwrite=True)
        except ResourceNotFoundError:  # registration could not create the container at the time
            self.ensure(username)
            container.upload_blob(name, data, overwrite=True)

    def delete(self, username: str, name: str) -> bool:
        try:
            self._container(username).delete_blob(name)
        except ResourceNotFoundError:
            return False
        return True


@lru_cache
def get_user_files() -> UserFiles:
    """Blob containers when a storage account is configured (Azure), otherwise local folders."""
    from backend.app import config  # not at the top: config imports the data agent, which imports this

    if config.AZURE_STORAGE_ACCOUNT_URL:
        from azure.identity import DefaultAzureCredential

        return BlobUserFiles(BlobServiceClient(config.AZURE_STORAGE_ACCOUNT_URL, credential=DefaultAzureCredential()))
    return LocalUserFiles(config.USER_FILES_DIR)
