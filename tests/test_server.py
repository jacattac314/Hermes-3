from __future__ import annotations

import json
from pathlib import Path
import sys

from hermes2.server import _local_chat_action, model_chain_payload, recent_runs_payload, report_payload
from hermes2.mobile import (
    mobile_payload,
    mobile_token_required,
    notify_mobile,
    verify_mobile_request,
)
from hermes2.teams import (
    clean_teams_text,
    extract_outgoing_message,
    outgoing_hmac,
    teams_bridge_payload,
    teams_message_response,
    verify_outgoing_hmac,
)
from hermes2.tools import execute_tool, tools_payload


def test_model_chain_payload_reports_local_candidate_and_skipped_cloud() -> None:
    config = {
        "runtime": {"preferred_local_model": "qwen2.5-coder-1.5b-instruct"},
        "models": {
            "local_worker": {"provider": "lmstudio", "model": "auto", "base_url": "http://local/v1"},
            "openai_fallback": {"provider": "openai", "model": "gpt", "api_key_env": "OPENAI_API_KEY"},
        },
        "model_chains": {"local_first": ["local_worker", "openai_fallback"]},
        "profiles": {"default": {"model_chain": "local_first"}},
    }

    payload = model_chain_payload(
        config=config,
        profile_name="default",
        fetcher=lambda _base, _timeout: ["qwen2.5-coder-1.5b-instruct"],
    )

    assert payload["profile"] == "default"
    assert payload["candidates"][0]["provider"] == "lmstudio"
    assert payload["candidates"][0]["model"] == "qwen2.5-coder-1.5b-instruct"
    assert payload["skipped"] == ["openai_fallback: OPENAI_API_KEY is not set"]


