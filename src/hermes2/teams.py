from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import re
from typing import Any

from hermes2.config import ConfigError


MENTION_RE = re.compile(r"<at>.*?</at>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
MAX_TEAMS_RESPONSE_CHARS = 3500


def teams_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("teams") or {}


def clean_teams_text(text: str) -> str:
    cleaned = MENTION_RE.sub("", text)
    cleaned = TAG_RE.sub("", cleaned)
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def outgoing_secret(config: dict[str, Any]) -> str:
    env_name = str(teams_config(config).get("outgoing_secret_env") or "HERMES2_TEAMS_OUTGOING_SECRET")
    return os.environ.get(env_name, "")


def _secret_key_bytes(secret: str) -> bytes:
    try:
        return base64.b64decode(secret, validate=True)
    except Exception:
        return secret.encode("utf-8")


def outgoing_hmac(secret: str, raw_body: bytes) -> str:
    return base64.b64encode(
        hmac.new(_secret_key_bytes(secret), raw_body, hashlib.sha256).digest()
    ).decode("ascii")


def verify_outgoing_hmac(
    *,
    config: dict[str, Any],
    authorization: str,
    raw_body: bytes,
) -> None:
    secret = outgoing_secret(config)
    allow_unsigned = bool(teams_config(config).get("allow_unsigned_dev_requests", False))
    if not secret:
        if allow_unsigned:
            return
        raise ConfigError("HERMES2_TEAMS_OUTGOING_SECRET is not set")

    scheme, _, supplied = authorization.partition(" ")
    if scheme.lower() != "hmac" or not supplied:
        raise ConfigError("Teams outgoing webhook requires Authorization: HMAC <signature>")

    expected = outgoing_hmac(secret, raw_body)
    if not hmac.compare_digest(supplied, expected):
        raise ConfigError("Teams outgoing webhook HMAC validation failed")


def extract_outgoing_message(activity: dict[str, Any]) -> str:
    text = str(activity.get("text") or "")
    value = activity.get("value")
    if not text and isinstance(value, dict):
        text = str(value.get("text") or value.get("message") or "")
    message = clean_teams_text(text)
    if not message:
        raise ConfigError("Teams message text is required")
    return message


def teams_message_response(text: str) -> dict[str, Any]:
    clean = text.strip() or "Hermes2 did not return a response."
    if len(clean) > MAX_TEAMS_RESPONSE_CHARS:
        clean = clean[:MAX_TEAMS_RESPONSE_CHARS].rstrip() + "\n\n[truncated]"
    return {"type": "message", "text": clean}


def teams_bridge_payload(config: dict[str, Any]) -> dict[str, Any]:
    cfg = teams_config(config)
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "mode": cfg.get("mode", "outgoing_webhook"),
        "endpoint": cfg.get("endpoint", "/teams/outgoing"),
        "profile": cfg.get("profile", "default"),
        "requires_hmac": not bool(cfg.get("allow_unsigned_dev_requests", False)),
        "secret_env": cfg.get("outgoing_secret_env", "HERMES2_TEAMS_OUTGOING_SECRET"),
        "response_timeout_seconds": float(cfg.get("response_timeout_seconds", 4.0)),
    }
