from __future__ import annotations

import json
from pathlib import Path

from hermes2.runlog import RunLogger


def test_runlog_writes_jsonl_and_markdown_with_redaction(tmp_path: Path) -> None:
    config = {
        "paths": {
            "log_dir": str(tmp_path / "logs"),
            "report_dir": str(tmp_path / "reports"),
        }
    }
    logger = RunLogger(config, "default_task", {"OPENAI_API_KEY": "sk-secret"})
    logger.write({"type": "test", "value": "token sk-secret"})
    logger.write_report(
        workflow="default_task",
        user_input="input sk-secret",
        status="completed",
        step_outputs={"agent": "output sk-secret"},
        command_results=[{"stdout": "stdout sk-secret"}],
    )

    log_line = logger.log_file.read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(log_line)["value"] == "token [REDACTED:OPENAI_API_KEY]"
    report = logger.report_file.read_text(encoding="utf-8")
    assert "sk-secret" not in report
    assert "[REDACTED:OPENAI_API_KEY]" in report
