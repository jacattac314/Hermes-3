from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from hermes2.config import ensure_observability_dirs


def redact_text(text: str, secrets: dict[str, str]) -> str:
    redacted = text
    for key, value in secrets.items():
        if value:
            redacted = redacted.replace(value, f"[REDACTED:{key}]")
    return redacted


def redact_payload(payload: Any, secrets: dict[str, str]) -> Any:
    if isinstance(payload, str):
        return redact_text(payload, secrets)
    if isinstance(payload, list):
        return [redact_payload(item, secrets) for item in payload]
    if isinstance(payload, dict):
        return {key: redact_payload(value, secrets) for key, value in payload.items()}
    return payload


class RunLogger:
    def __init__(self, config: dict[str, Any], workflow: str, secrets: dict[str, str]) -> None:
        logs, reports = ensure_observability_dirs(config)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = uuid.uuid4().hex[:8]
        safe_workflow = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in workflow)
        self.log_file = logs / f"{stamp}-{safe_workflow}-{suffix}.jsonl"
        self.report_file = reports / f"{stamp}-{safe_workflow}-{suffix}.md"
        self.secrets = secrets
        self.events: list[dict[str, Any]] = []

    def write(self, event: dict[str, Any]) -> None:
        safe_event = redact_payload(event, self.secrets)
        self.events.append(safe_event)
        with self.log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe_event, ensure_ascii=False) + "\n")

    def write_report(
        self,
        *,
        workflow: str,
        user_input: str,
        status: str,
        step_outputs: dict[str, str],
        command_results: list[dict[str, Any]],
    ) -> None:
        lines = [
            f"# Hermes 2.0 run: {workflow}",
            "",
            f"Status: {status}",
            "",
            "## Input",
            "",
            redact_text(user_input, self.secrets),
            "",
        ]
        if command_results:
            lines.extend(
                [
                    "## Verified Command Results",
                    "",
                    "```json",
                    json.dumps(redact_payload(command_results, self.secrets), ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
        lines.extend(["## Steps", ""])
        for name, output in step_outputs.items():
            lines.extend([f"### {name}", "", redact_text(output, self.secrets), ""])
        if command_results:
            lines.extend(
                [
                    "## Command Results Copy",
                    "",
                    "```json",
                    json.dumps(redact_payload(command_results, self.secrets), ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
        self.report_file.write_text("\n".join(lines), encoding="utf-8")
