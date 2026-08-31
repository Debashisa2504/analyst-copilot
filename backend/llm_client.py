"""
backend/llm_client.py
----------------------
Thin, provider-agnostic wrapper so the Draft/Verify pipeline (ADR-003)
isn't locked to one vendor's SDK. Configure via LLM_PROVIDER + the
matching API key in config.py / .env. All providers are asked to return
raw JSON so answerer.py can parse a single, stable shape.
"""
from __future__ import annotations

import json
import logging
import re
import time
from threading import Lock
from typing import Any, Dict

from . import config

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_REQUEST_TIMEOUT_S = 30


def _strip_json_fence(text: str) -> str:
    match = _JSON_FENCE_RE.search(text)
    return match.group(1).strip() if match else text.strip()


def _parse_json_response(raw: str) -> Dict[str, Any]:
    cleaned = _strip_json_fence(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Best-effort recovery: grab the first {...} block.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def call_llm_json(
    system_prompt: str, user_prompt: str, model: str, provider: str | None = None
) -> Dict[str, Any]:
    """
    Calls the configured provider and parses a JSON object from its reply.
    `provider` overrides config.LLM_PROVIDER for this call — used to route
    Draft and Verify passes to different providers (e.g. Gemini for Draft,
    Azure OpenAI for Verify).
    If LLM_FALLBACK_PROVIDER is set and the primary provider raises (network
    error, auth failure, timeout, outage), retries once against the
    fallback provider -- typically "ollama" for a fully local/offline
    degrade path -- instead of failing the whole /answer request.
    """
    active_provider = provider or config.LLM_PROVIDER
    try:
        raw = _dispatch(active_provider, system_prompt, user_prompt, model)
        return _parse_json_response(raw)
    except Exception as primary_error:
        if not config.LLM_FALLBACK_PROVIDER:
            raise
        logger.warning(
            "Primary provider '%s' (model=%s) failed: %s -- falling back to '%s' (model=%s)",
            active_provider, model, primary_error,
            config.LLM_FALLBACK_PROVIDER, config.FALLBACK_MODEL,
        )
        try:
            raw = _dispatch(
                config.LLM_FALLBACK_PROVIDER, system_prompt, user_prompt, config.FALLBACK_MODEL
            )
            return _parse_json_response(raw)
        except Exception as fallback_error:
            raise RuntimeError(
                f"Primary provider '{active_provider}' failed ({primary_error}); "
                f"fallback provider '{config.LLM_FALLBACK_PROVIDER}' also failed "
                f"({fallback_error})"
            ) from fallback_error


def _dispatch(provider: str, system_prompt: str, user_prompt: str, model: str) -> str:
    logger.info("llm_client dispatching provider=%s model=%s", provider, model)
    if provider == "azure_openai":
        return _call_azure_openai(system_prompt, user_prompt, model)
    if provider == "anthropic":
        return _call_anthropic(system_prompt, user_prompt, model)
    if provider == "openai":
        return _call_openai(system_prompt, user_prompt, model)
    if provider == "gemini":
        return _call_gemini(system_prompt, user_prompt, model)
    if provider == "ollama":
        return _call_ollama(system_prompt, user_prompt, model)
    raise ValueError(f"Unknown LLM provider: {provider}")


def _call_azure_openai(system_prompt: str, user_prompt: str, model: str) -> str:
    """
    Calls Azure OpenAI (GPT-4o) via the openai SDK's AzureOpenAI client.
    `model` is the Azure deployment name (DRAFT_MODEL / VERIFY_MODEL in .env),
    not the underlying model family string.
    Lazy import matches the pattern used by all other providers in this file.
    """
    from openai import AzureOpenAI

    client = AzureOpenAI(
        azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
        api_key=config.AZURE_OPENAI_API_KEY,
        api_version=config.AZURE_OPENAI_API_VERSION,
        timeout=_REQUEST_TIMEOUT_S,
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def _call_anthropic(system_prompt: str, user_prompt: str, model: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=_REQUEST_TIMEOUT_S)
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def _call_openai(system_prompt: str, user_prompt: str, model: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY, timeout=_REQUEST_TIMEOUT_S)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


class _RateLimiter:
    """
    Simple client-side token-spacing limiter: blocks just long enough to
    keep calls under `calls_per_minute`. Cheaper than hitting a 429 and
    retrying, and keeps a free-tier project from tripping abuse detection
    via bursty request patterns. Thread-safe (FastAPI can serve requests
    from multiple worker threads even though answerer.py calls this
    synchronously inside an async endpoint).
    """

    def __init__(self, calls_per_minute: int) -> None:
        self._min_interval = 60.0 / max(calls_per_minute, 1)
        self._lock = Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            remaining = self._min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
            self._last_call = time.monotonic()


_gemini_limiter = _RateLimiter(config.GEMINI_RPM_LIMIT)


def _is_retryable_error(exc: Exception) -> bool:
    """
    Best-effort detection of a transient error worth retrying -- both
    429/quota (rate limit) and 503/UNAVAILABLE ("high demand") responses.
    Checked by exception type name and message content since the exact
    exception classes have moved between google-generativeai SDK versions
    and this avoids a hard dependency on google.api_core.
    """
    name = type(exc).__name__.lower()
    if any(s in name for s in ("resourceexhausted", "quota", "ratelimit", "serviceunavailable", "unavailable")):
        return True
    msg = str(exc).lower()
    return any(s in msg for s in ("429", "quota", "rate limit", "503", "unavailable", "high demand"))


def _call_gemini(system_prompt: str, user_prompt: str, model: str) -> str:
    """
    Calls Gemini with client-side rate limiting (GEMINI_RPM_LIMIT) to stay
    under the free-tier cap proactively, plus exponential backoff retry
    (GEMINI_MAX_RETRIES) for transient errors -- 429/quota (rate limited)
    and 503/UNAVAILABLE ("high demand", common on the -latest alias) --
    other errors (bad key, invalid request) raise immediately since
    retrying won't help.
    """
    import google.generativeai as genai

    genai.configure(api_key=config.GEMINI_API_KEY)
    gmodel = genai.GenerativeModel(model, system_instruction=system_prompt)

    last_error: Exception | None = None
    for attempt in range(config.GEMINI_MAX_RETRIES):
        _gemini_limiter.wait()
        try:
            resp = gmodel.generate_content(
                user_prompt, request_options={"timeout": _REQUEST_TIMEOUT_S}
            )
            return resp.text
        except Exception as exc:
            last_error = exc
            if not _is_retryable_error(exc):
                raise
            backoff_s = 2 ** attempt
            logger.warning(
                "Gemini transient error (attempt %d/%d) -- backing off %ds: %s",
                attempt + 1, config.GEMINI_MAX_RETRIES, backoff_s, exc,
            )
            time.sleep(backoff_s)

    raise RuntimeError(
        f"Gemini still failing after {config.GEMINI_MAX_RETRIES} attempts (transient errors)"
    ) from last_error


def _call_ollama(system_prompt: str, user_prompt: str, model: str) -> str:
    """
    Calls a local Ollama daemon (https://ollama.com) via its native /api/chat
    endpoint. Requires `ollama serve` running and the model already pulled
    (`ollama pull <model>`) -- fully offline after that. `format: "json"`
    is Ollama's structured-output mode: it constrains decoding to valid
    JSON, which matters here since a local model is otherwise more likely
    to wrap its answer in prose than the hosted providers are.
    """
    import requests

    resp = requests.post(
        f"{config.OLLAMA_HOST}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
        },
        timeout=_REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]
