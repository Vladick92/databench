"""All configuration comes from environment variables (see .env.example at the repo root).

See docs/switching-model-providers.md for how to move between local llama-server and the
free-tier cloud providers - it's one variable (MODEL_PROVIDER), same pattern as project 01's
"same image runs everywhere, only the environment changes".
"""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from .agents.data_agent.tools.connectors import (
    BlobFileConnector,
    DataSourceConnector,
    LocalFileConnector,
    PostgresConnector,
)

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
# Rows one query tool call retrieves. All of them go to the workflow (and from there to the
# statistician's plotting code); the model never gets more than a profile of a big result, because
# Groq's free tier allows 8k tokens/minute and because a model that is shown rows pastes them into
# its answer.
MAX_ROWS_RETURNED = int(os.getenv("MAX_ROWS_RETURNED", "1000"))
# Up to this many rows the data agent sees the whole result and shows it as a table. Above it, the
# statistician charts the result and the data agent only writes a short summary.
CHART_MIN_ROWS = int(os.getenv("CHART_MIN_ROWS", "15"))

# Set by Terraform as Web App settings (see terraform/main.tf's backend_app module) - unset
# locally, so BlobFileConnector just isn't registered and only the local data/ folder shows up.
AZURE_STORAGE_ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT_NAME") or None
AZURE_STORAGE_CONTAINER_NAME = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "tabular-data")
AZURE_STORAGE_ACCOUNT_URL = (
    f"https://{AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net" if AZURE_STORAGE_ACCOUNT_NAME else None
)

# Blob is additive, not a replacement for local files: the sample data/ folder ships baked into
# the image either way, blob storage adds whatever the owner uploads through the UI on top of it.
CONNECTORS: list[DataSourceConnector] = [LocalFileConnector(DATA_DIR)]
if POSTGRES_DSN:
    CONNECTORS.append(PostgresConnector(POSTGRES_DSN, schema=POSTGRES_SCHEMA))
if AZURE_STORAGE_ACCOUNT_URL:
    CONNECTORS.append(BlobFileConnector(AZURE_STORAGE_ACCOUNT_URL, AZURE_STORAGE_CONTAINER_NAME))

# --- Model ---
# One preset per provider: base URL, a default model, and which key holds its credential (an
# environment variable of that name first, then ../model_service/.env).
# MODEL_BASE_URL/MODEL_NAME/MODEL_API_KEY, if set explicitly, always win for the primary provider -
# MODEL_PROVIDER is just a shortcut for "the usual settings for X".
PROVIDER_PRESETS: dict[str, dict[str, str | None]] = {
    "local": {
        "base_url": "http://127.0.0.1:8080/v1",
        "default_model": "qwen2.5-coder-7b",
        "api_key_env": "MODEL_API_KEY",  # llama-server ignores the key unless started with --api-key
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "openai/gpt-oss-120b",  # reliable tool-calling; openai/gpt-oss-20b is the lighter fallback
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
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        # Must stay a free model: the account has only a few dollars of credit, and a paid model id here
        # would quietly spend it. See docs/switching-model-providers.md for how this one was picked.
        "default_model": "openrouter/free",
        "api_key_env": "OPENROUTER_API_KEY",
    },
}

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelEndpoint:
    """One OpenAI-compatible endpoint a chat request can be sent to."""

    provider: str
    base_url: str
    model: str
    api_key: str = field(repr=False)  # endpoints end up in logs and tracebacks; the key must not


def _provider_key(preset: dict[str, str | None]) -> str | None:
    name = preset["api_key_env"]
    return (os.getenv(name) or _MODEL_SERVICE_VARS.get(name)) if name else None


MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "local")
if MODEL_PROVIDER not in PROVIDER_PRESETS:
    log.warning("MODEL_PROVIDER=%r is not a known provider (%s); using 'local'", MODEL_PROVIDER, ", ".join(PROVIDER_PRESETS))
_preset = PROVIDER_PRESETS.get(MODEL_PROVIDER, PROVIDER_PRESETS["local"])

MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", _preset["base_url"]).rstrip("/")
MODEL_NAME = os.getenv("MODEL_NAME", _preset["default_model"])
MODEL_API_KEY = os.getenv("MODEL_API_KEY") or _provider_key(_preset) or "not-needed"


def _fallback_endpoints() -> list[ModelEndpoint]:
    """MODEL_FALLBACK_PROVIDERS="openrouter,gemini": where a request goes, in order, when the one
    before it can't serve it (rate limit, outage, unreachable). Each uses its preset's default model
    and its own key. A provider without a key is skipped with a warning rather than failing later."""
    endpoints = []
    for name in (n.strip() for n in os.getenv("MODEL_FALLBACK_PROVIDERS", "").split(",")):
        if not name:
            continue
        preset = PROVIDER_PRESETS.get(name)
        if preset is None:
            raise ValueError(f"MODEL_FALLBACK_PROVIDERS: unknown provider {name!r} (known: {', '.join(PROVIDER_PRESETS)})")
        key = _provider_key(preset)
        if name != "local" and not key:
            log.warning("fallback provider %r skipped: no %s in the environment or %s", name, preset["api_key_env"], MODEL_SERVICE_ENV)
            continue
        endpoints.append(ModelEndpoint(name, preset["base_url"], preset["default_model"], key or "not-needed"))
    return endpoints


# The primary first, then the fallbacks in order. Only backend/app/llm.py reads this.
MODEL_ENDPOINTS: list[ModelEndpoint] = [
    ModelEndpoint(MODEL_PROVIDER, MODEL_BASE_URL, MODEL_NAME, MODEL_API_KEY),
    *_fallback_endpoints(),
]

# Per-agent names, one constant each as agents are added (see backend/app/agents/).
DATA_AGENT_NAME = "DataAgent"
STATISTICIAN_AGENT_NAME = "Statistician"
