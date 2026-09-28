"""Benchmark harness: speed + text-to-SQL quality for local GGUF models via llama-server.

Usage (from databench/, with the venv active):
    python eval/bench.py

Launches each configured model in llama-server one at a time (only one fits in 4GB VRAM at a
time anyway), runs the eval/cases.py set against it, records per-case latency/tokens-per-second
(from llama-server's own `timings`) and pass/fail, then moves to the next model. Results are
printed as a summary table and saved to eval/results/<timestamp>.json.

Launch parameters (see docs/PLAN.md benchmark section for the full reasoning):
- No explicit `-ngl`: forcing `-ngl 99` (full GPU offload) OOMs loading codellama-7b.Q2_K on
  this 4GB card - CodeLlama-7B uses full multi-head attention (no GQA), so its KV cache per
  token is much larger than Qwen2.5-Coder's (GQA), and llama.cpp's own device-fit heuristic
  explicitly aborts ("n_gpu_layers already set by user to 99, abort") when you hand it a fixed
  value instead of letting it choose. Omitting -ngl lets llama.cpp auto-fit layers to whatever
  VRAM is actually free; qwen2.5-coder still ends up fully offloaded, codellama-7b partially.
- `-c 4096`: matches model_service/run_model.sh's existing "4GB card: keep <= 4096" rule.
- `--parallel 1`: pins the whole context to one slot. llama-server defaults to n_parallel=4,
  which silently divides -c across 4 slots - fine for concurrent users, misleading for a
  single-request benchmark.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.app.tools.sql_guard import SQLValidationError, validate_readonly_sql  # noqa: E402
from eval.cases import CASES, EvalCase  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
GGUF_DIR = REPO_ROOT.parent / "gguf_models"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
PORT = 8080
HOST = "127.0.0.1"
CTX = 4096  # see module docstring

MODELS = [
    {"name": "codellama-7b-Q2_K", "file": GGUF_DIR / "codellama-7b.Q2_K.gguf"},
    {"name": "qwen2.5-coder-7b-Q2_K", "file": GGUF_DIR / "qwen2.5-coder-7b-instruct-q2_k.gguf"},
]

_READERS = {".csv": pd.read_csv, ".xlsx": pd.read_excel}
_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)

SYSTEM_PROMPT = (
    "You are a text-to-SQL assistant. You are given one table's name, its columns, and a "
    "question. Reply with exactly one fenced ```sql code block containing a single read-only "
    "SELECT statement (DuckDB SQL dialect) that answers the question. No explanation, no other "
    "text outside the code block."
)


@dataclass
class CaseResult:
    case_id: str
    ok: bool
    reason: str
    raw_sql: str | None
    latency_s: float
    tokens_per_second: float | None
    prompt_tokens_per_second: float | None


def start_server(model_file: Path, ctx: int = CTX) -> subprocess.Popen:
    log_path = RESULTS_DIR / f"{model_file.stem}.server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "w")
    proc = subprocess.Popen(
        [
            "llama-server",
            "-m", str(model_file),
            "--alias", model_file.stem,
            "--jinja",
            # no explicit -ngl: let llama.cpp auto-fit layers to free VRAM (see module docstring)
            "-c", str(ctx),
            "--parallel", "1",
            "--host", HOST,
            "--port", str(PORT),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://{HOST}:{PORT}/health", timeout=2)
            if r.status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited early, see {log_path}")
        time.sleep(1)
    raise TimeoutError(f"llama-server did not become ready in time, see {log_path}")


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def table_schema(source_name: str) -> tuple[Path, dict[str, str]]:
    for suffix, reader in _READERS.items():
        path = DATA_DIR / f"{source_name}{suffix}"
        if path.exists():
            sample = reader(path, nrows=20)
            return path, {c: str(t) for c, t in sample.dtypes.items()}
    raise FileNotFoundError(source_name)


def run_sql(path: Path, source_name: str, sql: str) -> list[tuple]:
    reader = _READERS[path.suffix]
    df = reader(path)
    con = duckdb.connect(":memory:")
    try:
        con.register(source_name, df)
        result = con.execute(sql).fetchdf()
    finally:
        con.close()
    rows = [tuple(round(v, 2) if isinstance(v, float) else v for v in row) for row in result.itertuples(index=False)]
    return sorted(rows, key=repr)


_TEMPLATE_LEAKAGE = re.compile(r"<\|im_(?:start|end)\|>.*", re.DOTALL)


def extract_sql(content: str) -> str:
    content = content.strip()
    match = _SQL_FENCE.search(content)
    if match:
        return match.group(1).strip()
    # No closing fence - likely truncated by the "\n```" stop sequence before it appeared.
    # Strip a leading fence marker so we don't try to parse "```sql\nSELECT ..." as SQL.
    content = re.sub(r"^```(?:sql)?\s*", "", content)
    # codellama-7b.Q2_K.gguf has no matching chat template for its tokenizer's special tokens
    # (see docs/PLAN.md benchmark notes): it never emits a real stop and free-runs into a
    # hallucinated next turn, literally spelling out "<|im_start|>"/"<|im_end|>" as text. The
    # real answer is what came before that - strip it rather than let it break SQL parsing.
    content = _TEMPLATE_LEAKAGE.sub("", content)
    return content.strip()


def ask_model(source_name: str, columns: dict[str, str], question: str) -> tuple[str, dict]:
    user = f"Table `{source_name}` columns: {columns}\n\nQuestion: {question}"
    r = httpx.post(
        f"http://{HOST}:{PORT}/v1/chat/completions",
        json={
            "model": "bench",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            # A one-line SELECT needs well under 100 tokens. Both a hard cap and a stop
            # sequence are needed: codellama-7b at Q2_K was observed rambling past 1500+
            # tokens with no request-side limit, past its own closing ``` fence.
            "max_tokens": 200,
            "stop": ["\n```"],  # closes the fenced block; opening fence is "```sql\n", won't match
        },
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    return content, data.get("timings", {})


def run_case(case: EvalCase) -> CaseResult:
    path, columns = table_schema(case.source_name)
    t0 = time.time()
    raw_sql = None
    tps = ptps = None
    try:
        content, timings = ask_model(case.source_name, columns, case.question)
        latency = time.time() - t0
        raw_sql = extract_sql(content)
        tps = timings.get("predicted_per_second")
        ptps = timings.get("prompt_per_second")

        validated = validate_readonly_sql(raw_sql, dialect="duckdb")
        candidate_rows = run_sql(path, case.source_name, validated.sql)
        gold_rows = run_sql(path, case.source_name, case.gold_sql)
    except SQLValidationError as e:
        return CaseResult(case.id, False, f"invalid/unsafe SQL: {e}", raw_sql, time.time() - t0, tps, ptps)
    except Exception as e:
        # A benchmark run is long and unattended - one bad case (a hung request, a weird
        # DuckDB error) should show up as a FAIL, not take the rest of the run down with it.
        return CaseResult(case.id, False, f"{type(e).__name__}: {e}", raw_sql, time.time() - t0, tps, ptps)

    ok = candidate_rows == gold_rows
    reason = "match" if ok else f"mismatch: got {candidate_rows}, expected {gold_rows}"
    return CaseResult(case.id, ok, reason, raw_sql, time.time() - t0, tps, ptps)


def bench_model(model_file: Path) -> list[CaseResult]:
    print(f"\n=== {model_file.name} ===")
    proc = start_server(model_file)
    try:
        results = []
        for case in CASES:
            result = run_case(case)
            status = "PASS" if result.ok else "FAIL"
            print(f"  [{status}] {case.id:16s} {result.latency_s:5.1f}s  {result.reason}")
            results.append(result)
        return results
    finally:
        stop_server(proc)


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    all_results = {}
    for m in MODELS:
        model_file = m["file"]
        if not model_file.exists():
            print(f"Skipping {m['name']}: {model_file} not found")
            continue
        all_results[m["name"]] = [asdict(r) for r in bench_model(model_file)]

    out_path = RESULTS_DIR / f"bench_{int(time.time())}.json"
    out_path.write_text(json.dumps(all_results, indent=2))

    print("\n=== Summary ===")
    for name, results in all_results.items():
        n = len(results)
        passed = sum(1 for r in results if r["ok"])
        latencies = [r["latency_s"] for r in results]
        tps_values = [r["tokens_per_second"] for r in results if r["tokens_per_second"]]
        avg_tps = sum(tps_values) / len(tps_values) if tps_values else 0.0
        print(
            f"{name:24s} pass {passed}/{n}  avg latency {sum(latencies)/n:5.1f}s  "
            f"avg gen speed {avg_tps:5.1f} tok/s"
        )
    print(f"\nFull results: {out_path}")


if __name__ == "__main__":
    main()
