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

from .agents.data_agent.tools.connectors import DataSourceConnector, LocalFileConnector, PostgresConnector

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

def _dsn_is_this_machine(dsn: str) -> bool:
    """True if every host of a Postgres DSN is loopback or a unix socket directory (or none is given)."""
    from psycopg.conninfo import conninfo_to_dict

    try:
        host = conninfo_to_dict(dsn).get("host") or ""
    except Exception:  # not parseable: leave it to the connector to say so
        return False
    hosts = [h.strip() for h in str(host).split(",")] or [""]
    return all(h in ("", "localhost", "127.0.0.1", "::1") or h.startswith("/") for h in hosts)


# What every user sees: the sample data/ folder baked into the image, and the Postgres database if
# one is configured. A user's own uploads are added per request (agents/data_agent/tools/scope.py).
SHARED_CONNECTORS: list[DataSourceConnector] = [LocalFileConnector(DATA_DIR)]
if POSTGRES_DSN:
    SHARED_CONNECTORS.append(PostgresConnector(POSTGRES_DSN, schema=POSTGRES_SCHEMA))

# --- Run mode: where the app's own state (users, chats, files) lives ---
# "local": a SQLite file and plain folders on this machine. "azure": Cosmos DB and Blob containers,
# the stack in terraform/. One switch, not three independent variables: a half-configured Azure app
# fails at startup instead of quietly writing to an ephemeral disk. Unrelated to MODEL_PROVIDER (the
# `local` preset there is the llama-server); either run mode can use any model provider.
APP_ENV = os.getenv("APP_ENV", "local").strip().lower()
if APP_ENV not in ("local", "azure"):
    raise ValueError(f"APP_ENV={APP_ENV!r} is not a run mode (use 'local' or 'azure').")

COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT") or None
COSMOS_DATABASE = os.getenv("COSMOS_DATABASE", "databench")
AZURE_STORAGE_ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT_NAME") or None
AZURE_STORAGE_ACCOUNT_URL = (
    f"https://{AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net" if AZURE_STORAGE_ACCOUNT_NAME else None
)
# Signs the login tokens. Azure: Terraform's random_password. Local: made and kept in VAR_DIR on first use (auth.py).
AUTH_SECRET = os.getenv("AUTH_SECRET") or None

if APP_ENV == "azure":
    _missing = [
        name
        for name, value in (
            ("COSMOS_ENDPOINT", COSMOS_ENDPOINT),
            ("AZURE_STORAGE_ACCOUNT_NAME", AZURE_STORAGE_ACCOUNT_NAME),
            ("AUTH_SECRET", AUTH_SECRET),
        )
        if not value
    ]
    if _missing:
        raise ValueError(f"APP_ENV=azure needs {', '.join(_missing)} (Terraform sets them on the backend Web App).")
    if POSTGRES_DSN and _dsn_is_this_machine(POSTGRES_DSN):  # the message must not repeat the DSN: it holds a password
        raise ValueError("APP_ENV=azure but POSTGRES_DSN points at this machine (localhost / a socket), which the Web App can't reach.")
else:
    _azure_only = [n for n, v in (("COSMOS_ENDPOINT", COSMOS_ENDPOINT), ("AZURE_STORAGE_ACCOUNT_NAME", AZURE_STORAGE_ACCOUNT_NAME)) if v]
    if _azure_only:  # mixed config: some of it would be used and some ignored, silently
        raise ValueError(f"APP_ENV=local but {', '.join(_azure_only)} is set. Set APP_ENV=azure, or remove it.")

# Local state that is not user data: the SQLite database and the login signing key (gitignored).
VAR_DIR = Path(os.getenv("VAR_DIR", BASE_DIR / "var"))
APP_DB_PATH = VAR_DIR / "databench.db"
# Local mode: one folder per user for their tables and charts. Next to databench/, outside the repo
# (same trick as MODEL_SERVICE_ENV above; inside the container BASE_DIR is /app, so this is /users).
USERS_DIR = Path(os.getenv("USERS_DIR", BASE_DIR.parent / "users"))

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
AUTH_TOKEN_HOURS = int(os.getenv("AUTH_TOKEN_HOURS", "12"))

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


MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "local").strip().lower()
if MODEL_PROVIDER not in PROVIDER_PRESETS:  # a typo must not quietly become the llama-server at 127.0.0.1:8080
    raise ValueError(f"MODEL_PROVIDER={MODEL_PROVIDER!r} is not a known provider (known: {', '.join(PROVIDER_PRESETS)}).")
_preset = PROVIDER_PRESETS[MODEL_PROVIDER]

MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", _preset["base_url"]).rstrip("/")
# The model of the primary provider: MODEL_NAME (explicit, wins whatever the provider), else the
# provider's own <PROVIDER>_MODEL (OPENROUTER_MODEL, GROQ_MODEL, ...), else the preset's default. Prefer
# <PROVIDER>_MODEL: it only applies while that provider is the primary one, so changing MODEL_PROVIDER
# alone can't send one provider another's model id (a 404, which doesn't fail over).
MODEL_NAME_ENV = f"{MODEL_PROVIDER.upper()}_MODEL"
MODEL_NAME = os.getenv("MODEL_NAME") or os.getenv(MODEL_NAME_ENV) or _preset["default_model"]
MODEL_API_KEY = os.getenv("MODEL_API_KEY") or _provider_key(_preset) or "not-needed"

_own_key = _provider_key(_preset) if _preset["api_key_env"] != "MODEL_API_KEY" else None
if os.getenv("MODEL_API_KEY") and _own_key and os.getenv("MODEL_API_KEY") != _own_key:
    log.warning(
        "MODEL_API_KEY is set and wins over %s, so %s's own key is ignored. Unset MODEL_API_KEY unless that is intended.",
        _preset["api_key_env"], MODEL_PROVIDER,
    )


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

if APP_ENV == "azure" and any(e.provider == "local" for e in MODEL_ENDPOINTS):
    # `local` is the llama-server on 127.0.0.1:8080 of a developer machine; a Web App has none.
    raise ValueError("APP_ENV=azure but the model provider list includes 'local' (MODEL_PROVIDER / MODEL_FALLBACK_PROVIDERS).")


def describe() -> str:
    """One line for the startup log: the settings that make a local run differ from a deployed one. No secrets."""
    model = " -> ".join(f"{e.provider}:{e.model}" for e in MODEL_ENDPOINTS)
    state = (
        f"cosmos={COSMOS_DATABASE}, blob={AZURE_STORAGE_ACCOUNT_NAME}"
        if APP_ENV == "azure"
        else f"sqlite={APP_DB_PATH}, files={USERS_DIR}"
    )
    return (
        f"run mode={APP_ENV} ({state}) | model={model} | rows<={MAX_ROWS_RETURNED}, chart above {CHART_MIN_ROWS} rows | "
        f"upload<={MAX_UPLOAD_MB}MB | login={AUTH_TOKEN_HOURS}h | postgres={'on' if POSTGRES_DSN else 'off'}"
    )


# Per-agent names, one constant each as agents are added (see backend/app/agents/).
DATA_AGENT_NAME = "DataAgent"
STATISTICIAN_AGENT_NAME = "Statistician"
