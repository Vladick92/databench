"""Users, chats and messages in Cosmos DB; the containers come from terraform/modules/cosmos (partition keys: username, username, chat id)."""
from __future__ import annotations

import uuid

from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceExistsError, CosmosResourceNotFoundError

from .models import Chat, Message, User, UsernameTaken, now_iso


def _chat(doc: dict) -> Chat:
    return Chat(doc["id"], doc["username"], doc["title"], doc["created_at"], doc["updated_at"])


def _message(doc: dict) -> Message:
    return Message(doc["id"], doc["chat_id"], doc["role"], doc["content"], doc["created_at"], doc.get("extras") or {})


class CosmosStore:
    def __init__(self, client: CosmosClient, database: str):
        db = client.get_database_client(database)
        self._users = db.get_container_client("users")
        self._chats = db.get_container_client("chats")
        self._messages = db.get_container_client("messages")

    def create_user(self, username: str, password_hash: str) -> User:
        user = User(username, password_hash, now_iso())
        doc = {"id": username, "username": username, "password_hash": password_hash, "created_at": user.created_at}
        try:
            self._users.create_item(doc)
        except CosmosResourceExistsError:
            raise UsernameTaken(username) from None
        return user

    def get_user(self, username: str) -> User | None:
        try:
            doc = self._users.read_item(username, partition_key=username)
        except CosmosResourceNotFoundError:
            return None
        return User(doc["username"], doc["password_hash"], doc["created_at"])

    def create_chat(self, username: str, title: str) -> Chat:
        now = now_iso()
        doc = {"id": uuid.uuid4().hex, "username": username, "title": title, "created_at": now, "updated_at": now}
        self._chats.create_item(doc)
        return _chat(doc)

    def list_chats(self, username: str) -> list[Chat]:
        docs = self._chats.query_items(
            "SELECT * FROM c WHERE c.username = @username ORDER BY c.updated_at DESC",
            parameters=[{"name": "@username", "value": username}],
            partition_key=username,
        )
        return [_chat(doc) for doc in docs]

    def get_chat(self, username: str, chat_id: str) -> Chat | None:
        try:
            return _chat(self._chats.read_item(chat_id, partition_key=username))
        except CosmosResourceNotFoundError:
            return None

    def delete_chat(self, username: str, chat_id: str) -> bool:
        try:
            self._chats.delete_item(chat_id, partition_key=username)
        except CosmosResourceNotFoundError:
            return False
        ids = [
            doc["id"]
            for doc in self._messages.query_items(
                "SELECT c.id FROM c WHERE c.chat_id = @chat_id",
                parameters=[{"name": "@chat_id", "value": chat_id}],
                partition_key=chat_id,
            )
        ]
        for message_id in ids:
            self._messages.delete_item(message_id, partition_key=chat_id)
        return True

    def add_message(self, username: str, chat_id: str, role: str, content: str, extras: dict | None = None) -> Message:
        now = now_iso()
        try:  # also proves the chat is this user's: the chat's partition is the username
            self._chats.patch_item(
                chat_id, partition_key=username, patch_operations=[{"op": "set", "path": "/updated_at", "value": now}]
            )
        except CosmosResourceNotFoundError:
            raise LookupError(chat_id) from None
        doc = {
            "id": uuid.uuid4().hex,
            "chat_id": chat_id,
            "username": username,
            "role": role,
            "content": content,
            "extras": extras or {},
            "created_at": now,
        }
        self._messages.create_item(doc)
        return _message(doc)

    def list_messages(self, username: str, chat_id: str) -> list[Message]:
        docs = self._messages.query_items(
            "SELECT * FROM c WHERE c.chat_id = @chat_id AND c.username = @username ORDER BY c.created_at ASC",
            parameters=[{"name": "@chat_id", "value": chat_id}, {"name": "@username", "value": username}],
            partition_key=chat_id,
        )
        return [_message(doc) for doc in docs]
