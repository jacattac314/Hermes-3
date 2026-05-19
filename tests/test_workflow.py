from __future__ import annotations

from pathlib import Path

from hermes2.runlog import RunLogger
from hermes2.workflow import run_commands


def test_risky_command_fails_closed_without_tty(tmp_path: Path) -> None:
    config = {
        "paths": {
            "log_dir": str(tmp_path / "logs"),
            "report_dir": str(tmp_path / "reports"),
        },
        "approval_rules": {"always_require_approval": ["git push"]},
        "runtime": {"command_timeout_seconds": 1},
    }
    logger = RunLogger(config, "risk", {})
    results = run_commands(
        commands=["git push origin main"],
        workspace=tmp_path,
        config=config,
        logger=logger,
        bypass_approvals=False,
    )

    assert results[0]["approval_required"] is True
    assert results[0]["approved"] is False
    assert results[0]["returncode"] is None
    assert "Approval required" in results[0]["stderr"]
