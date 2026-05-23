from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Callable
import urllib.error
import urllib.request


class ModelResolutionError(RuntimeError):
    """Raised when no usable local model can be selected."""


@dataclass(frozen=True)
class ModelResolution:
    provider: str
    model: str
    base_url: str
    reason: str
    available_models: list[str]
    rejected_candidates: list[str]


@dataclass(frozen=True)
class ModelCandidate:
    alias: str
    config: dict[str, Any]


ModelFetcher = Callable[[str, float], list[str]]


def parse_models_payload(payload: bytes | str) -> list[str]:
    raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    data = json.loads(raw)
    items = data.get("data", [])
    if not isinstance(items, list):
        return []
    models: list[str] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            models.append(item["id"])
    return models


def fetch_openai_models(base_url: str, timeout: float = 2.0) -> list[str]:
    url = f"{base_url.rstrip('/')}/models"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return parse_models_payload(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ModelResolutionError(f"model listing failed with HTTP {exc.code}: {body[:500]}") from exc
    except OSError as exc:
        raise ModelResolutionError(f"model listing failed for {url}: {exc}") from exc


def _runtime_base_url(config: dict[str, Any], model_cfg: dict[str, Any]) -> str:
    return (
        os.environ.get("HERMES2_LOCAL_BASE_URL")
        or model_cfg.get("base_url")
        or (config.get("runtime") or {}).get("local_base_url")
        or "http://127.0.0.1:1234/v1"
    )


def _choose_first_matching(available: list[str], *needles: str) -> str | None:
    for model in available:
        lowered = model.lower()
        if all(needle in lowered for needle in needles):
            return model
    return None


def resolve_local_model(
    config: dict[str, Any],
    model_cfg: dict[str, Any] | None = None,
    *,
    env: dict[str, str] | None = None,
    fetcher: ModelFetcher = fetch_openai_models,
    timeout: float | None = None,
) -> ModelResolution:
    env = env or os.environ
    model_cfg = model_cfg or ((config.get("models") or {}).get("local_worker") or {})
    base_url = _runtime_base_url(config, model_cfg)
    request_timeout = timeout if timeout is not None else float((config.get("runtime") or {}).get("request_timeout_seconds", 2))
    available = fetcher(base_url, request_timeout)
    if not available:
        raise ModelResolutionError(f"no models reported by {base_url.rstrip('/')}/models")

    rejected: list[str] = []
    for env_name in ("HERMES2_LOCAL_MODEL", "QWEN_MODEL"):
        candidate = (env.get(env_name) or "").strip()
        if not candidate:
            continue
        if candidate in available:
            return ModelResolution(
                provider=str(model_cfg.get("provider") or "lmstudio"),
                model=candidate,
                base_url=base_url,
                reason=f"selected from {env_name}",
                available_models=available,
                rejected_candidates=rejected,
            )
        rejected.append(f"{env_name}={candidate}")

    configured_model = str(model_cfg.get("model") or "").strip()
    if configured_model and configured_model != "auto":
        if configured_model in available:
            return ModelResolution(
                provider=str(model_cfg.get("provider") or "lmstudio"),
                model=configured_model,
                base_url=base_url,
                reason="selected from model alias config",
                available_models=available,
                rejected_candidates=rejected,
            )
        rejected.append(f"configured={configured_model}")

    preferred = str((config.get("runtime") or {}).get("preferred_local_model") or "").strip()
    if preferred:
        if preferred in available:
            return ModelResolution(
                provider=str(model_cfg.get("provider") or "lmstudio"),
                model=preferred,
                base_url=base_url,
                reason="selected preferred_local_model",
                available_models=available,
                rejected_candidates=rejected,
            )
        rejected.append(f"preferred_local_model={preferred}")

    qwen_coder = _choose_first_matching(available, "qwen", "coder")
    if qwen_coder:
        return ModelResolution(
            provider=str(model_cfg.get("provider") or "lmstudio"),
            model=qwen_coder,
            base_url=base_url,
            reason="selected first available Qwen Coder model",
            available_models=available,
            rejected_candidates=rejected,
        )

    qwen = _choose_first_matching(available, "qwen")
    if qwen:
        return ModelResolution(
            provider=str(model_cfg.get("provider") or "lmstudio"),
            model=qwen,
            base_url=base_url,
            reason="selected first available Qwen model",
            available_models=available,
            rejected_candidates=rejected,
        )

    for model in available:
        if "embed" not in model.lower():
            return ModelResolution(
                provider=str(model_cfg.get("provider") or "lmstudio"),
                model=model,
                base_url=base_url,
                reason="selected first non-embedding model",
                available_models=available,
                rejected_candidates=rejected,
            )

    raise ModelResolutionError("only embedding models are available; no chat model can be selected")


def effective_model_config(
    alias: str,
    config: dict[str, Any],
    *,
    fetcher: ModelFetcher = fetch_openai_models,
) -> dict[str, Any]:
    models = config.get("models") or {}
    if alias not in models:
        raise ModelResolutionError(f"unknown model alias: {alias}")
    model_cfg = dict(models[alias])
    provider = str(model_cfg.get("provider") or "").lower()
    if provider in {"lmstudio", "ollama", "local"}:
        resolution = resolve_local_model(config, model_cfg, fetcher=fetcher)
        model_cfg["model"] = resolution.model
        model_cfg["base_url"] = resolution.base_url
        model_cfg["_resolution"] = resolution
    model_cfg["_alias"] = alias
    return model_cfg


def profile_config(config: dict[str, Any], profile_name: str) -> dict[str, Any]:
    profiles = config.get("profiles") or {}
    if not profiles and profile_name == "default":
        return {"description": "Default profile", "model_chain": ["local_worker"]}
    if profile_name not in profiles:
        raise ModelResolutionError(f"unknown profile: {profile_name}")
    return dict(profiles[profile_name])


def validate_profile_workflow(config: dict[str, Any], profile_name: str, workflow_name: str) -> None:
    profile = profile_config(config, profile_name)
    workflows = profile.get("workflows") or []
    if workflows and workflow_name not in workflows:
        allowed = ", ".join(str(item) for item in workflows)
        raise ModelResolutionError(
            f"profile {profile_name!r} does not allow workflow {workflow_name!r}; allowed workflows: {allowed}"
        )


def profile_model_aliases(
    config: dict[str, Any],
    profile_name: str = "default",
    *,
    primary_alias: str | None = None,
) -> list[str]:
    profile = profile_config(config, profile_name)
    chain_value = profile.get("model_chain") or ["local_worker"]
    if isinstance(chain_value, str):
        chains = config.get("model_chains") or {}
        if chain_value not in chains:
            raise ModelResolutionError(f"profile {profile_name!r} references unknown model chain {chain_value!r}")
        aliases = list(chains[chain_value])
    elif isinstance(chain_value, list):
        aliases = list(chain_value)
    else:
        raise ModelResolutionError(f"profile {profile_name!r} has invalid model_chain")

    ordered: list[str] = []
    if primary_alias:
        ordered.append(primary_alias)
    ordered.extend(str(alias) for alias in aliases)
    deduped: list[str] = []
    for alias in ordered:
        if alias not in deduped:
            deduped.append(alias)
    return deduped


def provider_has_credentials(model_cfg: dict[str, Any], env: dict[str, str] | None = None) -> tuple[bool, str]:
    env = env or os.environ
    provider = str(model_cfg.get("provider") or "").lower()
    if provider in {"lmstudio", "ollama", "local"}:
        return True, ""
    default_key_env = {
        "openai": "OPENAI_API_KEY",
        "google": "GEMINI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }.get(provider, "ANTHROPIC_API_KEY")
    api_key_env = str(model_cfg.get("api_key_env") or default_key_env)
    if env.get(api_key_env):
        return True, ""
    return False, f"{api_key_env} is not set"


def effective_model_chain(
    *,
    primary_alias: str,
    config: dict[str, Any],
    profile_name: str = "default",
    fetcher: ModelFetcher = fetch_openai_models,
    env: dict[str, str] | None = None,
) -> tuple[list[ModelCandidate], list[str]]:
    aliases = profile_model_aliases(config, profile_name, primary_alias=primary_alias)
    candidates: list[ModelCandidate] = []
    skipped: list[str] = []
    models = config.get("models") or {}
    for alias in aliases:
        if alias not in models:
            skipped.append(f"{alias}: unknown model alias")
            continue
        ready, reason = provider_has_credentials(models[alias], env=env)
        if not ready:
            skipped.append(f"{alias}: {reason}")
            continue
        try:
            model_cfg = effective_model_config(alias, config, fetcher=fetcher)
        except ModelResolutionError as exc:
            skipped.append(f"{alias}: {exc}")
            continue
        candidates.append(ModelCandidate(alias=alias, config=model_cfg))
    if not candidates:
        detail = "; ".join(skipped) if skipped else "no candidate aliases configured"
        raise ModelResolutionError(f"no usable model candidates for profile {profile_name!r}: {detail}")
    return candidates, skipped
