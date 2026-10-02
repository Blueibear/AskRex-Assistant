"""Environment variable loader for Rex AI Assistant.

This module ensures .env files are loaded before any environment variables
are accessed, preventing initialization issues.

The .env file is automatically loaded when this module is first imported.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:
    # Gracefully handle missing python-dotenv
    def load_dotenv(*args, **kwargs):
        return False

    def find_dotenv(*args, **kwargs):
        return ""


_loaded = False


def load() -> None:
    """Load environment variables from .env file.

    Uses find_dotenv(usecwd=True) first, then falls back to <repo_root>/.env.
    Only loads once per process. Uses override=False to respect existing env vars.
    """
    global _loaded
    if _loaded:
        return

    explicit_path = os.getenv("ASKREX_ENV_PATH", "").strip()
    runtime_root = os.getenv("ASKREX_RUNTIME_DIR", "").strip()
    dotenv_path = explicit_path

    if not dotenv_path and runtime_root:
        runtime_path = Path(runtime_root).expanduser() / ".env"
        if runtime_path.exists():
            dotenv_path = str(runtime_path)

    if not dotenv_path:
        dotenv_path = find_dotenv(usecwd=True)

    if not dotenv_path:
        repo_root = Path(__file__).resolve().parent.parent
        fallback_path = repo_root / ".env"
        if fallback_path.exists():
            dotenv_path = str(fallback_path)

    # Load the .env file if found
    if dotenv_path:
        load_dotenv(dotenv_path, override=False)

    _loaded = True


# Automatically load .env on module import
load()
