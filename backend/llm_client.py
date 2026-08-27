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
import re
from typing import Any, Dict

from . import config

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


def _call_gemini(system_prompt: str, user_prompt: str, model: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=config.GEMINI_API_KEY)
    gmodel = genai.GenerativeModel(model, system_instruction=system_prompt)
    resp = gmodel.generate_content(
        user_prompt, request_options={"timeout": _REQUEST_TIMEOUT_S}
    )
    return resp.text


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
