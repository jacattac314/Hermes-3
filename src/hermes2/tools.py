from __future__ import annotations

import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import time
from typing import Any

from hermes2.config import ConfigError, expand_path, validate_workspace, workspace_allowed_paths


MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_TIMEOUT_SECONDS = 20.0
MAX_RESULT_STRING = 12000
MAX_RESULT_LINES = 80
COMPUTER_USE_MUTATING_ACTIONS = {
    "click",
    "drag",
    "perform_secondary_action",
    "press_key",
    "scroll",
    "select_text",
    "set_value",
    "type_text",
}


def tool_definitions(config: dict[str, Any]) -> dict[str, Any]:
    configured = config.get("tools") or {}
    if configured:
        return configured
    return {
        "shell": {
            "enabled": True,
            "adapter": "workflow_executor",
            "description": "Runs explicit validation commands through workflows.",
            "risk_level": "high",
            "actions": ["run_command"],
        }
    }


def tools_payload(config: dict[str, Any]) -> dict[str, Any]:
    tools = []
    for name, tool in tool_definitions(config).items():
        status = _tool_status(tool)
        enabled = bool(tool.get("enabled", True))
        tools.append(
            {
                "name": name,
                "enabled": enabled,
                "ready": enabled and status == "ready",
                "adapter": tool.get("adapter", ""),
                "description": tool.get("description", ""),
                "risk_level": tool.get("risk_level", "medium"),
                "requires_adapter": status == "adapter_required",
                "confirmation_policy": tool.get("confirmation_policy", ""),
                "actions": tool.get("actions") or [],
                "status": status,
            }
        )
    return {"tools": tools}


def _tool_status(tool: dict[str, Any]) -> str:
    if not bool(tool.get("enabled", True)):
        return "disabled"
    if bool(tool.get("requires_adapter", False)):
        return "adapter_required"
    if tool.get("adapter") == "mcp_stdio" and not _mcp_command_available(tool):
        return "adapter_missing"
    return "ready"


def _mcp_command_available(tool: dict[str, Any]) -> bool:
    command = str(tool.get("command") or "").strip()
    if not command:
        return False
    if "/" not in command:
        return shutil.which(command) is not None
    return expand_path(command).exists()


