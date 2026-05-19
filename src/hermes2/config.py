from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when a Hermes 2.0 config file is missing or invalid."""


@dataclass(frozen=True)
class ConfigBundle:
    config: dict[str, Any]
    agents: dict[str, Any]
    workflows: dict[str, Any]
    config_path: Path
    agents_path: Path
    workflows_path: Path
    repo_root: Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    return repo_root() / "config" / "config.yaml"


def default_agents_path() -> Path:
    return repo_root() / "config" / "agents.yaml"


def default_workflows_path() -> Path:
    return repo_root() / "config" / "workflows.yaml"


def expand_path(value: str | os.PathLike[str]) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Missing config file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"YAML root must be a mapping: {path}")
    return loaded


def load_bundle(
    config_path: str | Path | None = None,
    agents_path: str | Path | None = None,
    workflows_path: str | Path | None = None,
) -> ConfigBundle:
    cpath = expand_path(config_path or default_config_path())
    apath = expand_path(agents_path or default_agents_path())
    wpath = expand_path(workflows_path or default_workflows_path())
    bundle = ConfigBundle(
        config=load_yaml(cpath),
        agents=load_yaml(apath),
        workflows=load_yaml(wpath),
        config_path=cpath,
        agents_path=apath,
        workflows_path=wpath,
        repo_root=repo_root(),
    )
    validate_bundle(bundle)
    return bundle


def validate_bundle(bundle: ConfigBundle) -> None:
    config = bundle.config
    agents_root = bundle.agents
    workflows_root = bundle.workflows

    if config.get("version") != 1:
        raise ConfigError("config.yaml must contain version: 1")
    if agents_root.get("version") != 1:
        raise ConfigError("agents.yaml must contain version: 1")
    if workflows_root.get("version") != 1:
        raise ConfigError("workflows.yaml must contain version: 1")

    models = config.get("models")
    agents = agents_root.get("agents")
    workflows = workflows_root.get("workflows")
    if not isinstance(models, dict) or not models:
        raise ConfigError("config.yaml must define at least one model alias")
    if not isinstance(agents, dict) or not agents:
        raise ConfigError("agents.yaml must define agents")
    if not isinstance(workflows, dict) or not workflows:
        raise ConfigError("workflows.yaml must define workflows")

    for alias, model in models.items():
        if not isinstance(model, dict):
            raise ConfigError(f"model alias {alias!r} must be a mapping")
        if not model.get("provider"):
            raise ConfigError(f"model alias {alias!r} must define provider")
        if not model.get("model"):
            raise ConfigError(f"model alias {alias!r} must define model")

    for name, agent in agents.items():
        if not isinstance(agent, dict):
            raise ConfigError(f"agent {name!r} must be a mapping")
        model_alias = agent.get("model")
        if model_alias not in models:
            raise ConfigError(f"agent {name!r} references unknown model alias {model_alias!r}")

    for workflow_name, workflow in workflows.items():
        if not isinstance(workflow, dict):
            raise ConfigError(f"workflow {workflow_name!r} must be a mapping")
        steps = workflow.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ConfigError(f"workflow {workflow_name!r} must define non-empty steps")
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise ConfigError(f"workflow {workflow_name!r} step {idx} must be a mapping")
            agent_name = step.get("agent")
            if agent_name not in agents:
                raise ConfigError(
                    f"workflow {workflow_name!r} step {idx} references unknown agent {agent_name!r}"
                )


def hermes_home(config: dict[str, Any]) -> Path:
    return expand_path(((config.get("paths") or {}).get("hermes_home")) or "~/.hermes")


def log_dir(config: dict[str, Any]) -> Path:
    return expand_path(((config.get("paths") or {}).get("log_dir")) or "~/.hermes/logs/hermes2")


def report_dir(config: dict[str, Any]) -> Path:
    return expand_path(
        ((config.get("paths") or {}).get("report_dir")) or "~/.hermes/logs/hermes2/reports"
    )


def ensure_observability_dirs(config: dict[str, Any]) -> tuple[Path, Path]:
    logs = log_dir(config)
    reports = report_dir(config)
    logs.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    return logs, reports


def workspace_allowed_paths(config: dict[str, Any]) -> list[Path]:
    configured = (config.get("runtime") or {}).get("workspace_allowed_paths") or ["~"]
    # YAML parses a bare "~" as null, so treat null entries as the home path.
    return [expand_path("~" if item is None else item) for item in configured]


def is_within(path: Path, parent: Path) -> bool:
    resolved_path = path.resolve()
    resolved_parent = parent.resolve()
    return resolved_path == resolved_parent or resolved_parent in resolved_path.parents


def validate_workspace(config: dict[str, Any], workspace: Path) -> Path:
    resolved = workspace.resolve()
    allowed = workspace_allowed_paths(config)
    if not any(is_within(resolved, item) for item in allowed):
        allowed_text = ", ".join(str(item) for item in allowed)
        raise ConfigError(f"workspace {resolved} is outside allowed paths: {allowed_text}")
    return resolved
