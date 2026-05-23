from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from hermes2.config import ConfigError, ensure_observability_dirs, load_bundle
from hermes2.env import key_status, load_environment, secret_values
from hermes2.llm import LLMError, invoke_chat
from hermes2.models import (
    ModelResolutionError,
    effective_model_chain,
    profile_config,
    profile_model_aliases,
    resolve_local_model,
)
from hermes2.runlog import RunLogger
from hermes2.server import serve_http
from hermes2.teams import teams_bridge_payload
from hermes2.tools import tools_payload
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
    candidates, skipped = effective_model_chain(
        primary_alias=args.model_alias,
        config=bundle.config,
        profile_name=args.profile,
    )
    print(f"Profile: {args.profile}")
    print("Effective model chain:")
    for idx, candidate in enumerate(candidates, start=1):
        resolution = candidate.config.get("_resolution")
        print(
            f"  {idx}. {candidate.alias}: "
            f"{candidate.config.get('provider')}:{candidate.config.get('model')}"
        )
        if candidate.config.get("base_url"):
            print(f"     Base URL: {candidate.config.get('base_url')}")
        if resolution:
            print(f"     Reason: {resolution.reason}")
            if resolution.rejected_candidates:
                print(f"     Rejected: {', '.join(resolution.rejected_candidates)}")
    if skipped:
        print("Skipped candidates:")
        for item in skipped:
            print(f"  - {item}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    bundle = load_ready_bundle(args)
    logs, reports = ensure_observability_dirs(bundle.config)
    print("Hermes 2.0 Doctor")
    print(f"Repo: {bundle.repo_root}")
    print(f"Profile: {args.profile}")
    print(f"Hermes CLI: {command_version('hermes')}")
    print(f"Log dir: {logs}")
    print(f"Report dir: {reports}")
    print("Keys:")
    for key, status in key_status().items():
        print(f"  {key}: {status}")
    print("Tools:")
    for tool in tools_payload(bundle.config)["tools"]:
        print(f"  {tool['name']}: {tool['status']} ({tool['adapter']})")
    teams = teams_bridge_payload(bundle.config)
    print(f"Teams bridge: {'enabled' if teams['enabled'] else 'disabled'} ({teams['mode']}, {teams['endpoint']})")
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


def cmd_profiles(args: argparse.Namespace) -> int:
    bundle = load_ready_bundle(args)
    profiles = bundle.config.get("profiles") or {"default": {"description": "Default profile"}}
    for name, profile in profiles.items():
        description = profile.get("description", "")
        aliases = profile_model_aliases(bundle.config, name)
        marker = "*" if name == args.profile else " "
        print(f"{marker} {name}: {description}")
        print(f"    chain: {', '.join(aliases)}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    bundle = load_ready_bundle(args)
    profile_config(bundle.config, args.profile)
    logger = RunLogger(bundle.config, args.workflow, secret_values())
    result = run_workflow(
        bundle=bundle,
        workflow_name=args.workflow,
        user_input=args.input,
        workspace=Path(args.workspace).expanduser() if args.workspace else None,
        commands=args.command or [],
        bypass_approvals=args.bypass_approvals,
        logger=logger,
        profile_name=args.profile,
    )
    print(f"Status: {result.status}")
    print(f"JSONL log: {result.log_file}")
    print(f"Markdown report: {result.report_file}")
    return result.exit_code


def _chat_system_prompt() -> str:
    return (
        "You are Hermes 2.0, a local-first macOS agentic workflow assistant. "
        "Be concise, practical, and explicit about uncertainty. "
        "Do not claim to run commands or edit files from chat mode; tell the user to use "
        "`hermes2 run code_build --command ...` for executor-backed workflows."
    )


def _print_chat_header(candidates: list, profile_name: str) -> None:
    first = candidates[0]
    model = first.config.get("model")
    provider = first.config.get("provider")
    print(f"Hermes 2.0 chat [{profile_name}] ({provider}:{model})")
    resolution = first.config.get("_resolution")
    if resolution:
        print(f"Model resolution: {resolution.reason}")
    if len(candidates) > 1:
        print("Fallbacks: " + ", ".join(f"{item.config.get('provider')}:{item.config.get('model')}" for item in candidates[1:]))
    print("Commands: /exit, /quit, /clear, /model")
    print()


def _chat_once(bundle, args: argparse.Namespace, message: str) -> int:
    candidates, _skipped = effective_model_chain(
        primary_alias=args.model_alias,
        config=bundle.config,
        profile_name=args.profile,
    )
    timeout = float((bundle.config.get("runtime") or {}).get("request_timeout_seconds", 300))
    response = _invoke_chat_with_fallback(
        candidates=candidates,
        messages=[{"role": "system", "content": _chat_system_prompt()}, {"role": "user", "content": message}],
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout=timeout,
    )
    print(response)
    return 0


def _invoke_chat_with_fallback(
    *,
    candidates: list,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str:
    errors: list[str] = []
    for candidate in candidates:
        try:
            return invoke_chat(
                model_cfg=candidate.config,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except LLMError as exc:
            errors.append(f"{candidate.alias}: {exc}")
    raise LLMError("all chat model candidates failed: " + "; ".join(errors))


def cmd_chat(args: argparse.Namespace) -> int:
    bundle = load_ready_bundle(args)
    profile_config(bundle.config, args.profile)
    if args.message:
        return _chat_once(bundle, args, args.message)

    candidates, skipped = effective_model_chain(
        primary_alias=args.model_alias,
        config=bundle.config,
        profile_name=args.profile,
    )
    _print_chat_header(candidates, args.profile)
    if skipped:
        print("Skipped: " + "; ".join(skipped))
    messages = [{"role": "system", "content": _chat_system_prompt()}]
    timeout = float((bundle.config.get("runtime") or {}).get("request_timeout_seconds", 300))

    while True:
        try:
            user_text = input("you> ").strip()
        except EOFError:
            print()
            return 0
        if not user_text:
            continue
        lowered = user_text.lower()
        if lowered in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if lowered == "/clear":
            messages = [{"role": "system", "content": _chat_system_prompt()}]
            print("History cleared.")
            continue
        if lowered == "/model":
            for idx, candidate in enumerate(candidates, start=1):
                print(f"{idx}. {candidate.alias}: {candidate.config.get('provider')}:{candidate.config.get('model')}")
            continue

        messages.append({"role": "user", "content": user_text})
        response = _invoke_chat_with_fallback(
            candidates=candidates,
            messages=messages,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=timeout,
        )
        messages.append({"role": "assistant", "content": response})
        print(f"hermes2> {response}\n")


def cmd_serve(args: argparse.Namespace) -> int:
    bundle = load_ready_bundle(args)
    serve_http(bundle, host=args.host, port=args.port, profile_name=args.profile)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes2", description="Hermes 2.0 repo-local workflow runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local runtime readiness")
    add_config_args(doctor)
    doctor.add_argument("--profile", default="default")
    doctor.set_defaults(func=cmd_doctor)

    models = subparsers.add_parser("models", help="Resolve and list local models")
    add_config_args(models)
    models.add_argument("--profile", default="default")
    models.add_argument("--model-alias", default="local_worker")
    models.set_defaults(func=cmd_models)

    profiles = subparsers.add_parser("profiles", help="List configured Hermes2 profiles")
    add_config_args(profiles)
    profiles.add_argument("--profile", default="default", help="Profile to mark as selected")
    profiles.set_defaults(func=cmd_profiles)

    validate = subparsers.add_parser("validate-config", help="Validate config, agents, and workflows")
    add_config_args(validate)
    validate.set_defaults(func=cmd_validate_config)

    chat = subparsers.add_parser("chat", help="Start an interactive local Hermes 2.0 chat")
    add_config_args(chat)
    chat.add_argument("--message", "-m", help="Send one message and exit")
    chat.add_argument("--model-alias", default="local_worker", help="Model alias from config.yaml")
    chat.add_argument("--profile", default="default")
    chat.add_argument("--temperature", type=float, default=0.2)
    chat.add_argument("--max-tokens", type=int, default=512)
    chat.set_defaults(func=cmd_chat)

    serve = subparsers.add_parser("serve", help="Run Hermes 2.0 as a local HTTP agent server")
    add_config_args(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--profile", default="default")
    serve.set_defaults(func=cmd_serve)

    run = subparsers.add_parser("run", help="Run a configured workflow")
    add_config_args(run)
    run.add_argument("workflow", help="Workflow name from workflows.yaml")
    run.add_argument("--input", required=True, help="User input for the workflow")
    run.add_argument("--profile", default="default")
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