def test_recent_runs_payload_parses_jsonl_and_report(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    reports = tmp_path / "reports"
    logs.mkdir()
    reports.mkdir()
    log_file = logs / "20260520-120000-code_build-abc123ef.jsonl"
    report_file = reports / "20260520-120000-code_build-abc123ef.md"
    events = [
        {"type": "run_start", "workflow": "code_build", "profile": "code", "workspace": "/tmp/work", "commands": ["git status --short"]},
        {"type": "model_candidates", "agent": "planner", "skipped": ["google_omni: GEMINI_API_KEY is not set"]},
        {"type": "agent_step", "agent": "planner", "alias": "local_worker", "provider": "lmstudio", "model": "qwen"},
        {"type": "command", "command": "git status --short", "returncode": 0, "approved": True},
        {"type": "run_end", "workflow": "code_build", "status": "completed", "exit_code": 0},
    ]
    log_file.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    report_file.write_text("# Report\n\nDone.\n", encoding="utf-8")

    payload = recent_runs_payload(
        {"paths": {"log_dir": str(logs), "report_dir": str(reports)}},
        limit=5,
    )

    assert payload["runs"][0]["workflow"] == "code_build"
    assert payload["runs"][0]["profile"] == "code"
    assert payload["runs"][0]["status"] == "completed"
    assert payload["runs"][0]["report_name"] == report_file.name
    assert payload["runs"][0]["command_results"][0]["command"] == "git status --short"


def test_report_payload_rejects_path_traversal(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "run.md").write_text("# Run\n", encoding="utf-8")

    payload = report_payload({"paths": {"report_dir": str(reports)}}, "run.md")
    assert payload["markdown"] == "# Run\n"

    try:
        report_payload({"paths": {"report_dir": str(reports)}}, "../secret")
    except Exception as exc:
        assert "basename" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("path traversal was not rejected")


def test_local_chat_action_opens_gmail_search() -> None:
    opened: list[str] = []

    result = _local_chat_action("check gmail last 3 days", opener=opened.append)

    assert result is not None
    assert result["action"]["url"] == "https://mail.google.com/mail/u/0/#search/newer_than:3d"
    assert opened == ["https://mail.google.com/mail/u/0/#search/newer_than:3d"]
    assert "last 3 days" in result["response"]


def test_tools_payload_marks_computer_use_adapter_required() -> None:
    payload = tools_payload(
        {
            "tools": {
                "computer_use": {
                    "enabled": True,
                    "adapter": "external",
                    "requires_adapter": True,
                    "actions": ["inspect_screen"],
                }
            }
        }
    )

    assert payload["tools"][0]["name"] == "computer_use"
    assert payload["tools"][0]["ready"] is False
    assert payload["tools"][0]["status"] == "adapter_required"


def test_execute_filesystem_workspace_status(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    result = execute_tool(
        config={
            "runtime": {"workspace_allowed_paths": [str(tmp_path)]},
            "tools": {
                "filesystem": {
                    "enabled": True,
                    "adapter": "builtin",
                    "actions": ["workspace_status"],
                }
            },
        },
        tool_name="filesystem",
        action="workspace_status",
        payload={"workspace": str(tmp_path)},
    )

    assert result["status"] == "completed"
    assert result["exists"] is True
    assert "README.md" in result["entries"]


def test_execute_computer_use_blocks_without_adapter() -> None:
    result = execute_tool(
        config={
            "tools": {
                "computer_use": {
                    "enabled": True,
                    "adapter": "external",
                    "requires_adapter": True,
                    "confirmation_policy": "computer_use",
                    "actions": ["inspect_screen"],
                }
            }
        },
        tool_name="computer_use",
        action="inspect_screen",
        payload={},
    )

    assert result["status"] == "blocked"
    assert result["confirmation_policy"] == "computer_use"


def test_tools_payload_marks_mcp_stdio_adapter_ready() -> None:
    payload = tools_payload(
        {
            "tools": {
                "computer_use": {
                    "enabled": True,
                    "adapter": "mcp_stdio",
                    "command": sys.executable,
                    "actions": ["adapter_tools"],
                }
            }
        }
    )

    assert payload["tools"][0]["ready"] is True
    assert payload["tools"][0]["status"] == "ready"


def test_tools_payload_marks_missing_mcp_stdio_adapter() -> None:
    payload = tools_payload(
        {
            "tools": {
                "computer_use": {
                    "enabled": True,
                    "adapter": "mcp_stdio",
                    "command": "/tmp/hermes2-missing-mcp-command",
                    "actions": ["adapter_tools"],
                }
            }
        }
    )

    assert payload["tools"][0]["ready"] is False
    assert payload["tools"][0]["status"] == "adapter_missing"


def test_execute_computer_use_mutation_requires_approval() -> None:
    result = execute_tool(
        config={
            "tools": {
                "computer_use": {
                    "enabled": True,
                    "adapter": "mcp_stdio",
                    "command": sys.executable,
                    "confirmation_policy": "computer_use",
                    "actions": ["click"],
                }
            }
        },
        tool_name="computer_use",
        action="click",
        payload={"app": "Finder"},
    )

    assert result["status"] == "blocked"
    assert "approved=true" in result["reason"]


def test_execute_mcp_stdio_readonly_action_with_stub(tmp_path: Path) -> None:
    stub = tmp_path / "mcp_stub.py"
    stub.write_text(
        """
import json
import sys

for line in sys.stdin:
    item = json.loads(line)
    if item.get("id") == 1:
        print(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}}), flush=True)
    if item.get("id") == 2:
        print(json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "list_apps"}, {"name": "click"}]}}), flush=True)
        raise SystemExit(0)
""".lstrip(),
        encoding="utf-8",
    )

    result = execute_tool(
        config={
            "tools": {
                "computer_use": {
                    "enabled": True,
                    "adapter": "mcp_stdio",
                    "command": sys.executable,
                    "args": [str(stub)],
                    "actions": ["adapter_tools"],
                }
            }
        },
        tool_name="computer_use",
        action="adapter_tools",
        payload={},
    )

    assert result["status"] == "completed"
    assert result["entries"] == ["list_apps", "click"]


def test_clean_teams_text_removes_mentions_and_markup() -> None:
    assert clean_teams_text("<at>Hermes2</at>&nbsp; Please <b>help</b>") == "Please help"


def test_teams_hmac_validation(monkeypatch) -> None:
    raw = b'{"text":"hello"}'
    secret = "test-secret"
    monkeypatch.setenv("HERMES2_TEAMS_OUTGOING_SECRET", secret)
    signature = outgoing_hmac(secret, raw)

    verify_outgoing_hmac(
        config={"teams": {"outgoing_secret_env": "HERMES2_TEAMS_OUTGOING_SECRET"}},
        authorization=f"HMAC {signature}",
        raw_body=raw,
    )


def test_extract_outgoing_message_and_response_shape() -> None:
    message = extract_outgoing_message({"text": "<at>Hermes2</at> status"})
    response = teams_message_response("Hermes2 is ready.")

    assert message == "status"
    assert response == {"type": "message", "text": "Hermes2 is ready."}


def test_teams_bridge_payload_defaults() -> None:
    payload = teams_bridge_payload({"teams": {"enabled": True}})

    assert payload["enabled"] is True
    assert payload["endpoint"] == "/teams/outgoing"
    assert payload["requires_hmac"] is True


def test_mobile_payload_does_not_expose_secret_values(monkeypatch) -> None:
    monkeypatch.setenv("HERMES2_MOBILE_TOKEN", "super-secret-token")
    monkeypatch.setenv("HERMES2_NTFY_TOPIC", "private-topic")

    payload = mobile_payload(
        {
            "mobile": {
                "enabled": True,
                "token_env": "HERMES2_MOBILE_TOKEN",
                "ntfy": {"enabled": True, "topic_env": "HERMES2_NTFY_TOPIC"},
            }
        }
    )

    as_json = json.dumps(payload)
    assert payload["token_required"] is True
    assert payload["ntfy"]["enabled"] is True
    assert "super-secret-token" not in as_json
    assert "private-topic" not in as_json


def test_mobile_token_validation_accepts_header_and_bearer(monkeypatch) -> None:
    config = {"mobile": {"token_env": "HERMES2_MOBILE_TOKEN"}}
    monkeypatch.setenv("HERMES2_MOBILE_TOKEN", "mobile-secret")

    assert mobile_token_required(config) is True
    verify_mobile_request(config, {"X-Hermes2-Mobile-Token": "mobile-secret"})
    verify_mobile_request(config, {"Authorization": "Bearer mobile-secret"})


def test_mobile_token_validation_rejects_missing_or_bad_token(monkeypatch) -> None:
    config = {"mobile": {"token_env": "HERMES2_MOBILE_TOKEN"}}
    monkeypatch.setenv("HERMES2_MOBILE_TOKEN", "mobile-secret")

    for headers in ({}, {"Authorization": "Bearer wrong"}):
        try:
            verify_mobile_request(config, headers)
        except Exception as exc:
            assert "mobile token" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("bad mobile token was accepted")


def test_mobile_notify_skips_when_disabled_or_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("HERMES2_NTFY_TOPIC", raising=False)

    disabled = notify_mobile({"mobile": {"ntfy": {"enabled": False}}}, title="Done", message="ok")
    missing_topic = notify_mobile({"mobile": {"ntfy": {"enabled": True}}}, title="Done", message="ok")

    assert disabled["status"] == "skipped"
    assert missing_topic["status"] == "skipped"
