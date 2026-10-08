"""Whose files the data agent's tools see: a per-request context variable, like results.py's capture (tools take no user argument)."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from backend.app import config
from backend.app.userfiles import UserFiles

from .connectors import DataSourceConnector, UserFilesConnector


@dataclass(frozen=True)
class Scope:
    username: str
    files: UserFiles


_scope: ContextVar[Scope | None] = ContextVar("scope", default=None)


def use_scope(username: str, files: UserFiles) -> None:
    _scope.set(Scope(username, files))


def active_connectors() -> list[DataSourceConnector]:
    """The user's own files first, then what everyone sees. Outside a request (no user), only the shared ones."""
    scope = _scope.get()
    own = [UserFilesConnector(scope.files, scope.username)] if scope else []
    return [*own, *config.SHARED_CONNECTORS]
