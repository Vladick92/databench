"""Data agent construction. Instructions are a jinja2 template (docs/PLAN.md requires this for
every agent) so the hard rules stay in one well-defined, reviewable place instead of an f-string."""
from pathlib import Path

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient
from jinja2 import Environment, FileSystemLoader

from . import config
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_env = Environment(loader=FileSystemLoader(_PROMPTS_DIR), trim_blocks=True, lstrip_blocks=True)


def render_instructions() -> str:
    template = _env.get_template("data_agent.jinja2")
    return template.render(
        agent_name=config.AGENT_NAME,
        max_rows_returned=config.MAX_ROWS_RETURNED,
    )


def build_agent() -> Agent:
    client = OpenAIChatCompletionClient(
        model=config.MODEL_NAME,
        base_url=config.MODEL_BASE_URL,
        api_key=config.MODEL_API_KEY,
    )
    return Agent(client, render_instructions(), name=config.AGENT_NAME, tools=TOOLS)
