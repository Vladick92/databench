"""Data agent construction. Instructions are a jinja2 template (docs/PLAN.md requires this for
every agent) so the hard rules stay in one well-defined, reviewable place instead of an f-string.

Exposes build_data_agent() as the one thing other agents need to reuse this agent - e.g. the
ML trainer calling it as a sub-step (agent composition, see docs/PLAN.md) just imports this.
"""
from pathlib import Path

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient
from jinja2 import Environment, FileSystemLoader

from backend.app import config

from .tools import TOOLS

_AGENT_DIR = Path(__file__).parent
_env = Environment(loader=FileSystemLoader(_AGENT_DIR), trim_blocks=True, lstrip_blocks=True)


def render_instructions() -> str:
    template = _env.get_template("instructions.jinja2")
    return template.render(
        agent_name=config.DATA_AGENT_NAME,
        max_rows_returned=config.MAX_ROWS_RETURNED,
    )


def build_data_agent() -> Agent:
    client = OpenAIChatCompletionClient(
        model=config.MODEL_NAME,
        base_url=config.MODEL_BASE_URL,
        api_key=config.MODEL_API_KEY,
    )
    return Agent(client, render_instructions(), name=config.DATA_AGENT_NAME, tools=TOOLS)
