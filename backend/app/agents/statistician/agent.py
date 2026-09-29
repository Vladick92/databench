"""Statistician agent construction. Same shape as the data agent: a jinja2 instructions template
(docs/PLAN.md requires this for every agent) plus this module's build function.

The statistician gets no conversation session - each run is one question about one query result,
so there is nothing to remember between runs.
"""
from pathlib import Path

from agent_framework import Agent
from jinja2 import Environment, FileSystemLoader

from backend.app import config
from backend.app.llm import build_chat_client

from .tools import MAX_CATEGORIES, MAX_PLOTS, MAX_POINTS, MAX_SERIES, TOOLS

_AGENT_DIR = Path(__file__).parent
_env = Environment(loader=FileSystemLoader(_AGENT_DIR), trim_blocks=True, lstrip_blocks=True)


def render_instructions() -> str:
    template = _env.get_template("instructions.jinja2")
    return template.render(
        agent_name=config.STATISTICIAN_AGENT_NAME,
        max_categories=MAX_CATEGORIES,
        max_points=MAX_POINTS,
        max_series=MAX_SERIES,
        max_plots=MAX_PLOTS,
    )


def build_statistician_agent() -> Agent:
    return Agent(build_chat_client(), render_instructions(), name=config.STATISTICIAN_AGENT_NAME, tools=TOOLS)
