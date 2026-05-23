from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import subprocess
from typing import Any
from urllib.parse import parse_qs, urlparse

from hermes2.config import ConfigBundle, ConfigError, log_dir, report_dir
from hermes2.env import key_status, secret_values
from hermes2.llm import LLMError, invoke_chat
from hermes2.mobile import ensure_safe_bind_host, mobile_payload, notify_mobile, verify_mobile_request
from hermes2.models import (
    ModelFetcher,
    ModelResolutionError,
    effective_model_chain,
    fetch_openai_models,
    profile_config,
)
from hermes2.runlog import RunLogger
from hermes2.teams import (
    extract_outgoing_message,
    teams_bridge_payload,
    teams_message_response,
    verify_outgoing_hmac,
)
from hermes2.tools import execute_tool, tools_payload
from hermes2.workflow import run_workflow


def _server_system_prompt() -> str:
    return (
        "You are Hermes 2.0 running in local server mode. "
        "Answer as a concise local-first agent operator. "
        "Do not claim to run commands unless a workflow run returns command results."
    )


def _teams_system_prompt() -> str:
    return (
        "You are Hermes 2.0 replying inside Microsoft Teams. "
        "Be helpful, brief, and plain-spoken. "
        "Do not claim to run commands or alter files from Teams chat. "
        "If the user asks for local execution, tell them to run the workflow in the Hermes2 desktop app."
    )


def _open_url(url: str) -> None:
    subprocess.run(["/usr/bin/open", url], check=True, timeout=5)


def _local_chat_action(message: str, opener=_open_url) -> dict[str, Any] | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", message.lower()).strip()
    if "gmail" not in normalized:
        return None

    wants_open = any(word in normalized.split() for word in {"open", "launch", "show", "check", "search", "find"})
    if not wants_open:
        return None

    days_match = re.search(r"(?:last|past)\s+(\d{1,3})\s+days?", normalized) or re.search(
        r"(\d{1,3})\s+days?", normalized
    )
    if days_match:
        days = max(1, min(int(days_match.group(1)), 365))
        url = f"https://mail.google.com/mail/u/0/#search/newer_than:{days}d"
        opener(url)
        return {
            "action": {"type": "open_url", "url": url},
            "response": (
                f"Opened Gmail search for messages from the last {days} days. "
                "If Chrome asks, sign in to the Google account you want Hermes2 to inspect."
            ),
        }

    url = "https://mail.google.com/mail/u/0/"
    opener(url)
    return {
        "action": {"type": "open_url", "url": url},
        "response": "Opened Gmail in your browser.",
    }


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                loaded = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(loaded, dict):
                events.append(loaded)
    except OSError:
        return []
    return events


def _event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    for event in events:
        if event.get("type") == event_type:
            return event
    return {}


def _last_event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("type") == event_type:
            return event
    return {}


def _report_for_log(config: dict[str, Any], log_file: Path) -> Path:
    suffix = log_file.name.removesuffix(".jsonl")
    return report_dir(config) / f"{suffix}.md"


def run_summary(config: dict[str, Any], log_file: Path) -> dict[str, Any]:
    events = _read_jsonl(log_file)
    start = _event(events, "run_start")
    end = _last_event(events, "run_end")
    model_candidates = [event for event in events if event.get("type") == "model_candidates"]
    commands = [event for event in events if event.get("type") == "command"]
    report_file = _report_for_log(config, log_file)
    selected_models = []
    for event in events:
        if event.get("type") == "agent_step":
            selected_models.append(
                {
                    "agent": event.get("agent"),
                    "alias": event.get("alias"),
                    "provider": event.get("provider"),
                    "model": event.get("model"),
                }
            )
    return {
        "id": log_file.name.removesuffix(".jsonl"),
        "created_at": log_file.name[:15],
        "workflow": start.get("workflow") or end.get("workflow") or "",
        "profile": start.get("profile") or "",
        "status": end.get("status") or ("running" if start else "unknown"),
        "exit_code": end.get("exit_code"),
        "workspace": start.get("workspace") or "",
        "commands": start.get("commands") or [],
        "command_results": commands,
        "selected_models": selected_models,
        "skipped": model_candidates[0].get("skipped", []) if model_candidates else [],
        "jsonl_log": str(log_file),
        "markdown_report": str(report_file) if report_file.exists() else "",
        "report_name": report_file.name if report_file.exists() else "",
    }


