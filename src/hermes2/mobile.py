from __future__ import annotations

import hmac
import ipaddress
import json
import os
from typing import Any, Mapping
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from hermes2.config import ConfigError


MOBILE_TOKEN_HEADER = "X-Hermes2-Mobile-Token"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _mobile_config(config: dict[str, Any]) -> dict[str, Any]:
    mobile = config.get("mobile") or {}
    return mobile if isinstance(mobile, dict) else {}


def mobile_token(config: dict[str, Any]) -> str:
    token_env = str(_mobile_config(config).get("token_env") or "HERMES2_MOBILE_TOKEN")
    return os.environ.get(token_env, "")


def mobile_token_required(config: dict[str, Any]) -> bool:
    mobile = _mobile_config(config)
    return bool(mobile.get("require_token", False) or mobile_token(config))


def host_requires_mobile_token(host: str) -> bool:
    normalized = host.strip().lower().removeprefix("[").removesuffix("]")
    if normalized in LOOPBACK_HOSTS:
        return False
    try:
        return not ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return True


def ensure_safe_bind_host(config: dict[str, Any], host: str) -> None:
    if host_requires_mobile_token(host) and not mobile_token(config):
        raise ConfigError(
            "refusing to bind Hermes2 to a non-loopback host without HERMES2_MOBILE_TOKEN; "
            "set the token in ~/.hermes/.env or bind to 127.0.0.1"
        )


def _bearer_token(value: str) -> str:
    prefix = "bearer "
    stripped = value.strip()
    if stripped.lower().startswith(prefix):
        return stripped[len(prefix) :].strip()
    return ""


def verify_mobile_request(config: dict[str, Any], headers: Mapping[str, str]) -> None:
    if not mobile_token_required(config):
        return
    expected = mobile_token(config)
    if not expected:
        raise ConfigError("mobile token is required but HERMES2_MOBILE_TOKEN is not set")

    header_token = headers.get(MOBILE_TOKEN_HEADER, "")
    auth_token = _bearer_token(headers.get("Authorization", ""))
    supplied = header_token or auth_token
    if supplied and hmac.compare_digest(supplied, expected):
        return
    raise ConfigError("missing or invalid mobile token")


def _ntfy_config(config: dict[str, Any]) -> dict[str, Any]:
    ntfy = _mobile_config(config).get("ntfy") or {}
    return ntfy if isinstance(ntfy, dict) else {}


def mobile_payload(config: dict[str, Any]) -> dict[str, Any]:
    mobile = _mobile_config(config)
    ntfy = _ntfy_config(config)
    topic_env = str(ntfy.get("topic_env") or "HERMES2_NTFY_TOPIC")
    token_env = str(mobile.get("token_env") or "HERMES2_MOBILE_TOKEN")
    return {
        "enabled": bool(mobile.get("enabled", True)),
        "mode": str(mobile.get("mode") or "pwa"),
        "path": str(mobile.get("path") or "/mobile"),
        "token_required": mobile_token_required(config),
        "token_header": MOBILE_TOKEN_HEADER,
        "token_env": token_env,
        "authorization": "Bearer",
        "ntfy": {
            "enabled": bool(ntfy.get("enabled", False) and os.environ.get(topic_env)),
            "configured": bool(os.environ.get(topic_env)),
            "server": str(ntfy.get("server") or "https://ntfy.sh"),
            "topic_env": topic_env,
            "token_env": str(ntfy.get("token_env") or "HERMES2_NTFY_TOKEN"),
        },
        "endpoints": {
            "health": "/health",
            "models": "/models",
            "chat": "/chat",
            "run": "/run",
            "runs": "/runs",
            "tools": "/tools",
        },
    }


def notify_mobile(
    config: dict[str, Any],
    *,
    title: str,
    message: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    ntfy = _ntfy_config(config)
    if not ntfy.get("enabled", False):
        return {"status": "skipped", "reason": "ntfy disabled"}

    topic_env = str(ntfy.get("topic_env") or "HERMES2_NTFY_TOPIC")
    topic = os.environ.get(topic_env, "").strip()
    if not topic:
        return {"status": "skipped", "reason": f"{topic_env} is not set"}

    server = str(ntfy.get("server") or "https://ntfy.sh").rstrip("/")
    token_env = str(ntfy.get("token_env") or "HERMES2_NTFY_TOKEN")
    token = os.environ.get(token_env, "").strip()
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Title": title[:120],
    }
    if tags:
        headers["Tags"] = ",".join(tags)[:120]
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(
        f"{server}/{quote(topic)}",
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=5) as response:
            raw = response.read(4096)
            try:
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                parsed = {}
            return {
                "status": "sent",
                "status_code": response.status,
                "id": parsed.get("id", ""),
            }
    except URLError as exc:
        return {"status": "failed", "reason": str(exc)}
    except OSError as exc:
        return {"status": "failed", "reason": str(exc)}
