"""Tests for US-053: Secret management.

Acceptance criteria:
- secrets loaded from environment
- secrets not stored in repo
- missing secrets detected
- Typecheck passes
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rex.credentials import (
    CredentialManager,
    get_credential_manager,
    mask_token,
    set_credential_manager,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_credential_manager(monkeypatch):
    """Keep legacy environment-source coverage explicit and non-packaged."""
    monkeypatch.delenv("ASKREX_PACKAGED", raising=False)
    monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", "1")
    # The US-053 assertions cover plaintext environment behavior.  An
    # ambient OS vault must not replace those fixture values on machines
    # where an operator has configured credentials.
    monkeypatch.setattr(CredentialManager, "_get_vault", lambda _manager: None)
    original = None
    try:
        from rex.credentials import _credential_manager as _orig

        original = _orig
    except ImportError:
        pass
    yield
    set_credential_manager(original)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Criterion: secrets loaded from environment
# ---------------------------------------------------------------------------


def test_secrets_loaded_from_env_var(monkeypatch):
    """CredentialManager reads secrets from environment variables."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-abc123")
    mgr = CredentialManager(config_path="/nonexistent/path.json")
    token = mgr.get_token("openai")
    assert token == "sk-test-abc123"


def test_secrets_loaded_with_rex_prefix(monkeypatch):
    """CredentialManager reads REX_-prefixed env vars."""
    monkeypatch.setenv("REX_BRAVE_API_KEY", "brave-key-xyz")
    mgr = CredentialManager(config_path="/nonexistent/path.json")
    token = mgr.get_token("brave")
    assert token == "brave-key-xyz"


def test_multiple_secrets_loaded_from_env(monkeypatch):
    """Multiple secrets can all be loaded from environment simultaneously."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    monkeypatch.setenv("REX_HA_TOKEN", "ha-token")

    mgr = CredentialManager(config_path="/nonexistent/path.json")

    assert mgr.get_token("openai") == "sk-openai"
    assert mgr.get_token("anthropic") == "sk-anthropic"
    assert mgr.get_token("home_assistant") == "ha-token"


def test_credential_source_is_env(monkeypatch):
    """Credentials loaded from env have source='env'."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-env")
    mgr = CredentialManager(config_path="/nonexistent/path.json")
    cred = mgr.get_credential("openai")
    assert cred is not None
    assert cred.source == "env"


def test_global_credential_manager_uses_env(monkeypatch):
    """The global CredentialManager singleton also reads from environment."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-global-test")
    mgr = CredentialManager(config_path="/nonexistent/path.json")
    set_credential_manager(mgr)
    token = get_credential_manager().get_token("openai")
    assert token == "sk-global-test"


# ---------------------------------------------------------------------------
# Criterion: secrets not stored in repo
# ---------------------------------------------------------------------------


def test_env_file_is_gitignored():
    """.env must be listed in .gitignore so secrets are never committed."""
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.exists(), ".gitignore not found in repo root"
    content = gitignore.read_text(encoding="utf-8")
    assert ".env" in content, ".env must appear in .gitignore"


def test_env_file_not_tracked_by_git():
    """.env must not be a tracked file in the git repository."""
    result = subprocess.run(
        ["git", "ls-files", ".env"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "", ".env must not be tracked by git"


def test_env_example_contains_no_real_secrets():
    """.env.example must not contain real API keys."""
    env_example = REPO_ROOT / ".env.example"
    if not env_example.exists():
        pytest.skip(".env.example not found")

    content = env_example.read_text(encoding="utf-8")

    # Real secrets look like sk-... or long hex strings set to actual values
    # The example should only have placeholder values (empty or YOUR_KEY_HERE etc.)
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        _, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        # Real OpenAI keys start with sk- and are long
        assert not (
            value.startswith("sk-") and len(value) > 20
        ), f".env.example contains what looks like a real API key: {line}"


def test_credentials_json_not_tracked_by_git():
    """config/credentials.json must not be tracked by git."""
    result = subprocess.run(
        ["git", "ls-files", "config/credentials.json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "", "config/credentials.json must not be tracked by git"


# ---------------------------------------------------------------------------
# Criterion: missing secrets detected
# ---------------------------------------------------------------------------


def test_missing_secret_returns_none(monkeypatch):
    """get_token() returns None when the env var is not set."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("REX_OPENAI_API_KEY", raising=False)
    mgr = CredentialManager(config_path="/nonexistent/path.json")
    token = mgr.get_token("openai")
    assert token is None


def test_has_token_false_when_missing(monkeypatch):
    """has_token() returns False when the credential is not configured."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("REX_OPENAI_API_KEY", raising=False)
    mgr = CredentialManager(config_path="/nonexistent/path.json")
    assert mgr.has_token("openai") is False


def test_has_token_true_when_present(monkeypatch):
    """has_token() returns True when the credential is configured."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-present")
    mgr = CredentialManager(config_path="/nonexistent/path.json")
    assert mgr.has_token("openai") is True


def test_list_services_excludes_missing(monkeypatch):
    """list_services() only includes services that have credentials."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("REX_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("REX_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("BRAVE_API_KEY", "brave-present")

    mgr = CredentialManager(config_path="/nonexistent/path.json")
    services = mgr.list_services()

    assert "brave" in services
    assert "openai" not in services
    assert "anthropic" not in services


def test_missing_secret_token_masked_as_empty():
    """mask_token on None or empty returns '[empty]' indicating a missing secret."""
    assert mask_token(None) == "[empty]"
    assert mask_token("") == "[empty]"


def test_get_credential_returns_none_for_unknown_service():
    """get_credential() returns None for an unregistered service name."""
    mgr = CredentialManager(config_path="/nonexistent/path.json")
    assert mgr.get_credential("nonexistent_service_xyz") is None


def test_credential_info_returns_none_for_missing():
    """get_credential_info() returns None when secret is not configured."""
    mgr = CredentialManager(config_path="/nonexistent/path.json")
    info = mgr.get_credential_info("nonexistent_service_xyz")
    assert info is None
