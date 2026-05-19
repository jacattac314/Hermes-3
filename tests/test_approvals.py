from __future__ import annotations

from hermes2.approvals import is_risky_command


CONFIG = {
    "approval_rules": {
        "always_require_approval": [
            "git push",
            "rm -rf",
            "sudo",
            "chmod -R",
            "docker system prune",
            "curl -X POST",
        ]
    }
}


def test_safe_command_is_not_risky() -> None:
    assert not is_risky_command("git status --short", CONFIG)


def test_git_push_is_risky() -> None:
    assert is_risky_command("git push origin main", CONFIG)


def test_recursive_remove_is_risky() -> None:
    assert is_risky_command("rm -rf build", CONFIG)
