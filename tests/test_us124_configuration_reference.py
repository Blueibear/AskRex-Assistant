"""Tests for US-124: Environment variable and configuration reference.

Acceptance criteria:
- docs/configuration.md exists and lists every environment variable with:
  name, description, default, required/optional
- document organized into logical sections (server, database, LLM providers,
  integrations, logging)
- document consistent with .env.example (no variables in one but not the other)
- Typecheck passes
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CONFIG_MD = REPO_ROOT / "docs" / "configuration.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vars_in_env_example() -> set[str]:
    """Return all variable names defined in .env.example."""
    pattern = re.compile(r"^([A-Z][A-Z0-9_]*)=", re.MULTILINE)
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return {m.group(1) for m in pattern.finditer(text)}


def _vars_in_config_md() -> set[str]:
    """Return all variable names backtick-quoted in docs/configuration.md."""
    pattern = re.compile(r"`([A-Z][A-Z0-9_]*)`")
    text = CONFIG_MD.read_text(encoding="utf-8")
    return {m.group(1) for m in pattern.finditer(text)}


# ---------------------------------------------------------------------------
# Document existence
# ---------------------------------------------------------------------------


class TestConfigurationMdExists:
    def test_exists(self) -> None:
        assert CONFIG_MD.exists(), "docs/configuration.md must exist"

    def test_nonempty(self) -> None:
        assert len(CONFIG_MD.read_text().strip()) > 200


# ---------------------------------------------------------------------------
# Required sections
# ---------------------------------------------------------------------------


class TestRequiredSections:
    def _content(self) -> str:
        return CONFIG_MD.read_text().lower()

    def test_has_server_section(self) -> None:
        assert "server" in self._content()

    def test_has_database_section(self) -> None:
        assert "database" in self._content()

    def test_has_llm_section(self) -> None:
        c = self._content()
        assert "llm" in c or "language model" in c or "openai" in c

    def test_has_integrations_section(self) -> None:
        c = self._content()
        assert "integration" in c or "home assistant" in c or "search" in c

    def test_has_logging_section(self) -> None:
        assert "logging" in self._content()


# ---------------------------------------------------------------------------
# Each variable has name, description, default, required/optional
# ---------------------------------------------------------------------------


class TestVariableDocumentation:
    def _content(self) -> str:
        return CONFIG_MD.read_text()

    def test_default_column_present(self) -> None:
        """Tables must include a Default column."""
        assert "Default" in self._content()

    def test_required_column_present(self) -> None:
        """Tables must document which variables are required."""
        c = self._content()
        assert "Required" in c or "required" in c

    def test_description_column_present(self) -> None:
        """Tables must include a Description column."""
        assert "Description" in self._content()


# ---------------------------------------------------------------------------
# Consistency with .env.example
# ---------------------------------------------------------------------------


class TestConsistencyWithEnvExample:
    def test_env_example_exists(self) -> None:
        assert ENV_EXAMPLE.exists(), ".env.example must exist"

    def test_all_env_example_vars_in_config_md(self) -> None:
        """Every variable in .env.example must be documented in configuration.md."""
        env_vars = _vars_in_env_example()
        config_vars = _vars_in_config_md()

        missing = env_vars - config_vars
        assert not missing, (
            f"Variables in .env.example but not in docs/configuration.md: " f"{sorted(missing)}"
        )

    def test_config_md_vars_in_env_example(self) -> None:
        """Every variable documented in configuration.md must be in .env.example."""
        # Only check variables that look like proper env var names (all-caps, underscore).
        # Exclude variables that are development-only or auto-set (e.g. REX_TESTING).
        env_vars = _vars_in_env_example()
        config_vars = _vars_in_config_md()

        # Allow a small allowlist of variables that are internal/derived and
        # don't belong in .env.example.
        ALLOWLIST = {
            "REX_TESTING",  # dev-only, set by test suite
            "REX_ALLOWED_ORIGINS",  # noted in deployment guide, not in .env.example
            # Log level enum values used as examples in the Description column
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
            # Default value of REX_AGENT_TOKEN_ENV (not itself a configurable var)
            "REX_AGENT_TOKEN",
        }

        missing = config_vars - env_vars - ALLOWLIST
        assert not missing, (
            f"Variables documented in configuration.md but missing from .env.example: "
            f"{sorted(missing)}"
        )

    def test_new_db_vars_in_both(self) -> None:
        """Database pool variables added in US-114/116 are in both documents."""
        for var in [
            "DB_POOL_MIN_SIZE",
            "DB_POOL_MAX_SIZE",
            "DB_POOL_ACQUIRE_TIMEOUT",
            "DB_POOL_IDLE_TIMEOUT",
            "DB_QUERY_TIMEOUT",
            "SKIP_MIGRATION_CHECK",
            "API_RATE_LIMIT",
        ]:
            env_vars = _vars_in_env_example()
            config_vars = _vars_in_config_md()
            assert var in env_vars, f"{var} missing from .env.example"
            assert var in config_vars, f"{var} missing from docs/configuration.md"


# ---------------------------------------------------------------------------
# Spot-checks for key variables
# ---------------------------------------------------------------------------


class TestKeyVariablesPresent:
    def _content(self) -> str:
        return CONFIG_MD.read_text()

    def test_openai_api_key_documented(self) -> None:
        assert "OPENAI_API_KEY" in self._content()

    def test_rex_proxy_token_documented(self) -> None:
        assert "REX_PROXY_TOKEN" in self._content()

    def test_log_level_documented(self) -> None:
        assert "LOG_LEVEL" in self._content()

    def test_db_query_timeout_documented(self) -> None:
        assert "DB_QUERY_TIMEOUT" in self._content()

    def test_skip_migration_check_documented(self) -> None:
        assert "SKIP_MIGRATION_CHECK" in self._content()

    def test_api_rate_limit_documented(self) -> None:
        assert "API_RATE_LIMIT" in self._content()
