from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

from hermes2.config import hermes_home


SECRET_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "LMSTUDIO_API_KEY",
    "OLLAMA_API_KEY",
)


def load_environment(config: dict, repo_root: Path) -> list[Path]:
    """Load non-empty values from ~/.hermes/.env, then repo .env if needed.

    Existing shell environment values win. The home Hermes env is preferred over
    repo-local .env because it is the intended secret store.
    """

    loaded: list[Path] = []
    candidates = [hermes_home(config) / ".env", repo_root / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        loaded.append(path)
        for key, value in dotenv_values(path).items():
            if value in (None, ""):
                continue
            if os.environ.get(key) in (None, ""):
                os.environ[key] = str(value)
    return loaded


def key_status() -> dict[str, str]:
    return {key: ("set" if os.environ.get(key) else "empty") for key in SECRET_KEYS}


def secret_values() -> dict[str, str]:
    return {
        key: value
        for key in SECRET_KEYS
        if (value := os.environ.get(key)) and len(value) >= 4 and value != "local"
    }
