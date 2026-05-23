from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
import os
from pathlib import Path
from typing import Any, Protocol

import yaml


class ConfigError(ValueError):
    """Raised when a Hermes 2.0 config file is missing or invalid."""


class ReadablePath(Protocol):
    def exists(self) -> bool: ...
    def open(self, mode: str = "r", encoding: str | None = None): ...


@dataclass(frozen=True)
class ConfigBundle:
    config: dict[str, Any]
    agents: dict[str, Any]
    workflows: dict[str, Any]
    config_path: Path | ReadablePath
    agents_path: Path | ReadablePath
    workflows_path: Path | ReadablePath
    repo_root: Path


def repo_root() -> Path:
    cwd = Path.cwd().resolve()
    if (cwd / "pyproject.toml").exists() and (cwd / "config" / "config.yaml").exists():
        return cwd
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").exists() and (source_root / "config" / "config.yaml").exists():
        return source_root
    return cwd


def _default_resource(name: str):
    return resources.files("hermes2.defaults").joinpath(name)


def default_config_path() -> Path:
    candidate = repo_root() / "config" / "config.yaml"
    return candidate if candidate.exists() else _default_resource("config.yaml")


def default_agents_path() -> Path:
    candidate = repo_root() / "config" / "agents.yaml"
    return candidate if candidate.exists() else _default_resource("agents.yaml")


def default_workflows_path() -> Path:
    candidate = repo_root() / "config" / "workflows.yaml"
    return candidate if candidate.exists() else _default_resource("workflows.yaml")


def expand_path(value: str | os.PathLike[str]) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


def load_yaml(path: Path | ReadablePath) -> dict[str, Any]:
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

    chains = config.get("model_chains") or {}
    if chains and not isinstance(chains, dict):
        raise ConfigError("model_chains must be a mapping")
    for chain_name, aliases in chains.items():
        if not isinstance(aliases, list) or not aliases:
            raise ConfigError(f"model chain {chain_name!r} must be a non-empty list")
        for alias in aliases:
            if alias not in models:
                raise ConfigError(f"model chain {chain_name!r} references unknown model alias {alias!r}")

    profiles = config.get("profiles") or {}
    if profiles and not isinstance(profiles, dict):
        raise ConfigError("profiles must be a mapping")
    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            raise ConfigError(f"profile {profile_name!r} must be a mapping")
        chain = profile.get("model_chain")
        if isinstance(chain, str) and chain not in chains:
            raise ConfigError(f"profile {profile_name!r} references unknown model chain {chain!r}")
        if isinstance(chain, list):
            for alias in chain:
                if alias not in models:
                    raise ConfigError(f"profile {profile_name!r} references unknown model alias {alias!r}")
        profile_workflows = profile.get("workflows") or []
        if profile_workflows and not isinstance(profile_workflows, list):
            raise ConfigError(f"profile {profile_name!r} workflows must be a list")
        for workflow_name in profile_workflows:
            if workflow_name not in workflows:
                raise ConfigError(f"profile {profile_name!r} references unknown workflow {workflow_name!r}")

    tools = config.get("tools") or {}
    if tools and not isinstance(tools, dict):
        raise ConfigError("tools must be a mapping")
    for tool_name, tool in tools.items():
        if not isinstance(tool, dict):
            raise ConfigError(f"tool {tool_name!r} must be a mapping")
        if "enabled" in tool and not isinstance(tool["enabled"], bool):
            raise ConfigError(f"tool {tool_name!r} enabled must be boolean")
        if "adapter" in tool and not isinstance(tool["adapter"], str):
            raise ConfigError(f"tool {tool_name!r} adapter must be a string")
        if tool.get("adapter") == "mcp_stdio" and not isinstance(tool.get("command"), str):
            raise ConfigError(f"tool {tool_name!r} mcp_stdio adapter requires command")
        if "args" in tool and not isinstance(tool["args"], list):
            raise ConfigError(f"tool {tool_name!r} args must be a list")
        for arg in tool.get("args") or []:
            if not isinstance(arg, str):
                raise ConfigError(f"tool {tool_name!r} args values must be strings")
        if "cwd" in tool and not isinstance(tool["cwd"], str):
            raise ConfigError(f"tool {tool_name!r} cwd must be a string")
        if "transport" in tool and not isinstance(tool["transport"], str):
            raise ConfigError(f"tool {tool_name!r} transport must be a string")
        actions = tool.get("actions") or []
        if not isinstance(actions, list):
            raise ConfigError(f"tool {tool_name!r} actions must be a list")
        for action in actions:
            if not isinstance(action, str):
                raise ConfigError(f"tool {tool_name!r} action values must be strings")

    teams = config.get("teams") or {}
    if teams and not isinstance(teams, dict):
        raise ConfigError("teams must be a mapping")
    if "enabled" in teams and not isinstance(teams["enabled"], bool):
        raise ConfigError("teams enabled must be boolean")
    if "endpoint" in teams and not isinstance(teams["endpoint"], str):
        raise ConfigError("teams endpoint must be a string")
    if "profile" in teams and teams["profile"] not in profiles:
        raise ConfigError(f"teams profile references unknown profile {teams['profile']!r}")
    if "allow_unsigned_dev_requests" in teams and not isinstance(teams["allow_unsigned_dev_requests"], bool):
        raise ConfigError("teams allow_unsigned_dev_requests must be boolean")

    mobile = config.get("mobile") or {}
    if mobile and not isinstance(mobile, dict):
        raise ConfigError("mobile must be a mapping")
    if "enabled" in mobile and not isinstance(mobile["enabled"], bool):
        raise ConfigError("mobile enabled must be boolean")
    if "path" in mobile and not isinstance(mobile["path"], str):
        raise ConfigError("mobile path must be a string")
    if "token_env" in mobile and not isinstance(mobile["token_env"], str):
        raise ConfigError("mobile token_env must be a string")
    if "require_token" in mobile and not isinstance(mobile["require_token"], bool):
        raise ConfigError("mobile require_token must be boolean")
    ntfy = mobile.get("ntfy") or {}
    if ntfy and not isinstance(ntfy, dict):
        raise ConfigError("mobile ntfy must be a mapping")
    if "enabled" in ntfy and not isinstance(ntfy["enabled"], bool):
        raise ConfigError("mobile ntfy enabled must be boolean")
    for key in ("server", "topic_env", "token_env"):
        if key in ntfy and not isinstance(ntfy[key], str):
            raise ConfigError(f"mobile ntfy {key} must be a string")

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
