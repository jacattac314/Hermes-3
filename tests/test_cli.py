from __future__ import annotations

from hermes2.cli import build_parser
from hermes2.mobile import ensure_safe_bind_host, host_requires_mobile_token


def test_chat_parser_accepts_message() -> None:
    parser = build_parser()
    args = parser.parse_args(["chat", "--message", "hello"])
    assert args.command == "chat"
    assert args.message == "hello"
    assert args.model_alias == "local_worker"


def test_profiles_parser_accepts_profile_marker() -> None:
    parser = build_parser()
    args = parser.parse_args(["profiles", "--profile", "code"])
    assert args.command == "profiles"
    assert args.profile == "code"


def test_serve_parser_accepts_local_server_options() -> None:
    parser = build_parser()
    args = parser.parse_args(["serve", "--host", "127.0.0.1", "--port", "9999", "--profile", "research"])
    assert args.command == "serve"
    assert args.host == "127.0.0.1"
    assert args.port == 9999
    assert args.profile == "research"


def test_non_loopback_bind_requires_mobile_token(monkeypatch) -> None:
    monkeypatch.delenv("HERMES2_MOBILE_TOKEN", raising=False)

    assert host_requires_mobile_token("127.0.0.1") is False
    assert host_requires_mobile_token("localhost") is False
    assert host_requires_mobile_token("::1") is False
    assert host_requires_mobile_token("0.0.0.0") is True
    assert host_requires_mobile_token("100.64.0.10") is True

    try:
        ensure_safe_bind_host({"mobile": {"token_env": "HERMES2_MOBILE_TOKEN"}}, "0.0.0.0")
    except Exception as exc:
        assert "HERMES2_MOBILE_TOKEN" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("non-loopback bind was allowed without mobile token")

    monkeypatch.setenv("HERMES2_MOBILE_TOKEN", "test-token")
    ensure_safe_bind_host({"mobile": {"token_env": "HERMES2_MOBILE_TOKEN"}}, "0.0.0.0")
