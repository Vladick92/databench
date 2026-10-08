"""Users, their chats and the messages in each, plus the interface of the two backends (sqlite.py, cosmos.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


class UsernameTaken(Exception):
    pass


def now_iso() -> str:
    """UTC, fixed width, so timestamps sort correctly as plain strings."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class User:
    username: str  # lowercase, unique; also names the user's file container
    password_hash: str
    created_at: str


@dataclass(frozen=True)
class Chat:
    id: str
    username: str
    title: str
    created_at: str
    updated_at: str  # last message; the chat list is newest first by this


@dataclass(frozen=True)
class Message:
    id: str
    chat_id: str
    role: str  # "user" | "assistant"
    content: str
    created_at: str
    # Assistant messages only: chart_summary, tools [{name, result}], plots [{title, blob}], errors, failed, interrupted
    extras: dict = field(default_factory=dict)


class Store(Protocol):
    """Scoped by username: another user's chat id behaves like one that doesn't exist."""

    def create_user(self, username: str, password_hash: str) -> User:
        """Raises UsernameTaken."""

    def get_user(self, username: str) -> User | None: ...

    def create_chat(self, username: str, title: str) -> Chat: ...

    def list_chats(self, username: str) -> list[Chat]:
        """Newest activity first."""

    def get_chat(self, username: str, chat_id: str) -> Chat | None: ...

    def delete_chat(self, username: str, chat_id: str) -> bool:
        """The chat and all its messages. False if there was no such chat."""

    def add_message(self, username: str, chat_id: str, role: str, content: str, extras: dict | None = None) -> Message:
        """Also moves the chat to the top of the list. Raises LookupError if the user has no such chat."""

    def list_messages(self, username: str, chat_id: str) -> list[Message]:
        """Oldest first."""
