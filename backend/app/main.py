"""FastAPI backend. Owns the agent; the Streamlit UI is a thin HTTP client (see docs/PLAN.md's
FastAPI + Streamlit split, decided so agent/session/DB logic doesn't fight Streamlit's rerun model).

One process-wide agent session per `session_id` for now (in-memory, lost on restart) - a
conversations DB lands in week 2 per the schedule; this is enough to prove the wiring.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .agents.data_agent import build_data_agent

app = FastAPI(title="databench data agent")
_sessions: dict[str, object] = {}


class ChatRequest(BaseModel):
    session_id: str
    message: str


def _get_session(session_id: str):
    if session_id not in _sessions:
        _sessions[session_id] = build_data_agent().create_session()
    return _sessions[session_id]


@app.post("/chat")
async def chat(req: ChatRequest) -> dict:
    """Non-streaming: the whole answer plus the tool calls made along the way."""
    agent = build_data_agent()
    session = _get_session(req.session_id)
    tool_calls: list[dict] = []
    answer = ""
    pending: list[str] = []
    async for update in agent.run(req.message, stream=True, session=session):
        for c in update.contents:
            if c.type == "function_call" and c.name:
                pending.append(c.name)
            elif c.type == "function_result":
                tool_calls.append({"name": pending.pop(0) if pending else "tool", "result": str(c.result)})
            elif c.type == "text" and c.text:
                answer += c.text
    return {"answer": answer, "tool_calls": tool_calls}


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Streaming variant: newline-delimited JSON events {"type": "text"|"tool_call", ...}."""
    import json

    agent = build_data_agent()
    session = _get_session(req.session_id)

    async def events():
        pending: list[str] = []
        async for update in agent.run(req.message, stream=True, session=session):
            for c in update.contents:
                if c.type == "function_call" and c.name:
                    pending.append(c.name)
                elif c.type == "function_result":
                    name = pending.pop(0) if pending else "tool"
                    yield json.dumps({"type": "tool_call", "name": name, "result": str(c.result)}) + "\n"
                elif c.type == "text" and c.text:
                    yield json.dumps({"type": "text", "text": c.text}) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.delete("/chat/{session_id}")
async def reset_session(session_id: str) -> dict:
    _sessions.pop(session_id, None)
    return {"ok": True}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
