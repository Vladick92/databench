"""The one place a chat client is built from config, so every agent talks to the same provider
(MODEL_PROVIDER preset or explicit MODEL_* overrides - see docs/switching-model-providers.md), and
the one place that knows about failing over to another provider.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

import httpx2  # not httpx: the OpenAI SDK (3.x) is built on httpx2, and its client only accepts httpx2 transports
from agent_framework.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI, DefaultAsyncHttpxClient

if TYPE_CHECKING:  # config is imported inside build_chat_client, see there
    from backend.app.config import ModelEndpoint

log = logging.getLogger(__name__)

# Groq's free tier allows 8k tokens per minute for every model, and one question that gets a chart
# costs about that much (two agents, several model calls). When the limit is hit the API says how
# long to wait (retry-after, a few seconds); the SDK honours it. Its default of 2 retries gave up
# too early and crashed the stream, so keep retrying for up to about a minute instead. With
# fallback providers configured, each of these attempts walks the whole provider chain first.
_MAX_RETRIES = 6


def _cannot_serve_now(status: int) -> bool:
    """The request is fine, this provider just can't take it right now: timeout, rate limit, outage.
    Never a 4xx like 401/404/422 - those are mistakes in our config or request, and hiding them
    behind a fallback would only make them harder to find."""
    return status in (408, 429) or status >= 500


# Replies carry provider-specific extras on assistant messages: OpenRouter adds `reasoning_details`,
# some others `reasoning` / `reasoning_content`. The framework keeps them in the conversation and replays
# them, and a different provider rejects the unknown property (Groq: HTTP 400 "reasoning_details is
# unsupported"). The history we send has to work on every provider in the chain, so these are dropped.
_PROVIDER_SPECIFIC_MESSAGE_KEYS = ("reasoning_details", "reasoning_content", "reasoning")


def _portable(body: bytes) -> bytes:
    """`body` without the provider-specific message keys; returned untouched if it has none."""
    if not any(f'"{key}"'.encode() in body for key in _PROVIDER_SPECIFIC_MESSAGE_KEYS):
        return body
    try:
        payload = json.loads(body)
    except ValueError:
        return body
    if not isinstance(payload, dict):
        return body
    for message in payload.get("messages") or []:
        if isinstance(message, dict):
            for key in _PROVIDER_SPECIFIC_MESSAGE_KEYS:
                message.pop(key, None)
    return json.dumps(payload).encode()


def _with_body(request: httpx2.Request, body: bytes) -> httpx2.Request:
    """The same request with a different body."""
    headers = {k: v for k, v in request.headers.items() if k.lower() != "content-length"}
    return httpx2.Request(request.method, request.url, headers=headers, content=body, extensions=request.extensions)


def _retarget(request: httpx2.Request, body: bytes, source: ModelEndpoint, target: ModelEndpoint) -> httpx2.Request:
    """The same request addressed to `target`: its URL, its key, its model name."""
    source_base = httpx2.URL(source.base_url.rstrip("/") + "/")
    target_base = httpx2.URL(target.base_url.rstrip("/") + "/")
    tail = request.url.path[len(source_base.path):]  # e.g. "chat/completions"
    url = target_base.copy_with(path=target_base.path + tail, query=request.url.query)

    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length", "authorization")}
    headers["authorization"] = f"Bearer {target.api_key}"

    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    if isinstance(payload, dict) and "model" in payload:
        payload["model"] = target.model
        body = json.dumps(payload).encode()
    # extensions carry the SDK's timeouts; a new request without them would wait forever
    return httpx2.Request(request.method, url, headers=headers, content=body, extensions=request.extensions)


_MAX_SCREEN_BYTES = 64 * 1024  # how much of a stream's start is read while looking for an error event


def _first_sse_data(buffer: bytes) -> str | None:
    """The data of the first complete event in `buffer` that has any, or None if there isn't one yet.
    Comment-only events (OpenRouter sends ': OPENROUTER PROCESSING' keep-alives) are skipped."""
    text = buffer.decode("utf-8", "replace")
    for event in re.split(r"\r?\n\r?\n", text)[:-1]:  # the last piece has no blank line after it yet
        data = [line[5:].lstrip() for line in event.splitlines() if line.startswith("data:")]
        if data:
            return "\n".join(data)
    return None


def _in_band_error(data: str) -> tuple[int, dict] | None:
    """(HTTP status, response body) if this event is an error, as the OpenAI SDK would see it."""
    try:
        payload = json.loads(data)
    except ValueError:
        return None
    if not isinstance(payload, dict) or not payload.get("error"):
        return None
    error = payload["error"] if isinstance(payload["error"], dict) else {"message": str(payload["error"])}
    code = str(error.get("code", ""))
    status = int(code) if code.isdigit() and 400 <= int(code) <= 599 else 502
    return status, {"error": error}


class _Replay(httpx2.AsyncByteStream):
    """The body of a response whose first chunks were already read to look at them."""

    def __init__(self, head: bytes, rest, original: httpx2.AsyncByteStream):
        self._head, self._rest, self._original = head, rest, original

    async def __aiter__(self):
        if self._head:
            yield self._head
        async for chunk in self._rest:
            yield chunk

    async def aclose(self) -> None:
        close = getattr(self._rest, "aclose", None)  # the generator we peeked through, then the connection under it
        if close is not None:
            await close()
        await self._original.aclose()


async def _screen_stream(response: httpx2.Response) -> httpx2.Response:
    """OpenRouter reports an upstream failure ("Service temporarily overloaded") as an error event
    inside a 200 event stream. The SDK raises that as an APIError that nothing retries. If the very
    first event is such an error, return the equivalent HTTP error instead, so failover and the SDK's
    retries treat it like any other failed request. Anything unrecognised is passed through untouched."""
    if (
        response.status_code != 200
        or "text/event-stream" not in response.headers.get("content-type", "")
        or response.headers.get("content-encoding", "identity") != "identity"
    ):
        return response
    chunks = response.stream.__aiter__()
    buffered, first = b"", None
    while first is None and len(buffered) < _MAX_SCREEN_BYTES:
        try:
            buffered += await chunks.__anext__()
        except StopAsyncIteration:
            break
        first = _first_sse_data(buffered)
    error = _in_band_error(first) if first is not None else None
    if error is not None:
        await response.aclose()
        status, body = error
        return httpx2.Response(status, headers={"content-type": "application/json"}, content=json.dumps(body).encode())
    return httpx2.Response(200, headers=response.headers, stream=_Replay(buffered, chunks, response.stream), extensions=response.extensions)


class FailoverTransport(httpx2.AsyncBaseTransport):
    """Sends each request to the first endpoint that can serve it.

    Works below the SDK, on the raw HTTP request, so it covers every model call of every agent -
    including calls in the middle of a tool loop - and streaming needs no special care: a provider
    that can't serve a request says so in the response status (or is unreachable) before any of the
    body has been sent (an error event as the first thing in a 200 stream counts as a failure too,
    see _screen_stream). If every endpoint fails, the last one's answer goes back to the SDK, whose
    own retries then start the chain over.
    """

    def __init__(self, endpoints: list[ModelEndpoint], inner: httpx2.AsyncBaseTransport | None = None):
        self._endpoints = endpoints
        self._inner = inner or httpx2.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        original = await request.aread()
        body = _portable(original)
        if body is not original:
            request = _with_body(request, body)
        primary = self._endpoints[0]
        for i, endpoint in enumerate(self._endpoints):
            last = i == len(self._endpoints) - 1
            outgoing = request if i == 0 else _retarget(request, body, primary, endpoint)
            try:
                response = await _screen_stream(await self._inner.handle_async_request(outgoing))
            except httpx2.TransportError as e:  # connection refused, DNS, timeout before any response
                if last:
                    raise
                log.warning("%s unreachable (%s); falling back to %s", endpoint.provider, type(e).__name__, self._endpoints[i + 1].provider)
                continue
            if last or not _cannot_serve_now(response.status_code):
                if i:
                    log.info("answered by fallback provider %s (%s)", endpoint.provider, endpoint.model)
                return response
            log.warning("%s answered HTTP %d; falling back to %s", endpoint.provider, response.status_code, self._endpoints[i + 1].provider)
            await response.aclose()
        raise AssertionError("unreachable: the last endpoint either returns or raises")

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_chat_client() -> OpenAIChatCompletionClient:
    # Not a module-level import: config imports the data agent's connectors, whose package imports
    # agent.py, which imports this module - importing config here at the top would be a cycle.
    from backend.app import config

    primary, *fallbacks = config.MODEL_ENDPOINTS
    return OpenAIChatCompletionClient(
        model=primary.model,
        async_client=AsyncOpenAI(
            base_url=primary.base_url,
            api_key=primary.api_key,
            max_retries=_MAX_RETRIES,
            http_client=DefaultAsyncHttpxClient(transport=FailoverTransport(config.MODEL_ENDPOINTS)) if fallbacks else None,
        ),
    )
