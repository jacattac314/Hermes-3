from __future__ import annotations

from pathlib import Path

import pytest

from hermes2.config import ConfigError, load_bundle, validate_workspace


def test_default_config_bundle_is_valid() -> None:
    bundle = load_bundle()
    assert bundle.config["version"] == 1
    assert "local_worker" in bundle.config["models"]
    assert "default_task" in bundle.workflows["workflows"]


def test_workspace_allowed_under_home() -> None:
    config = {"runtime": {"workspace_allowed_paths": ["~"]}}
    resolved = validate_workspace(config, Path.home())
    assert resolved == Path.home().resolve()


def test_yaml_null_workspace_entry_means_home() -> None:
    config = {"runtime": {"workspace_allowed_paths": [None]}}
    resolved = validate_workspace(config, Path.home())
    assert resolved == Path.home().resolve()


def test_workspace_rejected_outside_allowed_path(tmp_path: Path) -> None:
    config = {"runtime": {"workspace_allowed_paths": [str(tmp_path / "allowed")]}}
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ConfigError):
        validate_workspace(config, outside)
