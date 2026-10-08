"""Users, chats and messages in one SQLite file: the local stand-in for Cosmos DB."""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import Chat, Message, User, UsernameTaken, now_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chats_by_user ON chats(username, updated_at DESC);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    extras TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_chat ON messages(chat_id, created_at);
"""


class SqliteStore:
    def __init__(self, path: Path):
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")  # off by default, and per connection
        try:
            with db:  # commit, or roll back if the block raised
                yield db
        finally:
            db.close()

    def create_user(self, username: str, password_hash: str) -> User:
        user = User(username, password_hash, now_iso())
        try:
            with self._db() as db:
                db.execute("INSERT INTO users VALUES (?, ?, ?)", (user.username, user.password_hash, user.created_at))
        except sqlite3.IntegrityError:
            raise UsernameTaken(username) from None
        return user

    def get_user(self, username: str) -> User | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return User(**row) if row else None

    def create_chat(self, username: str, title: str) -> Chat:
        now = now_iso()
        chat = Chat(uuid.uuid4().hex, username, title, now, now)
        with self._db() as db:
            db.execute("INSERT INTO chats VALUES (?, ?, ?, ?, ?)", (chat.id, username, title, now, now))
        return chat

    def list_chats(self, username: str) -> list[Chat]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM chats WHERE username = ? ORDER BY updated_at DESC", (username,)).fetchall()
        return [Chat(**row) for row in rows]

    def get_chat(self, username: str, chat_id: str) -> Chat | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM chats WHERE id = ? AND username = ?", (chat_id, username)).fetchone()
        return Chat(**row) if row else None

    def delete_chat(self, username: str, chat_id: str) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM chats WHERE id = ? AND username = ?", (chat_id, username)).rowcount > 0

    def add_message(self, username: str, chat_id: str, role: str, content: str, extras: dict | None = None) -> Message:
        message = Message(uuid.uuid4().hex, chat_id, role, content, now_iso(), extras or {})
        with self._db() as db:
            moved = db.execute(
                "UPDATE chats SET updated_at = ? WHERE id = ? AND username = ?", (message.created_at, chat_id, username)
            ).rowcount
            if not moved:
                raise LookupError(chat_id)
            db.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (message.id, chat_id, role, content, json.dumps(message.extras), message.created_at),
            )
        return message

    def list_messages(self, username: str, chat_id: str) -> list[Message]:
        with self._db() as db:
            rows = db.execute(
                """
                SELECT m.* FROM messages m JOIN chats c ON c.id = m.chat_id
                WHERE c.id = ? AND c.username = ? ORDER BY m.created_at, m.rowid
                """,
                (chat_id, username),
            ).fetchall()
        return [Message(**{**row, "extras": json.loads(row["extras"])}) for row in rows]
