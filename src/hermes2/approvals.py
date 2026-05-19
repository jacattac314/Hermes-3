from __future__ import annotations

import re
from typing import Any


def approval_patterns(config: dict[str, Any]) -> list[str]:
    configured = ((config.get("approval_rules") or {}).get("always_require_approval")) or []
    return [str(item) for item in configured]


def is_risky_command(command: str, config: dict[str, Any]) -> bool:
    lowered = command.lower()
    for pattern in approval_patterns(config):
        normalized = pattern.lower().strip()
        if not normalized:
            continue
        if normalized in lowered:
            return True
        try:
            if re.search(normalized, lowered):
                return True
        except re.error:
            continue
    return False
