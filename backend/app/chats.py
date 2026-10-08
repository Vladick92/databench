"""A user's chats and their messages; someone else's chat id is a 404."""
from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Depends, HTTPException, Path

from .auth import current_user
from .store import Chat, Message, Store, get_store
from .userfiles import UserFiles, get_user_files

log = logging.getLogger(__name__)
router = APIRouter(prefix="/chats", tags=["chats"])

CHAT_ID_PATTERN = r"^[0-9a-f]{32}$"  # what the store generates; anything else can't be a chat


def chat_view(chat: Chat) -> dict:
    return {"id": chat.id, "title": chat.title, "created_at": chat.created_at, "updated_at": chat.updated_at}


def _message_view(message: Message, username: str, files: UserFiles) -> dict:
    """The stored message with its charts read back from the user's file space as base64 PNGs."""
    extras = dict(message.extras)
    plots = []
    for plot in extras.pop("plots", []):
        try:
            png = files.read(username, plot["blob"])
        except FileNotFoundError:
            continue
        plots.append({"title": plot["title"], "png_b64": base64.b64encode(png).decode()})
    return {"id": message.id, "role": message.role, "content": message.content, "created_at": message.created_at, "plots": plots, **extras}


def _get_chat(store: Store, username: str, chat_id: str) -> Chat:
    chat = store.get_chat(username, chat_id)
    if chat is None:
        raise HTTPException(404, "No such chat.")
    return chat


@router.get("")
def list_chats(username: str = Depends(current_user), store: Store = Depends(get_store)) -> list[dict]:
    return [chat_view(chat) for chat in store.list_chats(username)]


@router.get("/{chat_id}")
def get_chat(
    chat_id: str = Path(pattern=CHAT_ID_PATTERN),
    username: str = Depends(current_user),
    store: Store = Depends(get_store),
    files: UserFiles = Depends(get_user_files),
) -> dict:
    chat = _get_chat(store, username, chat_id)
    messages = [_message_view(m, username, files) for m in store.list_messages(username, chat_id)]
    return {**chat_view(chat), "messages": messages}


@router.delete("/{chat_id}")
def delete_chat(
    chat_id: str = Path(pattern=CHAT_ID_PATTERN),
    username: str = Depends(current_user),
    store: Store = Depends(get_store),
    files: UserFiles = Depends(get_user_files),
) -> dict:
    _get_chat(store, username, chat_id)
    charts = [p["blob"] for m in store.list_messages(username, chat_id) for p in m.extras.get("plots", [])]
    store.delete_chat(username, chat_id)
    for blob in charts:
        try:
            files.delete(username, blob)
        except Exception:  # the chat is gone either way; a chart left behind is only wasted space
            log.warning("could not delete chart %s of %s", blob, username, exc_info=True)
    return {"ok": True}
