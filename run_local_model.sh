#!/usr/bin/env bash
# Start the local model that MODEL_PROVIDER=local expects: Qwen2.5-Coder-7B-Instruct, Q2_K quantization,
# served by llama-server on 127.0.0.1:8080 (OpenAI-compatible API, /v1).
# Usage (from databench/, keep this terminal open while you use the app):  ./run_local_model.sh
#
# Not model_service/run_model.sh: that one serves Qwen3-4B behind an API key for the Azure tunnel.
#
# Flags are the ones that worked on the 4 GB RTX 3050 (see docs/PLAN.md, benchmark section):
#   --jinja       needed for tool calling (the agents are all tools)
#   -c 4096       context window; 4 GB of VRAM is the limit
#   --parallel 1  llama-server defaults to 4 slots, which would split -c four ways
#   no -ngl       let llama.cpp fit as many layers on the GPU as actually fit; forcing 99 OOMs some 7B models
#   no --api-key  the server only listens on 127.0.0.1 (Docker uses host networking, so it reaches it too)
set -euo pipefail
cd "$(dirname "$0")"

MODEL_FILE="${MODEL_FILE:-../gguf_models/qwen2.5-coder-7b-instruct-q2_k.gguf}"
MODEL_ALIAS="${MODEL_ALIAS:-qwen2.5-coder-7b}" # the local preset's default MODEL_NAME in backend/app/config.py
PORT="${PORT:-8080}"                          # the local preset's base URL is http://127.0.0.1:8080/v1
CTX="${CTX:-4096}"

command -v llama-server >/dev/null || { echo "llama-server is not on PATH" >&2; exit 1; }
[ -f "${MODEL_FILE}" ] || { echo "Model file not found: ${MODEL_FILE}" >&2; exit 1; }
if ss -ltn "sport = :${PORT}" | tail -n +2 | grep -q .; then
  echo "Port ${PORT} is already in use - is llama-server already running? (curl http://127.0.0.1:${PORT}/health)" >&2
  exit 1
fi

echo ">> llama-server on http://127.0.0.1:${PORT}/v1 - model '${MODEL_ALIAS}' from ${MODEL_FILE}"
echo ">> ready when you see 'server is listening'; check with: curl http://127.0.0.1:${PORT}/health"
exec llama-server \
  -m "${MODEL_FILE}" \
  --alias "${MODEL_ALIAS}" \
  --jinja \
  -c "${CTX}" \
  --parallel 1 \
  --host 127.0.0.1 --port "${PORT}"
