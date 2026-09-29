"""FastAPI backend. Owns the agents; the Streamlit UI is a thin HTTP client (see docs/PLAN.md's
FastAPI + Streamlit split, decided so agent/session/DB logic doesn't fight Streamlit's rerun model).

One process-wide agent session per `session_id` for now (in-memory, lost on restart) - a
conversations DB lands in week 2 per the schedule; this is enough to prove the wiring.
"""
from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .agents.data_agent import build_data_agent
from .files import router as files_router
from .workflows.data_exploration import DATA_AGENT, STATISTICIAN, describe_error, run_data_exploration

log = logging.getLogger(__name__)

app = FastAPI(title="databench data agent")
app.include_router(files_router)
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
    """Non-streaming: the data agent's answer, the statistician's chart(s) and summary if the result
    was big enough for one, and every tool call made along the way."""
    events = [e async for e in run_data_exploration(req.message, _get_session(req.session_id))]

    def text_of(agent: str) -> str:
        return "".join(e["text"] for e in events if e["type"] == "text" and e["agent"] == agent)

    return {
        "answer": text_of(DATA_AGENT),
        "chart_summary": text_of(STATISTICIAN),
        "plots": [{"title": e["title"], "png_b64": e["png_b64"]} for e in events if e["type"] == "plot"],
        "tool_calls": [
            {"agent": e["agent"], "name": e["name"], "result": e["result"]} for e in events if e["type"] == "tool_call"
        ],
        "errors": [e["message"] for e in events if e["type"] == "error"],
    }


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Streaming variant: newline-delimited JSON events, see workflows/data_exploration.py for the types."""
    session = _get_session(req.session_id)

    async def events():
        try:
            async for event in run_data_exploration(req.message, session):
                yield json.dumps(event) + "\n"
        except Exception as e:  # the response has started, so the failure has to travel inside the stream
            log.exception("chat stream failed")
            yield json.dumps({"type": "error", "agent": DATA_AGENT, "message": describe_error(e)}) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.delete("/chat/{session_id}")
async def reset_session(session_id: str) -> dict:
    _sessions.pop(session_id, None)
    return {"ok": True}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
