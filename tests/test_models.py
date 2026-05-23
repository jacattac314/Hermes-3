from __future__ import annotations

import pytest

from hermes2.models import (
    ModelResolutionError,
    effective_model_chain,
    parse_models_payload,
    profile_model_aliases,
    resolve_local_model,
    validate_profile_workflow,
)


def test_parse_models_payload_extracts_ids() -> None:
    payload = b'{"data":[{"id":"a"},{"id":"b"},{"name":"ignored"}]}'
    assert parse_models_payload(payload) == ["a", "b"]


def test_resolve_local_model_rejects_stale_qwen_alias() -> None:
    config = {
        "runtime": {
            "local_base_url": "http://127.0.0.1:1234/v1",
            "preferred_local_model": "qwen2.5-coder-1.5b-instruct",
        },
        "models": {
            "local_worker": {
                "provider": "lmstudio",
                "model": "auto",
                "base_url": "http://127.0.0.1:1234/v1",
            }
        },
    }

    def fetcher(base_url: str, timeout: float) -> list[str]:
        assert base_url == "http://127.0.0.1:1234/v1"
        return ["google/gemma-4-e4b", "qwen2.5-coder-1.5b-instruct"]

    result = resolve_local_model(
        config,
        config["models"]["local_worker"],
        env={"QWEN_MODEL": "qwen-local"},
        fetcher=fetcher,
    )
    assert result.model == "qwen2.5-coder-1.5b-instruct"
    assert result.reason == "selected preferred_local_model"
    assert "QWEN_MODEL=qwen-local" in result.rejected_candidates


def test_resolve_local_model_uses_valid_hermes2_env_override() -> None:
    config = {
        "runtime": {"preferred_local_model": "qwen2.5-coder-1.5b-instruct"},
        "models": {"local_worker": {"provider": "lmstudio", "model": "auto", "base_url": "http://local/v1"}},
    }

    result = resolve_local_model(
        config,
        config["models"]["local_worker"],
        env={"HERMES2_LOCAL_MODEL": "custom-local"},
        fetcher=lambda _base, _timeout: ["custom-local", "qwen2.5-coder-1.5b-instruct"],
    )
    assert result.model == "custom-local"
    assert result.reason == "selected from HERMES2_LOCAL_MODEL"


def test_profile_model_aliases_dedupes_primary_alias() -> None:
    config = {
        "models": {
            "local_worker": {"provider": "lmstudio", "model": "auto"},
            "openai_fallback": {"provider": "openai", "model": "gpt"},
        },
        "model_chains": {"local_first": ["local_worker", "openai_fallback"]},
        "profiles": {"default": {"model_chain": "local_first"}},
    }

    assert profile_model_aliases(config, "default", primary_alias="local_worker") == [
        "local_worker",
        "openai_fallback",
    ]


def test_effective_model_chain_skips_missing_cloud_key() -> None:
    config = {
        "runtime": {"preferred_local_model": "qwen2.5-coder-1.5b-instruct"},
        "models": {
            "local_worker": {"provider": "lmstudio", "model": "auto", "base_url": "http://local/v1"},
            "openai_fallback": {"provider": "openai", "model": "gpt", "api_key_env": "OPENAI_API_KEY"},
        },
        "model_chains": {"local_first": ["local_worker", "openai_fallback"]},
        "profiles": {"default": {"model_chain": "local_first"}},
    }

    candidates, skipped = effective_model_chain(
        primary_alias="local_worker",
        config=config,
        env={},
        fetcher=lambda _base, _timeout: ["qwen2.5-coder-1.5b-instruct"],
    )

    assert [candidate.alias for candidate in candidates] == ["local_worker"]
    assert skipped == ["openai_fallback: OPENAI_API_KEY is not set"]


def test_effective_model_chain_skips_missing_gemini_key() -> None:
    config = {
        "runtime": {"preferred_local_model": "qwen2.5-coder-1.5b-instruct"},
        "models": {
            "local_worker": {"provider": "lmstudio", "model": "auto", "base_url": "http://local/v1"},
            "google_omni": {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "api_key_env": "GEMINI_API_KEY",
            },
        },
        "model_chains": {"local_first": ["local_worker", "google_omni"]},
        "profiles": {"default": {"model_chain": "local_first"}},
    }

    candidates, skipped = effective_model_chain(
        primary_alias="local_worker",
        config=config,
        env={},
        fetcher=lambda _base, _timeout: ["qwen2.5-coder-1.5b-instruct"],
    )

    assert [candidate.alias for candidate in candidates] == ["local_worker"]
    assert skipped == ["google_omni: GEMINI_API_KEY is not set"]


def test_effective_model_chain_includes_google_when_gemini_key_is_set() -> None:
    config = {
        "runtime": {"preferred_local_model": "qwen2.5-coder-1.5b-instruct"},
        "models": {
            "local_worker": {"provider": "lmstudio", "model": "auto", "base_url": "http://local/v1"},
            "google_omni": {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "api_key_env": "GEMINI_API_KEY",
            },
        },
        "model_chains": {"local_first": ["local_worker", "google_omni"]},
        "profiles": {"default": {"model_chain": "local_first"}},
    }

    candidates, skipped = effective_model_chain(
        primary_alias="local_worker",
        config=config,
        env={"GEMINI_API_KEY": "test-key"},
        fetcher=lambda _base, _timeout: ["qwen2.5-coder-1.5b-instruct"],
    )

    assert [candidate.alias for candidate in candidates] == ["local_worker", "google_omni"]
    assert skipped == []


def test_validate_profile_workflow_enforces_profile_allow_list() -> None:
    config = {
        "profiles": {
            "code": {
                "model_chain": ["local_worker"],
                "workflows": ["code_build"],
            }
        }
    }

    validate_profile_workflow(config, "code", "code_build")
    with pytest.raises(ModelResolutionError, match="does not allow workflow"):
        validate_profile_workflow(config, "code", "default_task")