def recent_runs_payload(config: dict[str, Any], *, limit: int = 20) -> dict[str, Any]:
    logs = log_dir(config)
    files = sorted(logs.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
    return {"runs": [run_summary(config, item) for item in files[:limit]]}


def report_payload(config: dict[str, Any], report_name: str) -> dict[str, Any]:
    if not report_name or "/" in report_name or "\\" in report_name:
        raise ConfigError("report name must be a basename")
    reports = report_dir(config).resolve()
    target = (reports / report_name).resolve()
    if reports != target.parent:
        raise ConfigError("report path is outside report directory")
    if not target.exists():
        raise ConfigError(f"report not found: {report_name}")
    return {
        "name": target.name,
        "path": str(target),
        "markdown": target.read_text(encoding="utf-8")[:80000],
    }


def model_chain_payload(
    *,
    config: dict[str, Any],
    profile_name: str,
    model_alias: str = "local_worker",
    fetcher: ModelFetcher = fetch_openai_models,
) -> dict[str, Any]:
    candidates, skipped = effective_model_chain(
        primary_alias=model_alias,
        config=config,
        profile_name=profile_name,
        fetcher=fetcher,
    )
    return {
        "profile": profile_name,
        "model_alias": model_alias,
        "candidates": [
            {
                "alias": candidate.alias,
                "provider": candidate.config.get("provider"),
                "model": candidate.config.get("model"),
                "base_url": candidate.config.get("base_url"),
            }
            for candidate in candidates
        ],
        "skipped": skipped,
    }


def _invoke_chat_with_fallback(
    *,
    candidates: list,
    message: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
    system_prompt: str | None = None,
) -> tuple[str, dict[str, Any]]:
    errors: list[str] = []
    for candidate in candidates:
        try:
            response = invoke_chat(
                model_cfg=candidate.config,
                messages=[
                    {"role": "system", "content": system_prompt or _server_system_prompt()},
                    {"role": "user", "content": message},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            return response, {
                "alias": candidate.alias,
                "provider": candidate.config.get("provider"),
                "model": candidate.config.get("model"),
                "base_url": candidate.config.get("base_url"),
            }
        except LLMError as exc:
            errors.append(f"{candidate.alias}: {exc}")
    raise LLMError("all server chat model candidates failed: " + "; ".join(errors))


def make_handler(bundle: ConfigBundle, default_profile: str):
    class Hermes2Handler(BaseHTTPRequestHandler):
        server_version = "Hermes2HTTP/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Hermes2-Mobile-Token")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                return

        def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                return

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Hermes2-Mobile-Token")
            self.end_headers()

        def _require_mobile_auth(self) -> bool:
            try:
                verify_mobile_request(bundle.config, self.headers)
            except ConfigError as exc:
                self._send(401, {"error": str(exc), "mobile": mobile_payload(bundle.config)})
                return False
            return True

        def _static_target(self, path: str) -> Path | None:
            dist = bundle.repo_root / "frontend" / "dist"
            if path in {"/", "/mobile", "/mobile/"}:
                return dist / "index.html"
            if not (path.startswith("/assets/") or path in {"/manifest.webmanifest", "/sw.js", "/icon.svg"}):
                return None
            target = (dist / path.lstrip("/")).resolve()
            dist_resolved = dist.resolve()
            if target == dist_resolved or dist_resolved not in target.parents:
                return None
            return target

        def _send_static(self, path: str) -> bool:
            target = self._static_target(path)
            if target is None:
                return False
            if not target.exists() or not target.is_file():
                self._send(
                    404,
                    {
                        "error": "frontend build artifact not found",
                        "hint": "run: cd frontend && npm run build",
                    },
                )
                return True
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.name == "sw.js":
                content_type = "text/javascript; charset=utf-8"
            if target.suffix == ".html":
                content_type = "text/html; charset=utf-8"
            self._send_bytes(200, target.read_bytes(), content_type)
            return True

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            return self._parse_json(raw)

        def _parse_json(self, raw: bytes) -> dict[str, Any]:
            try:
                loaded = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ConfigError(f"invalid JSON request body: {exc}") from exc
            if not isinstance(loaded, dict):
                raise ConfigError("JSON request body must be an object")
            return loaded

        def _read_json_with_raw(self) -> tuple[dict[str, Any], bytes]:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            return self._parse_json(raw), raw

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            profile_name = query.get("profile", [default_profile])[0]
            model_alias = query.get("model_alias", ["local_worker"])[0]
            try:
                if parsed.path == "/mobile.json":
                    self._send(200, mobile_payload(bundle.config))
                    return
                if parsed.path == "/teams":
                    self._send(200, teams_bridge_payload(bundle.config))
                    return
                if parsed.path in {"/health", "/models", "/runs", "/report", "/tools"} and not self._require_mobile_auth():
                    return
                if parsed.path == "/health":
                    profile_config(bundle.config, profile_name)
                    self._send(
                        200,
                        {
                            "status": "ok",
                            "profile": profile_name,
                            "profiles": sorted((bundle.config.get("profiles") or {}).keys()),
                            "repo": str(bundle.repo_root),
                            "keys": key_status(),
                            "tools": tools_payload(bundle.config)["tools"],
                            "teams": teams_bridge_payload(bundle.config),
                            "models": model_chain_payload(
                                config=bundle.config,
                                profile_name=profile_name,
                                model_alias=model_alias,
                            ),
                        },
                    )
                    return
                if parsed.path == "/models":
                    self._send(
                        200,
                        model_chain_payload(
                            config=bundle.config,
                            profile_name=profile_name,
                            model_alias=model_alias,
                        ),
                    )
                    return
                if parsed.path == "/runs":
                    limit = int(query.get("limit", ["20"])[0])
                    self._send(200, recent_runs_payload(bundle.config, limit=max(1, min(limit, 100))))
                    return
                if parsed.path == "/report":
                    report_name = query.get("name", [""])[0]
                    self._send(200, report_payload(bundle.config, report_name))
                    return
                if parsed.path == "/tools":
                    self._send(200, tools_payload(bundle.config))
                    return
                if self._send_static(parsed.path):
                    return
                self._send(404, {"error": "not found"})
            except (ConfigError, ModelResolutionError) as exc:
                self._send(400, {"error": str(exc)})

        def do_POST(self) -> None:
            try:
                if self.path == "/teams/outgoing":
                    body, raw_body = self._read_json_with_raw()
                    teams_cfg = teams_bridge_payload(bundle.config)
                    if not teams_cfg["enabled"]:
                        raise ConfigError("Teams bridge is disabled")
                    verify_outgoing_hmac(
                        config=bundle.config,
                        authorization=self.headers.get("Authorization", ""),
                        raw_body=raw_body,
                    )
                    teams_profile = str(body.get("profile") or teams_cfg["profile"] or default_profile)
                    profile_config(bundle.config, teams_profile)
                    message = extract_outgoing_message(body)
                    candidates, _skipped = effective_model_chain(
                        primary_alias=str(body.get("model_alias") or "local_worker"),
                        config=bundle.config,
                        profile_name=teams_profile,
                    )
                    response, _selected_model = _invoke_chat_with_fallback(
                        candidates=candidates,
                        message=f"Teams user message: {message}",
                        temperature=float(body.get("temperature", 0.2)),
                        max_tokens=int(body.get("max_tokens") or (bundle.config.get("teams") or {}).get("max_tokens", 320)),
                        timeout=float(teams_cfg["response_timeout_seconds"]),
                        system_prompt=_teams_system_prompt(),
                    )
                    self._send(200, teams_message_response(response))
                    return

                if self.path in {"/chat", "/run", "/tools/execute"} and not self._require_mobile_auth():
                    return

                body = self._read_json()
                profile_name = str(body.get("profile") or default_profile)
                model_alias = str(body.get("model_alias") or "local_worker")
                profile_config(bundle.config, profile_name)

                if self.path == "/chat":
                    message = str(body.get("message") or "").strip()
                    if not message:
                        raise ConfigError("message is required")
                    local_action = _local_chat_action(message)
                    if local_action:
                        self._send(
                            200,
                            {
                                "status": "completed",
                                "profile": profile_name,
                                "selected_model": {
                                    "alias": "local_action",
                                    "provider": "macos",
                                    "model": "open-url",
                                    "base_url": "",
                                },
                                "skipped": [],
                                "response": local_action["response"],
                                "action": local_action["action"],
                            },
                        )
                        return
                    candidates, skipped = effective_model_chain(
                        primary_alias=model_alias,
                        config=bundle.config,
                        profile_name=profile_name,
                    )
                    timeout = float((bundle.config.get("runtime") or {}).get("request_timeout_seconds", 300))
                    response, selected_model = _invoke_chat_with_fallback(
                        candidates=candidates,
                        message=message,
                        temperature=float(body.get("temperature", 0.2)),
                        max_tokens=int(body.get("max_tokens", 512)),
                        timeout=timeout,
                    )
                    self._send(
                        200,
                        {
                            "status": "completed",
                            "profile": profile_name,
                            "selected_model": selected_model,
                            "skipped": skipped,
                            "response": response,
                        },
                    )
                    return

                if self.path == "/run":
                    workflow = str(body.get("workflow") or "").strip()
                    user_input = str(body.get("input") or "").strip()
                    if not workflow:
                        raise ConfigError("workflow is required")
                    if not user_input:
                        raise ConfigError("input is required")
                    commands = body.get("commands") or []
                    if not isinstance(commands, list) or not all(isinstance(item, str) for item in commands):
                        raise ConfigError("commands must be a list of strings")
                    workspace_value = body.get("workspace")
                    logger = RunLogger(bundle.config, workflow, secret_values())
                    result = run_workflow(
                        bundle=bundle,
                        workflow_name=workflow,
                        user_input=user_input,
                        workspace=Path(str(workspace_value)).expanduser() if workspace_value else None,
                        commands=commands,
                        bypass_approvals=bool(body.get("bypass_approvals", False)),
                        logger=logger,
                        profile_name=profile_name,
                    )
                    notification = notify_mobile(
                        bundle.config,
                        title="Hermes2 workflow completed" if result.exit_code == 0 else "Hermes2 workflow needs attention",
                        message=f"{workflow}: {result.status} (exit {result.exit_code})",
                        tags=["white_check_mark"] if result.exit_code == 0 else ["warning"],
                    )
                    self._send(
                        200 if result.exit_code == 0 else 409,
                        {
                            "status": result.status,
                            "exit_code": result.exit_code,
                            "profile": profile_name,
                            "workflow": workflow,
                            "jsonl_log": str(result.log_file),
                            "markdown_report": str(result.report_file),
                            "command_results": result.command_results,
                            "mobile_notification": notification,
                        },
                    )
                    return

                if self.path == "/tools/execute":
                    tool_name = str(body.get("tool") or "").strip()
                    action = str(body.get("action") or "").strip()
                    if not tool_name:
                        raise ConfigError("tool is required")
                    if not action:
                        raise ConfigError("action is required")
                    payload = body.get("payload") or {}
                    if not isinstance(payload, dict):
                        raise ConfigError("payload must be an object")
                    self._send(
                        200,
                        execute_tool(
                            config=bundle.config,
                            tool_name=tool_name,
                            action=action,
                            payload=payload,
                        ),
                    )
                    return

                self._send(404, {"error": "not found"})
            except (ConfigError, ModelResolutionError) as exc:
                self._send(400, {"error": str(exc)})
            except LLMError as exc:
                if self.path == "/teams/outgoing":
                    self._send(
                        200,
                        teams_message_response(
                            "Hermes2 received the Teams message, but the local model did not answer fast enough for Teams. Try again with a shorter request or use the desktop app for longer work."
                        ),
                    )
                else:
                    self._send(502, {"error": str(exc)})

    return Hermes2Handler


def serve_http(bundle: ConfigBundle, *, host: str, port: int, profile_name: str) -> None:
    profile_config(bundle.config, profile_name)
    ensure_safe_bind_host(bundle.config, host)
    server = ThreadingHTTPServer((host, port), make_handler(bundle, profile_name))
    print(f"Hermes 2.0 server listening on http://{host}:{port}")
    print("Endpoints: GET /mobile, GET /mobile.json, GET /health, GET /models, GET /runs, GET /report, GET /tools, GET /teams, POST /chat, POST /run, POST /tools/execute, POST /teams/outgoing")
    print("Press Ctrl-C to stop.")
    server.serve_forever()
