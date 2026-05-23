# Hermes 2.0 Repo Bootstrap

This repository contains a local-first Hermes 2.0 workflow runner. It keeps configuration templates in the repo, reads secrets from `~/.hermes/.env`, and writes run artifacts to `~/.hermes/logs/hermes2/` without overwriting the existing Hermes Agent installation.

## Quick Start

```bash
uv run hermes2 doctor
uv run hermes2 models
uv run hermes2 profiles
uv run hermes2 validate-config
uv run hermes2 chat
uv run hermes2 chat --message "Say hello in one sentence."
uv run hermes2 serve

uv run hermes2 run default_task \
  --input "Reply with exactly: HERMES2_OK"

uv run hermes2 run code_build \
  --workspace "/Users/jack/Documents/Hermes 2.0" \
  --input "Validate repo state" \
  --command "git status --short"
```

The local model is resolved defensively from LM Studio:

1. `HERMES2_LOCAL_MODEL`, if it is present in `/v1/models`.
2. `QWEN_MODEL`, if it is present in `/v1/models`.
3. `runtime.preferred_local_model` from `config/config.yaml`, if available.
4. A Qwen Coder model from `/v1/models`.
5. The first non-embedding model from `/v1/models`.

This means a stale `QWEN_MODEL=qwen-local` will not be used unless LM Studio currently reports that identifier.

## Profiles And Fallbacks

Hermes2 now mirrors the practical Hermes v6-style ideas from the macOS build guide:

- Profiles isolate intent. The default config ships with `default`, `code`, and `research`.
- Model chains provide local-first fallback order. The default chain tries LM Studio first, then Google Gemini, OpenAI, and Anthropic as cloud fallbacks when their API keys are set.
- Local server mode lets other tools call Hermes2 over localhost through `/health`, `/models`, `/chat`, and `/run`.
- Workflow execution stays command-gated. Chat mode does not run commands; `code_build` only runs commands passed with `--command`.

Inspect the active setup:

```bash
uv run hermes2 profiles
uv run hermes2 models --profile code
```

Use a profile in chat or workflows:

```bash
uv run hermes2 chat --profile research

uv run hermes2 run code_build \
  --profile code \
  --workspace "/Users/jack/Documents/Hermes 2.0" \
  --input "Validate the repo" \
  --command "git status --short"
```

Run Hermes2 as a local agent server:

```bash
uv run hermes2 serve --host 127.0.0.1 --port 8765 --profile default

curl http://127.0.0.1:8765/health

curl http://127.0.0.1:8765/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Reply with exactly: HERMES2_SERVER_OK"}'
```

The HTTP `/run` endpoint uses the same approval behavior as the CLI. In non-interactive server mode, risky commands fail closed unless the request explicitly sets `bypass_approvals: true`.

## Frontend Direction

The detailed frontend plan lives in [docs/frontend-spec.md](docs/frontend-spec.md). It describes a local Hermes2 Workbench with runtime status, chat, workflow execution, run reports, model routing, and settings views backed by the `hermes2 serve` API.

The implemented frontend lives in `frontend/`. It follows the Stitch mobile console direction: dark terminal panels, cyan telemetry, left icon rail, runtime inspector, model-chain monitor, command launcher, chat, and workflow dispatch.

Run it locally:

```bash
uv run hermes2 serve --host 127.0.0.1 --port 8765 --profile default

cd frontend
npm install
npm run dev
```

Then open `http://127.0.0.1:5173`. The frontend calls `http://127.0.0.1:8765` by default. Override it with `VITE_HERMES2_API_URL` if needed.

## Desktop App

Create or refresh the macOS app bundle:

```bash
scripts/create-desktop-app.sh --force
open "$HOME/Desktop/Hermes 2.0.app"
```

The desktop app starts the Hermes2 API and Workbench when needed, writes launcher logs to `~/.hermes/logs/hermes2/desktop-api.log` and `~/.hermes/logs/hermes2/desktop-web.log`, then opens a dedicated Chrome app window. The repo copy lives at `dist/Hermes 2.0.app`; the clickable app is installed at `~/Desktop/Hermes 2.0.app`.

The Talk view supports laptop voice use in Chrome: the mic button captures speech into the composer when browser speech recognition is available, and the read button speaks the latest Hermes2 response with browser text-to-speech. If speech recognition is not available, use macOS Dictation in the text field.

## Microsoft Teams

Hermes2 exposes a Teams Outgoing Webhook bridge at `POST /teams/outgoing`. This lets a Teams channel mention Hermes2 and receive a short reply from the local-first chat model. Setup requires a public HTTPS tunnel to the local Hermes2 server and the Teams-generated HMAC token in `~/.hermes/.env` as `HERMES2_TEAMS_OUTGOING_SECRET`.

See [docs/teams-setup.md](docs/teams-setup.md) for the exact setup steps and limitations.

## Android Mobile

Hermes2 also serves an installable Android PWA from the same local API server:

```bash
cd frontend && npm run build && cd ..
HERMES2_MOBILE_TOKEN="$(openssl rand -base64 24)"
uv run hermes2 serve --host 0.0.0.0 --port 8765 --profile default
```

Open `http://<mac-tailscale-name-or-ip>:8765/mobile` from Android Chrome. The mobile UI supports chat, voice input where the browser exposes it, text-to-speech, quick prompts, and command-gated workflow runs against the Mac. Hermes2 refuses non-loopback binds such as `0.0.0.0` unless `HERMES2_MOBILE_TOKEN` is set, so store the token in `~/.hermes/.env` for daily use.

See [docs/android-mobile.md](docs/android-mobile.md) for Tailscale, token, and optional ntfy notification setup.

The Workbench reads persisted observability data from Hermes2:

- `GET /runs` lists recent JSONL traces from `~/.hermes/logs/hermes2/`
- `GET /report?name=<report.md>` returns a Markdown report from `~/.hermes/logs/hermes2/reports/`
- `GET /tools` lists configured runtime tools such as shell, filesystem, browser, and computer use
- `POST /tools/execute` runs guarded tool actions through built-in handlers or configured adapters

Tool behavior is conservative by default. Shell commands still run through workflow execution with explicit command values. Computer use is configured through the installed Codex Computer Use MCP stdio adapter at `/Users/jack/.codex/computer-use/.../SkyComputerUseClient`. The `adapter_tools` action verifies the MCP handshake and lists the adapter's available tools. Direct UI actions such as `get_app_state`, `click`, `type_text`, `press_key`, `scroll`, and `drag` are blocked unless the caller sends an explicit `approved=true` payload after action-time confirmation.

## Optional Wrapper

To install a convenience wrapper without touching `~/.hermes/config.yaml`:

```bash
scripts/install-hermes2ctl.sh
~/.hermes/bin/hermes2ctl doctor
~/.hermes/bin/hermes2ctl chat
```

If `~/.hermes/bin/hermes2ctl` already exists, the installer refuses to overwrite it unless `--force` is passed.

## Logs

Every workflow run writes:

- JSONL trace: `~/.hermes/logs/hermes2/*.jsonl`
- Markdown report: `~/.hermes/logs/hermes2/reports/*.md`

Command output and model output are redacted for any known non-empty secret values loaded from the environment.
