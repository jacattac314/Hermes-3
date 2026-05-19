from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from hermes2.approvals import is_risky_command
from hermes2.config import ConfigBundle, ConfigError, validate_workspace
from hermes2.llm import LLMError, invoke_model
from hermes2.models import ModelFetcher, effective_model_config, fetch_openai_models
from hermes2.runlog import RunLogger


@dataclass(frozen=True)
class WorkflowRunResult:
    status: str
    exit_code: int
    log_file: Path
    report_file: Path
    command_results: list[dict[str, Any]]


def _shorten(value: str, limit: int = 1600) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def _state_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    outputs = state.get("step_outputs") or {}
    return {
        "previous_outputs": {key: _shorten(str(value), 1000) for key, value in outputs.items()},
        "executor_results": state.get("executor_results", []),
    }


def build_prompt(
    *,
    agent_name: str,
    action: str,
    agent_cfg: dict[str, Any],
    user_input: str,
    workspace: Path,
    commands: list[str],
    state: dict[str, Any],
) -> str:
    payload = {
        "user_input": user_input,
        "workspace": str(workspace),
        "commands_supplied": commands,
        "state": _state_snapshot(state),
    }
    return f"""You are the '{agent_name}' agent in Hermes 2.0.

Role:
{agent_cfg.get("description", "").strip()}

System prompt:
{agent_cfg.get("system_prompt", "").strip()}

Action:
{action}

Workflow payload:
{json.dumps(payload, ensure_ascii=False, indent=2)}

Grounding rules:
- The executor_results array is authoritative for command execution.
- A command with approved=false or returncode=null was skipped, not executed.
- Do not claim a command ran unless executor_results show approved=true and a numeric returncode.

Return concise Markdown useful to the local operator.
"""


def run_commands(
    *,
    commands: list[str],
    workspace: Path,
    config: dict[str, Any],
    logger: RunLogger,
    bypass_approvals: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    timeout = int((config.get("runtime") or {}).get("command_timeout_seconds", 300))
    for command in commands:
        risky = is_risky_command(command, config)
        approved = True
        approval_required = risky and not bypass_approvals
        if approval_required:
            if sys.stdin.isatty():
                answer = input(f"Approve command: {command} [y/N]: ").strip().lower()
                approved = answer in {"y", "yes"}
            else:
                approved = False

        if not approved:
            result = {
                "command": command,
                "workspace": str(workspace),
                "risky": risky,
                "approval_required": approval_required,
                "approved": False,
                "returncode": None,
                "stdout": "",
                "stderr": "Approval required; command skipped.",
            }
            logger.write({"type": "command", **result})
            results.append(result)
            continue

        try:
            completed = subprocess.run(
                command,
                cwd=str(workspace),
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            result = {
                "command": command,
                "workspace": str(workspace),
                "risky": risky,
                "approval_required": approval_required,
                "approved": True,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-12000:],
                "stderr": completed.stderr[-12000:],
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "command": command,
                "workspace": str(workspace),
                "risky": risky,
                "approval_required": approval_required,
                "approved": True,
                "returncode": 124,
                "stdout": (exc.stdout or "")[-12000:] if isinstance(exc.stdout, str) else "",
                "stderr": f"Command timed out after {timeout}s",
            }
        logger.write({"type": "command", **result})
        results.append(result)
    return results


def command_results_failed(results: list[dict[str, Any]]) -> bool:
    return any(result.get("returncode") not in (0, None) or result.get("approved") is False for result in results)


def run_workflow(
    *,
    bundle: ConfigBundle,
    workflow_name: str,
    user_input: str,
    workspace: Path | None,
    commands: list[str],
    bypass_approvals: bool,
    logger: RunLogger,
    fetcher: ModelFetcher = fetch_openai_models,
) -> WorkflowRunResult:
    workflows = bundle.workflows["workflows"]
    agents = bundle.agents["agents"]
    if workflow_name not in workflows:
        raise ConfigError(f"unknown workflow: {workflow_name}")

    resolved_workspace = validate_workspace(bundle.config, workspace or Path.cwd())
    workflow = workflows[workflow_name]
    state: dict[str, Any] = {"step_outputs": {}, "executor_results": []}
    command_results: list[dict[str, Any]] = []
    status = "completed"
    exit_code = 0

    logger.write(
        {
            "type": "run_start",
            "workflow": workflow_name,
            "workspace": str(resolved_workspace),
            "commands": commands,
        }
    )

    for step in workflow["steps"]:
        agent_name = step["agent"]
        action = step.get("action", agent_name)
        agent_cfg = agents[agent_name]
        model_cfg = effective_model_config(str(agent_cfg["model"]), bundle.config, fetcher=fetcher)
        resolution = model_cfg.get("_resolution")
        if resolution:
            logger.write(
                {
                    "type": "model_resolution",
                    "agent": agent_name,
                    "provider": resolution.provider,
                    "model": resolution.model,
                    "base_url": resolution.base_url,
                    "reason": resolution.reason,
                    "rejected_candidates": resolution.rejected_candidates,
                }
            )

        prompt = build_prompt(
            agent_name=agent_name,
            action=action,
            agent_cfg=agent_cfg,
            user_input=user_input,
            workspace=resolved_workspace,
            commands=commands,
            state=state,
        )
        temperature = float(agent_cfg.get("temperature", (bundle.config.get("runtime") or {}).get("default_temperature", 0.2)))
        max_tokens = int(agent_cfg.get("max_tokens", (bundle.config.get("runtime") or {}).get("default_max_tokens", 512)))
        timeout = float((bundle.config.get("runtime") or {}).get("request_timeout_seconds", 300))
        logger.write(
            {
                "type": "agent_start",
                "agent": agent_name,
                "action": action,
                "provider": model_cfg.get("provider"),
                "model": model_cfg.get("model"),
            }
        )
        try:
            output = invoke_model(
                model_cfg=model_cfg,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except LLMError as exc:
            status = "failed"
            exit_code = 4
            output = f"Model invocation failed: {exc}"
            state["step_outputs"][agent_name] = output
            logger.write(
                {
                    "type": "agent_error",
                    "agent": agent_name,
                    "action": action,
                    "provider": model_cfg.get("provider"),
                    "model": model_cfg.get("model"),
                    "error": str(exc),
                }
            )
            break
        state["step_outputs"][agent_name] = output
        logger.write(
            {
                "type": "agent_step",
                "agent": agent_name,
                "action": action,
                "provider": model_cfg.get("provider"),
                "model": model_cfg.get("model"),
                "output": output,
            }
        )

        if agent_name == "executor":
            command_results = run_commands(
                commands=commands,
                workspace=resolved_workspace,
                config=bundle.config,
                logger=logger,
                bypass_approvals=bypass_approvals,
            )
            state["executor_results"] = command_results
            state["step_outputs"][agent_name] = output + "\n\nCommand results:\n```json\n" + json.dumps(command_results, ensure_ascii=False, indent=2) + "\n```"

    if status == "completed" and command_results_failed(command_results):
        status = "failed"
        exit_code = 3

    logger.write({"type": "run_end", "workflow": workflow_name, "status": status, "exit_code": exit_code})
    logger.write_report(
        workflow=workflow_name,
        user_input=user_input,
        status=status,
        step_outputs=state["step_outputs"],
        command_results=command_results,
    )
    return WorkflowRunResult(
        status=status,
        exit_code=exit_code,
        log_file=logger.log_file,
        report_file=logger.report_file,
        command_results=command_results,
    )
