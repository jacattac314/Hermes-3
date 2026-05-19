from __future__ import annotations

from hermes2.models import parse_models_payload, resolve_local_model


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
