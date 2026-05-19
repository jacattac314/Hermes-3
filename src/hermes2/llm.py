from __future__ import annotations

import json
import os
from typing import Any
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    """Raised when a model invocation fails."""


def chat_openai_compatible(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise LLMError(f"{model} failed with HTTP {exc.code}: {body[:1000]}") from exc
    except OSError as exc:
        raise LLMError(f"{model} request failed at {url}: {exc}") from exc

    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"{model} returned an unexpected response shape") from exc


def chat_anthropic(
    *,
    model: str,
    content: str,
    api_key: str,
    temperature: float,
    max_tokens: int,
) -> str:
    if not api_key:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise LLMError("anthropic package is not installed") from exc

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": content}],
    )
    parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", "") == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(parts).strip()


def invoke_model(
    *,
    model_cfg: dict[str, Any],
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str:
    provider = str(model_cfg.get("provider") or "").lower()
    model = str(model_cfg["model"])

    if provider == "anthropic":
        api_key = os.environ.get(str(model_cfg.get("api_key_env") or "ANTHROPIC_API_KEY"), "")
        return chat_anthropic(
            model=model,
            content=prompt,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    if provider in {"lmstudio", "ollama", "local", "openai"}:
        base_url = str(model_cfg.get("base_url") or "https://api.openai.com/v1")
        api_key_env = str(model_cfg.get("api_key_env") or ("OPENAI_API_KEY" if provider == "openai" else "LMSTUDIO_API_KEY"))
        api_key = os.environ.get(api_key_env) or ("local" if provider in {"lmstudio", "ollama", "local"} else "")
        return chat_openai_compatible(
            base_url=base_url,
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a reliable Hermes 2.0 workflow step. Return concise Markdown.",
                },
                {"role": "user", "content": prompt},
            ],
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    raise LLMError(f"unsupported provider: {provider}")
