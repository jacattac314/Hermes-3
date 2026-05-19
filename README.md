# Hermes 2.0 Repo Bootstrap

This repository contains a local-first Hermes 2.0 workflow runner. It keeps configuration templates in the repo, reads secrets from `~/.hermes/.env`, and writes run artifacts to `~/.hermes/logs/hermes2/` without overwriting the existing Hermes Agent installation.

## Quick Start

```bash
uv run hermes2 doctor
uv run hermes2 models
uv run hermes2 validate-config

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

## Optional Wrapper

To install a convenience wrapper without touching `~/.hermes/config.yaml`:

```bash
scripts/install-hermes2ctl.sh
~/.hermes/bin/hermes2ctl doctor
```

If `~/.hermes/bin/hermes2ctl` already exists, the installer refuses to overwrite it unless `--force` is passed.

## Logs

Every workflow run writes:

- JSONL trace: `~/.hermes/logs/hermes2/*.jsonl`
- Markdown report: `~/.hermes/logs/hermes2/reports/*.md`

Command output and model output are redacted for any known non-empty secret values loaded from the environment.
