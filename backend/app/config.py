"""All configuration comes from environment variables (see .env.example at the repo root).

See docs/switching-model-providers.md for how to move between local llama-server and the
free-tier cloud providers - it's one variable (MODEL_PROVIDER), same pattern as project 01's
"same image runs everywhere, only the environment changes".
"""
import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from .tools.connectors import DataSourceConnector, LocalFileConnector, PostgresConnector

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # databench/
MODEL_SERVICE_ENV = BASE_DIR.parent / "model_service" / ".env"

load_dotenv()  # databench/.env - local app config (data dir, Postgres, which provider)

# Read (not load_dotenv!) ../model_service/.env: it has its OWN generically-named MODEL_API_KEY
# (the local llama-server's own auth key, set by run_model.sh) which would collide with and
# shadow this file's if dumped into the same process environment. dotenv_values() parses it
# into a plain dict instead, so only the specific provider key we ask for is ever used.
_MODEL_SERVICE_VARS = dotenv_values(MODEL_SERVICE_ENV) if MODEL_SERVICE_ENV.exists() else {}

# --- Data sources ---
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
POSTGRES_DSN = os.getenv("POSTGRES_DSN") or None  # e.g. postgresql://databench_ro:pw@localhost:5432/therapist_db
POSTGRES_SCHEMA = os.getenv("POSTGRES_SCHEMA", "public")
POSTGRES_STATEMENT_TIMEOUT_MS = int(os.getenv("POSTGRES_STATEMENT_TIMEOUT_MS", "5000"))
MAX_ROWS_RETURNED = int(os.getenv("MAX_ROWS_RETURNED", "50"))

CONNECTORS: list[DataSourceConnector] = [LocalFileConnector(DATA_DIR)]
if POSTGRES_DSN:
    CONNECTORS.append(PostgresConnector(POSTGRES_DSN, schema=POSTGRES_SCHEMA))

# --- Model ---
# One preset per provider: base URL, a small default model, and which key in
# ../model_service/.env holds its credential. MODEL_BASE_URL/MODEL_NAME/MODEL_API_KEY, if set
# explicitly, always win - MODEL_PROVIDER is just a shortcut for "the usual settings for X".
PROVIDER_PRESETS: dict[str, dict[str, str | None]] = {
    "local": {
        "base_url": "http://127.0.0.1:8080/v1",
        "default_model": "qwen2.5-coder-7b",
        "api_key_env": None,  # llama-server ignores the key unless started with --api-key
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "openai/gpt-oss-20b",  # smallest current general-purpose Groq model with real tool-calling
        "api_key_env": "GROQ_API_KEY",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-flash-lite-latest",
        "api_key_env": "GEMINI_API_KEY",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "codestral-latest",  # code-specialized, per docs/PLAN.md's model ladder
        "api_key_env": "MISTRAL_API_KEY",
    },
}

MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "local")
_preset = PROVIDER_PRESETS.get(MODEL_PROVIDER, PROVIDER_PRESETS["local"])

MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", _preset["base_url"]).rstrip("/")
MODEL_NAME = os.getenv("MODEL_NAME", _preset["default_model"])
MODEL_API_KEY = (
    os.getenv("MODEL_API_KEY")
    or (_MODEL_SERVICE_VARS.get(_preset["api_key_env"]) if _preset["api_key_env"] else None)
    or "not-needed"
)

AGENT_NAME = "DataAgent"
