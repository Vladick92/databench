"""FastAPI backend: owns the agents, users, chats and files; the Streamlit UI is a thin HTTP client (docs/PLAN.md)."""
from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agent_framework import AgentSession, InMemoryHistoryProvider
from agent_framework import Message as AgentMessage
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import config
from .auth import current_user
from .auth import router as auth_router
from .chats import CHAT_ID_PATTERN
from .chats import router as chats_router
from .files import router as files_router
from .store import Chat, Message, Store, get_store
from .userfiles import CHARTS_DIR, UserFiles, get_user_files
from .workflows.data_exploration import DATA_AGENT, STATISTICIAN, describe_error, run_data_exploration

log = logging.getLogger(__name__)

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # uvicorn's own logger: the app's loggers have no handler at INFO, and this line is how a deployed
    # backend's settings are read back from the Web App's log stream.
    logging.getLogger("uvicorn.error").info("databench backend started: %s", config.describe())
    yield


app = FastAPI(title="databench data agent", lifespan=_lifespan)
app.include_router(auth_router)
app.include_router(chats_router)
app.include_router(files_router)

_HISTORY_MESSAGES = 20  # how much of a chat the agent is reminded of: its last ten exchanges
_MAX_STORED_TOOL_RESULT = 8000  # characters of one tool call's output kept with the message


class ChatRequest(BaseModel):
    message: str = Field(max_length=4000)
    chat_id: str | None = Field(default=None, pattern=CHAT_ID_PATTERN)  # None starts a new chat


def _title(message: str) -> str:
    title = " ".join(message.split())
    return title if len(title) <= 60 else title[:59].rstrip() + "…"


def _remembering(earlier: list[Message]) -> AgentSession:
    """A fresh session remembering the chat's questions and answers (not its tool calls); no process holds chat state.

    Only complete exchanges are remembered: a question goes in together with the answer that followed it.
    A question whose answer failed, or was cut off before it said anything, goes out with it, so the model
    never sees a user message with no reply (or two user messages in a row, which some providers refuse)."""
    kept = []
    for question, answer in zip(earlier, earlier[1:]):
        if question.role == "user" and answer.role == "assistant" and answer.content and not answer.extras.get("failed"):
            kept += [question, answer]
    session = AgentSession()
    session.state[InMemoryHistoryProvider.DEFAULT_SOURCE_ID] = {
        "messages": [AgentMessage(m.role, [m.content]) for m in kept[-_HISTORY_MESSAGES:]]
    }
    return session


async def _open_turn(req: ChatRequest, username: str, store: Store) -> tuple[Chat, str, AgentSession]:
    """The chat, the question and the agent's memory of the chat; fails with 404/422 before a response starts."""
    message = req.message.strip()
    if not message:
        raise HTTPException(422, "Write a question first.")
    if req.chat_id is None:
        chat = await run_in_threadpool(store.create_chat, username, _title(message))
        return chat, message, _remembering([])
    chat = await run_in_threadpool(store.get_chat, username, req.chat_id)
    if chat is None:
        raise HTTPException(404, "No such chat.")
    return chat, message, _remembering(await run_in_threadpool(store.list_messages, username, chat.id))


class _Reply:
    """The assistant's side of a turn, collected from the events as they stream past."""

    def __init__(self) -> None:
        self.content = ""
        self.chart_summary = ""
        self.tools: list[dict] = []
        self.plots: list[dict] = []
        self.errors: list[str] = []
        self.failed = False

    def add(self, event: dict) -> None:
        kind, agent = event["type"], event.get("agent")
        if kind == "text" and agent == DATA_AGENT:
            self.content += event["text"]
        elif kind == "text":
            self.chart_summary += event["text"]
        elif kind == "tool_call":
            self.tools.append({"name": event["name"], "result": event["result"][:_MAX_STORED_TOOL_RESULT]})
        elif kind == "plot":
            self.plots.append({"title": event["title"], "png_b64": event["png_b64"]})
        elif kind == "error" and agent == DATA_AGENT:
            self.content, self.failed = event["message"], True
        elif kind == "error":  # the statistician failed; the data agent's answer stands
            self.errors.append(event["message"])


def _save_reply(store: Store, files: UserFiles, username: str, chat_id: str, reply: _Reply, interrupted: bool) -> None:
    """Synchronous and non-raising on purpose: it runs after the client has gone, where an await would be cancelled."""
    if not (reply.content or reply.tools):  # cut off before the model produced anything
        return
    try:
        plots = []
        for plot in reply.plots:  # charts live in the user's file space, the message only points at them
            blob = f"{CHARTS_DIR}/{chat_id}/{uuid.uuid4().hex}.png"
            try:
                files.write(username, blob, base64.b64decode(plot["png_b64"]))
            except Exception:
                log.exception("could not store a chart of chat %s", chat_id)
                continue
            plots.append({"title": plot["title"], "blob": blob})
        extras = {
            "chart_summary": reply.chart_summary,
            "tools": reply.tools,
            "plots": plots,
            "errors": reply.errors,
            "failed": reply.failed,
            "interrupted": interrupted,
        }
        store.add_message(username, chat_id, "assistant", reply.content, {k: v for k, v in extras.items() if v})
    except Exception:
        log.exception("could not save the reply of chat %s", chat_id)


async def _turn(
    chat: Chat, message: str, session: AgentSession, username: str, store: Store, files: UserFiles
) -> AsyncIterator[dict]:
    """The workflow's events for one question, led by a {"type": "chat", "chat_id", "title"} event; saved as it goes."""
    reply, finished = _Reply(), False
    try:
        yield {"type": "chat", "chat_id": chat.id, "title": chat.title}
        await run_in_threadpool(store.add_message, username, chat.id, "user", message)
        async for event in run_data_exploration(message, session, username, files):
            reply.add(event)
            yield event
        finished = True
    except Exception as e:  # the response has started, so the failure has to travel inside the stream
        log.exception("chat turn failed")
        event = {"type": "error", "agent": DATA_AGENT, "message": describe_error(e)}
        reply.add(event)
        finished = True
        yield event
    finally:
        _save_reply(store, files, username, chat.id, reply, interrupted=not finished)


@app.post("/chat")
async def chat(
    req: ChatRequest,
    username: str = Depends(current_user),
    store: Store = Depends(get_store),
    files: UserFiles = Depends(get_user_files),
) -> dict:
    """Non-streaming: the data agent's answer, the statistician's chart(s) and summary if the result
    was big enough for one, and every tool call made along the way."""
    opened, message, session = await _open_turn(req, username, store)
    events = [e async for e in _turn(opened, message, session, username, store, files)]

    def text_of(agent: str) -> str:
        return "".join(e["text"] for e in events if e["type"] == "text" and e["agent"] == agent)

    return {
        "chat_id": opened.id,
        "answer": text_of(DATA_AGENT),
        "chart_summary": text_of(STATISTICIAN),
        "plots": [{"title": e["title"], "png_b64": e["png_b64"]} for e in events if e["type"] == "plot"],
        "tool_calls": [
            {"agent": e["agent"], "name": e["name"], "result": e["result"]} for e in events if e["type"] == "tool_call"
        ],
        "errors": [e["message"] for e in events if e["type"] == "error"],
    }


@app.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    username: str = Depends(current_user),
    store: Store = Depends(get_store),
    files: UserFiles = Depends(get_user_files),
) -> StreamingResponse:
    """Streaming variant: newline-delimited JSON events, see workflows/data_exploration.py for the types."""
    opened, message, session = await _open_turn(req, username, store)

    async def lines():
        async for event in _turn(opened, message, session, username, store, files):
            yield json.dumps(event) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
