"""data agent -> statistician, as plain sequential Python.

Two awaits and one `if` on a property of the data (its row count). A graph workflow or an
orchestration package couldn't make that decision: they only see the agents' messages, and a tool
result in a message is the text the model was shown, never the real rows. Here the rows travel
beside the messages, through a per-request capture (data_agent/tools/results.py), and go straight
to the statistician's plotting code.

Everything is reported as events (dicts) so one generator serves both the streaming and the
non-streaming endpoint. Event types, each tagged with `agent` (DATA_AGENT or STATISTICIAN):
  text       {"text": chunk}
  tool_call  {"name", "result"}          a finished tool call
  stage      {"status": "started"}       the statistician has begun (the UI shows progress)
  plot       {"title", "png_b64"}        a finished chart
  error      {"message"}                 the statistician failed; the data agent's answer stands
"""
from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator

from backend.app import config
from backend.app.agents.data_agent import QueryResult, build_data_agent, start_capture
from backend.app.agents.statistician import build_prompt, build_statistician_agent, open_workspace

log = logging.getLogger(__name__)

DATA_AGENT = "data_agent"
STATISTICIAN = "statistician"

Event = dict


def describe_error(e: Exception) -> str:
    """The framework wraps provider failures (rate limit, bad key...) in a ChatClientException whose
    message is mostly class paths; the wrapped error is the readable part."""
    root = e.__cause__ or e
    return f"{type(root).__name__}: {root}"


async def _stream_agent(agent, message: str, agent_id: str, session=None) -> AsyncIterator[Event]:
    """One agent run as events: text chunks and finished tool calls."""
    pending: list[str] = []
    async for update in agent.run(message, stream=True, session=session):
        for c in update.contents:
            if c.type == "function_call" and c.name:
                pending.append(c.name)
            elif c.type == "function_result":
                name = pending.pop(0) if pending else "tool"
                yield {"type": "tool_call", "agent": agent_id, "name": name, "result": str(c.result)}
            elif c.type == "text" and c.text:
                yield {"type": "text", "agent": agent_id, "text": c.text}


async def _run_statistician(question: str, result: QueryResult) -> AsyncIterator[Event]:
    note = ""
    if result.truncated:
        cap = config.MAX_ROWS_RETURNED
        note = f"The query matched more rows than the {cap} retrieved, so the chart uses only the first {cap}."
    workspace = open_workspace(result.df, note=note)
    prompt = build_prompt(question, workspace, sql=result.sql)

    yield {"type": "stage", "agent": STATISTICIAN, "status": "started"}
    sent = 0
    async for event in _stream_agent(build_statistician_agent(), prompt, STATISTICIAN):
        yield event
        while sent < len(workspace.plots):  # a chart exists as soon as build_plot has returned
            plot = workspace.plots[sent]
            sent += 1
            yield {
                "type": "plot",
                "agent": STATISTICIAN,
                "title": plot.title,
                "png_b64": base64.b64encode(plot.png).decode(),
            }


async def run_data_exploration(message: str, session) -> AsyncIterator[Event]:
    results = start_capture()
    async for event in _stream_agent(build_data_agent(), message, DATA_AGENT, session):
        yield event

    result = results[-1] if results else None  # the last successful query is the one that answered
    if result is None or len(result.df) <= config.CHART_MIN_ROWS:
        return

    try:
        async for event in _run_statistician(message, result):
            yield event
    except Exception as e:  # the data agent's answer is already delivered; a failed chart must not undo it
        log.exception("statistician stage failed")
        yield {"type": "error", "agent": STATISTICIAN, "message": describe_error(e)}