def execute_tool(
    *,
    config: dict[str, Any],
    tool_name: str,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    tools = tool_definitions(config)
    if tool_name not in tools:
        raise ConfigError(f"unknown tool: {tool_name}")
    tool = tools[tool_name]
    if not bool(tool.get("enabled", True)):
        raise ConfigError(f"tool {tool_name} is disabled")
    actions = tool.get("actions") or []
    if action not in actions:
        raise ConfigError(f"tool {tool_name} does not support action {action}")
    if bool(tool.get("requires_adapter", False)):
        return {
            "status": "blocked",
            "tool": tool_name,
            "action": action,
            "reason": f"{tool_name} requires an external adapter before execution",
            "confirmation_policy": tool.get("confirmation_policy", ""),
        }
    if tool_name == "filesystem" and action == "workspace_status":
        return workspace_status(config=config, payload=payload)
    if tool_name == "shell":
        return {
            "status": "blocked",
            "tool": tool_name,
            "action": action,
            "reason": "shell commands must run through hermes2 workflow execution with explicit --command values",
        }
    if tool.get("adapter") == "mcp_stdio":
        return execute_mcp_stdio_tool(tool=tool, tool_name=tool_name, action=action, payload=payload)
    return {
        "status": "blocked",
        "tool": tool_name,
        "action": action,
        "reason": "no built-in executor is configured for this tool",
    }


def workspace_status(*, config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    workspace_value = str(payload.get("workspace") or Path.cwd())
    workspace = validate_workspace(config, Path(workspace_value).expanduser())
    exists = workspace.exists()
    is_dir = workspace.is_dir()
    entries: list[str] = []
    if exists and is_dir:
        entries = sorted(item.name for item in workspace.iterdir())[:40]
    return {
        "status": "completed",
        "tool": "filesystem",
        "action": "workspace_status",
        "workspace": str(workspace),
        "exists": exists,
        "is_dir": is_dir,
        "allowed_paths": [str(path) for path in workspace_allowed_paths(config)],
        "entries": entries,
    }


def execute_mcp_stdio_tool(
    *,
    tool: dict[str, Any],
    tool_name: str,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not _mcp_command_available(tool):
        return {
            "status": "blocked",
            "tool": tool_name,
            "action": action,
            "reason": "configured MCP stdio adapter command is missing",
        }
    if action == "adapter_tools":
        response = _mcp_stdio_list_tools(tool)
        result = _sanitize_mcp_value(response.get("result") or {})
        tool_names = _mcp_tool_names(result)
        return {
            "status": "completed",
            "tool": tool_name,
            "action": action,
            "entries": tool_names,
            "result": result,
        }

    if tool_name == "computer_use" and payload.get("approved") is not True:
        return {
            "status": "blocked",
            "tool": tool_name,
            "action": action,
            "reason": (
                "direct computer_use actions require an explicit approved=true payload after "
                "action-time confirmation"
            ),
            "confirmation_policy": tool.get("confirmation_policy", ""),
        }

    if tool_name == "computer_use" and action in COMPUTER_USE_MUTATING_ACTIONS:
        if payload.get("approved") is not True:
            return {
                "status": "blocked",
                "tool": tool_name,
                "action": action,
                "reason": (
                    "computer_use UI actions require an explicit approved=true payload after "
                    "action-time confirmation"
                ),
                "confirmation_policy": tool.get("confirmation_policy", ""),
            }

    arguments = _mcp_arguments(payload)
    response = _mcp_stdio_call(tool, action, arguments)
    if "error" in response:
        return {
            "status": "failed",
            "tool": tool_name,
            "action": action,
            "reason": _mcp_error_text(response["error"]),
            "result": _sanitize_mcp_value(response["error"]),
        }

    result = _sanitize_mcp_value(response.get("result") or {})
    entries = _text_entries(result)
    return {
        "status": "completed",
        "tool": tool_name,
        "action": action,
        "entries": entries,
        "result": result,
    }


def _mcp_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"approved", "confirmation", "confirmation_policy"}
    }


def _mcp_stdio_command(tool: dict[str, Any]) -> list[str]:
    command = str(tool.get("command") or "").strip()
    if not command:
        raise ConfigError("mcp_stdio adapter command is required")
    resolved = str(expand_path(command)) if "/" in command else command
    args = [str(item) for item in (tool.get("args") or [])]
    return [resolved, *args]


def _mcp_stdio_call(tool: dict[str, Any], action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if str(tool.get("transport") or "").lower() == "pty":
        return _mcp_pty_call(tool, action, arguments)
    return _mcp_pipe_call(tool, action, arguments)


def _mcp_stdio_list_tools(tool: dict[str, Any]) -> dict[str, Any]:
    return _mcp_pipe_request(tool, method="tools/list", params={})


def _mcp_pipe_request(tool: dict[str, Any], *, method: str, params: dict[str, Any]) -> dict[str, Any]:
    command = _mcp_stdio_command(tool)
    cwd_value = tool.get("cwd")
    cwd = str(expand_path(cwd_value)) if cwd_value else None
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=os.environ.copy(),
    )
    try:
        _mcp_write(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "hermes2", "version": "0.1.0"},
                },
            },
        )
        _read_mcp_response(proc, response_id=1, timeout=8.0)
        _mcp_write(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        _mcp_write(proc, {"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
        response, stderr = _read_mcp_response(proc, response_id=2, timeout=8.0)
        if stderr and "error" not in response:
            response["_stderr"] = stderr
        return response
    finally:
        _stop_process(proc)


def _mcp_pipe_call(tool: dict[str, Any], action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _mcp_pipe_request(
        tool,
        method="tools/call",
        params={"name": action, "arguments": arguments},
    )


def _mcp_tool_names(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    tools = result.get("tools")
    if not isinstance(tools, list):
        return []
    names = []
    for item in tools:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def _mcp_write(proc: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise ConfigError("MCP adapter stdin is unavailable")
    proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def _read_mcp_response(
    proc: subprocess.Popen[str],
    *,
    response_id: int,
    timeout: float,
) -> tuple[dict[str, Any], str]:
    if proc.stdout is None or proc.stderr is None:
        raise ConfigError("MCP adapter pipes are unavailable")
    deadline = time.monotonic() + timeout
    stderr_chunks: list[str] = []
    stdout_lines: list[str] = []

    while time.monotonic() < deadline:
        remaining = max(0.05, min(0.25, deadline - time.monotonic()))
        readable, _, _ = select.select([proc.stdout, proc.stderr], [], [], remaining)
        for stream in readable:
            line = stream.readline()
            if not line:
                continue
            if stream is proc.stderr:
                stderr_chunks.append(line)
                continue
            stdout_lines.append(line)
            loaded = _parse_mcp_line(line)
            if _is_mcp_response(loaded, response_id):
                return loaded, "".join(stderr_chunks)[-4000:]
        if proc.poll() is not None:
            break

    flushed = _interrupt_and_collect(proc)
    stdout_lines.extend(flushed[0].splitlines(True))
    stderr_chunks.extend(flushed[1].splitlines(True))
    for line in stdout_lines:
        loaded = _parse_mcp_line(line)
        if _is_mcp_response(loaded, response_id):
            return loaded, "".join(stderr_chunks)[-4000:]
    raise ConfigError(
        "MCP adapter timed out waiting for response"
        + (f": {''.join(stderr_chunks)[-1000:].strip()}" if stderr_chunks else "")
    )


def _mcp_pty_call(tool: dict[str, Any], action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    command = _mcp_stdio_command(tool)
    cwd_value = tool.get("cwd")
    cwd = str(expand_path(cwd_value)) if cwd_value else None
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        env=os.environ.copy(),
    )
    os.close(slave_fd)
    try:
        _mcp_pty_write(
            master_fd,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "hermes2", "version": "0.1.0"},
                },
            },
        )
        _read_mcp_pty_response(master_fd, proc, response_id=1, timeout=8.0)
        _mcp_pty_write(master_fd, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        _mcp_pty_write(
            master_fd,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": action, "arguments": arguments},
            },
        )
        response = _read_mcp_pty_response(
            master_fd,
            proc,
            response_id=2,
            timeout=MCP_TIMEOUT_SECONDS,
            interrupt_on_timeout=True,
        )
        return response
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        _stop_process(proc)


def _mcp_pty_write(fd: int, payload: dict[str, Any]) -> None:
    os.write(fd, (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))


def _read_mcp_pty_response(
    fd: int,
    proc: subprocess.Popen[bytes],
    *,
    response_id: int,
    timeout: float,
    interrupt_on_timeout: bool = False,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    buffer = ""
    lines: list[str] = []

    def consume(chunk: str) -> dict[str, Any] | None:
        nonlocal buffer
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.rstrip("\r")
            lines.append(line)
            loaded = _parse_mcp_line(line)
            if _is_mcp_response(loaded, response_id):
                return loaded
        return None

    while time.monotonic() < deadline:
        remaining = max(0.05, min(0.25, deadline - time.monotonic()))
        readable, _, _ = select.select([fd], [], [], remaining)
        if fd not in readable:
            if proc.poll() is not None:
                break
            continue
        try:
            raw = os.read(fd, 65536)
        except OSError:
            break
        if not raw:
            continue
        loaded = consume(raw.decode("utf-8", errors="replace"))
        if loaded is not None:
            return loaded

    if interrupt_on_timeout and proc.poll() is None:
        try:
            os.write(fd, b"\x03")
        except OSError:
            pass
        interrupt_deadline = time.monotonic() + 4.0
        while time.monotonic() < interrupt_deadline:
            readable, _, _ = select.select([fd], [], [], 0.2)
            if fd not in readable:
                if proc.poll() is not None:
                    break
                continue
            try:
                raw = os.read(fd, 65536)
            except OSError:
                break
            if not raw:
                continue
            loaded = consume(raw.decode("utf-8", errors="replace"))
            if loaded is not None:
                return loaded

    for line in lines:
        loaded = _parse_mcp_line(line)
        if _is_mcp_response(loaded, response_id):
            return loaded
    raise ConfigError("MCP adapter timed out waiting for response")


def _parse_mcp_line(line: str) -> dict[str, Any]:
    try:
        loaded = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _is_mcp_response(loaded: dict[str, Any], response_id: int) -> bool:
    return loaded.get("id") == response_id and ("result" in loaded or "error" in loaded)


def _interrupt_and_collect(proc: subprocess.Popen[str]) -> tuple[str, str]:
    if proc.poll() is None:
        try:
            proc.send_signal(signal.SIGINT)
        except ProcessLookupError:  # pragma: no cover - process already exited
            pass
    try:
        return proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        _stop_process(proc)
        try:
            return proc.communicate(timeout=1)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            return "", ""


def _stop_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=1)
    except Exception:  # pragma: no cover - defensive cleanup
        try:
            proc.kill()
        except Exception:
            return


def _mcp_error_text(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return "MCP adapter returned an error"


def _sanitize_mcp_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > MAX_RESULT_STRING:
            return value[:MAX_RESULT_STRING] + f"\n[truncated {len(value) - MAX_RESULT_STRING} chars]"
        return value
    if isinstance(value, list):
        return [_sanitize_mcp_value(item) for item in value]
    if isinstance(value, dict):
        if value.get("type") == "image" and isinstance(value.get("data"), str):
            clean = dict(value)
            clean["data"] = f"[image data omitted; {len(value['data'])} chars]"
            return clean
        return {key: _sanitize_mcp_value(item) for key, item in value.items()}
    return value


def _text_entries(result: Any) -> list[str]:
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, list):
        return []
    lines: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
            lines.extend(line for line in item["text"].splitlines() if line.strip())
    return lines[:MAX_RESULT_LINES]
