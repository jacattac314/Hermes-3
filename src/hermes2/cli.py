from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from hermes2.config import ConfigError, ensure_observability_dirs, load_bundle
from hermes2.env import key_status, load_environment, secret_values
from hermes2.llm import LLMError
from hermes2.models import ModelResolutionError, fetch_openai_models, resolve_local_model
from hermes2.runlog import RunLogger
from hermes2.workflow import run_workflow


def add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--agents", help="Path to agents.yaml")
    parser.add_argument("--workflows", help="Path to workflows.yaml")


def load_ready_bundle(args: argparse.Namespace):
    bundle = load_bundle(args.config, args.agents, args.workflows)
    load_environment(bundle.config, bundle.repo_root)
    return bundle


def command_version(command: str, timeout: int = 10) -> str:
    path = shutil.which(command)
    if not path:
        return "missing"
    try:
        completed = subprocess.run(
            [command, "--version"],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - defensive status output
        return f"{path} ({exc})"
    output = (completed.stdout or completed.stderr).strip().splitlines()
    suffix = output[0] if output else f"exit {completed.returncode}"
    return f"{path} ({suffix})"


def cmd_validate_config(args: argparse.Namespace) -> int:
    load_ready_bundle(args)
    print("Config valid.")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    bundle = load_ready_bundle(args)
    local_cfg = (bundle.config.get("models") or {}).get("local_worker") or {}
    resolution = resolve_local_model(bundle.config, local_cfg)
    print(f"Base URL: {resolution.base_url}")
    print(f"Selected model: {resolution.model}")
    print(f"Reason: {resolution.reason}")
    if resolution.rejected_candidates:
        print("Rejected candidates:")
        for candidate in resolution.rejected_candidates:
            print(f"  - {candidate}")
    print("Available models:")
    for model in resolution.available_models:
        print(f"  - {model}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    bundle = load_ready_bundle(args)
    logs, reports = ensure_observability_dirs(bundle.config)
    print("Hermes 2.0 Doctor")
    print(f"Repo: {bundle.repo_root}")
    print(f"Hermes CLI: {command_version('hermes')}")
    print(f"Log dir: {logs}")
    print(f"Report dir: {reports}")
    print("Keys:")
    for key, status in key_status().items():
        print(f"  {key}: {status}")
    local_cfg = (bundle.config.get("models") or {}).get("local_worker") or {}
    try:
        resolution = resolve_local_model(bundle.config, local_cfg, timeout=2)
        print(f"LM Studio/OpenAI-compatible server: reachable")
        print(f"Selected local model: {resolution.model}")
        print(f"Selection reason: {resolution.reason}")
        if resolution.rejected_candidates:
            print("Rejected candidates:")
            for candidate in resolution.rejected_candidates:
                print(f"  - {candidate}")
        print(f"Model count: {len(resolution.available_models)}")
    except ModelResolutionError as exc:
        print(f"LM Studio/OpenAI-compatible server: failed ({exc})")
        return 2
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    bundle = load_ready_bundle(args)
    logger = RunLogger(bundle.config, args.workflow, secret_values())
    result = run_workflow(
        bundle=bundle,
        workflow_name=args.workflow,
        user_input=args.input,
        workspace=Path(args.workspace).expanduser() if args.workspace else None,
        commands=args.command or [],
        bypass_approvals=args.bypass_approvals,
        logger=logger,
    )
    print(f"Status: {result.status}")
    print(f"JSONL log: {result.log_file}")
    print(f"Markdown report: {result.report_file}")
    return result.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes2", description="Hermes 2.0 repo-local workflow runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local runtime readiness")
    add_config_args(doctor)
    doctor.set_defaults(func=cmd_doctor)

    models = subparsers.add_parser("models", help="Resolve and list local models")
    add_config_args(models)
    models.set_defaults(func=cmd_models)

    validate = subparsers.add_parser("validate-config", help="Validate config, agents, and workflows")
    add_config_args(validate)
    validate.set_defaults(func=cmd_validate_config)

    run = subparsers.add_parser("run", help="Run a configured workflow")
    add_config_args(run)
    run.add_argument("workflow", help="Workflow name from workflows.yaml")
    run.add_argument("--input", required=True, help="User input for the workflow")
    run.add_argument("--workspace", help="Workspace for executor commands")
    run.add_argument("--command", action="append", default=[], help="Explicit command for the executor step")
    run.add_argument(
        "--bypass-approvals",
        action="store_true",
        help="Run risky commands without prompting. Use only for trusted local runs.",
    )
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, LLMError, ModelResolutionError) as exc:
        print(f"hermes2 error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
