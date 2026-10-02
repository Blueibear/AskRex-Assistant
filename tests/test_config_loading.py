"""Tests for US-012: Validate configuration loading.

Acceptance criteria:
  - config loads from config file
  - environment overrides supported (secrets from env)
  - missing config handled safely
  - Typecheck passes
"""

from __future__ import annotations

import json
from pathlib import Path

from rex.config import AppConfig, _migrate_wake_word_section, build_app_config, load_config
from rex.config_manager import DEFAULT_CONFIG
from rex.config_manager import load_config as load_json_config

# ---------------------------------------------------------------------------
# Criterion 1: config loads from config file
# ---------------------------------------------------------------------------


class TestConfigLoadsFromFile:
    """build_app_config / load_config must read values from the JSON config."""

    def test_build_app_config_reads_llm_provider(self):
        """build_app_config picks up models.llm_provider from JSON config."""
        json_cfg = {
            "models": {
                "llm_provider": "transformers",
                "llm_model": "sshleifer/tiny-gpt2",
            }
        }
        cfg = build_app_config(json_cfg)
        assert cfg.llm_provider == "transformers"

    def test_build_app_config_reads_wakeword(self):
        """build_app_config picks up wake_word.wakeword from JSON config."""
        json_cfg = {
            "wake_word": {"wakeword": "hello-rex"},
            "models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"},
        }
        cfg = build_app_config(json_cfg)
        assert cfg.wakeword == "hello-rex"

    def test_build_app_config_reads_canonical_wakeword(self):
        """build_app_config picks up canonical wakeword.wakeword from JSON config."""
        json_cfg = {
            "wakeword": {"wakeword": "hello-modern"},
            "models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"},
        }
        cfg = build_app_config(json_cfg)
        assert cfg.wakeword == "hello-modern"

    def test_wake_word_migration_handles_missing_key(self):
        """Migration is a no-op when neither wake_word nor wakeword is present."""
        json_cfg = {"models": {"llm_provider": "transformers"}}
        migrated = _migrate_wake_word_section(json_cfg)
        assert migrated == {"models": {"llm_provider": "transformers"}}

    def test_wake_word_migration_merges_legacy_without_overwriting_canonical(self):
        """Legacy wake_word fills missing canonical values but preserves wakeword values."""
        json_cfg = {
            "wakeword": {"wakeword": "modern"},
            "wake_word": {"wakeword": "legacy", "threshold": 0.25},
        }
        migrated = _migrate_wake_word_section(json_cfg)
        assert "wake_word" not in migrated
        assert migrated["wakeword"] == {"wakeword": "modern", "threshold": 0.25}

    def test_wake_word_migration_is_idempotent(self):
        """Migration can run repeatedly without raising or changing the migrated result."""
        json_cfg = {"wake_word": {"wakeword": "legacy", "threshold": 0.25}}
        first = _migrate_wake_word_section(json_cfg)
        second = _migrate_wake_word_section(first)
        assert second == {"wakeword": {"wakeword": "legacy", "threshold": 0.25}}

    def test_build_app_config_reads_rate_limit(self):
        """build_app_config picks up api.rate_limit from JSON config."""
        json_cfg = {
            "api": {"rate_limit": "10/minute"},
            "models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"},
        }
        cfg = build_app_config(json_cfg)
        assert cfg.rate_limit == "10/minute"

    def test_build_app_config_reads_memory_max_turns(self):
        """build_app_config picks up runtime.memory_max_turns from JSON config."""
        json_cfg = {
            "runtime": {"memory_max_turns": 25},
            "models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"},
        }
        cfg = build_app_config(json_cfg)
        assert cfg.memory_max_turns == 25

    def test_build_app_config_reads_session_ttl_hours(self):
        """build_app_config picks up runtime.session_ttl_hours from JSON config."""
        json_cfg = {
            "runtime": {"session_ttl_hours": 12},
            "models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"},
        }
        cfg = build_app_config(json_cfg)
        assert cfg.session_ttl_hours == 12

    def test_load_json_config_from_file(self, tmp_path: Path):
        """load_config (config_manager) reads from a custom JSON file path."""
        config_file = tmp_path / "config" / "rex_config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            json.dumps(
                {
                    "runtime": {"memory_max_turns": 77},
                    "models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"},
                }
            )
        )
        data = load_json_config(path=str(config_file))
        assert data["runtime"]["memory_max_turns"] == 77

    def test_load_config_with_json_config_kwarg(self):
        """load_config (rex.config) accepts a json_config dict and builds AppConfig."""
        json_cfg = {
            "models": {
                "llm_provider": "transformers",
                "llm_model": "sshleifer/tiny-gpt2",
                "llm_max_tokens": 64,
            }
        }
        cfg = load_config(json_config=json_cfg, reload=True)
        assert isinstance(cfg, AppConfig)
        assert cfg.llm_max_tokens == 64

    def test_load_config_returns_app_config_type(self):
        """load_config always returns an AppConfig instance."""
        json_cfg = {"models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"}}
        cfg = load_config(json_config=json_cfg, reload=True)
        assert isinstance(cfg, AppConfig)


# ---------------------------------------------------------------------------
# Criterion 2: environment overrides supported (secrets from env)
# ---------------------------------------------------------------------------


class TestEnvironmentOverrides:
    """Secrets must be read from environment variables."""

    def test_openai_api_key_from_env(self, monkeypatch):
        """build_app_config reads OPENAI_API_KEY from environment."""
        monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", "1")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai-key")
        json_cfg = {
            "models": {
                "llm_provider": "transformers",
                "llm_model": "sshleifer/tiny-gpt2",
                "llm_max_tokens": 10,
            }
        }
        cfg = build_app_config(json_cfg)
        assert cfg.openai_api_key == "sk-test-openai-key"  # pragma: allowlist secret

    def test_openai_api_key_absent_when_not_set(self, monkeypatch):
        """build_app_config returns None for OPENAI_API_KEY when not set."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        json_cfg = {"models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"}}
        cfg = build_app_config(json_cfg)
        assert cfg.openai_api_key is None

    def test_brave_api_key_from_env(self, monkeypatch):
        """build_app_config reads BRAVE_API_KEY from environment."""
        monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", "1")
        monkeypatch.setenv("BRAVE_API_KEY", "brave-test-key")
        json_cfg = {"models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"}}
        cfg = build_app_config(json_cfg)
        assert cfg.brave_api_key == "brave-test-key"  # pragma: allowlist secret

    def test_ha_token_from_env(self, monkeypatch):
        """build_app_config reads HA_TOKEN from environment."""
        monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", "1")
        monkeypatch.setenv("HA_TOKEN", "ha-long-lived-token")
        json_cfg = {"models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"}}
        cfg = build_app_config(json_cfg)
        assert cfg.ha_token == "ha-long-lived-token"

    def test_speak_api_key_from_env(self, monkeypatch):
        """build_app_config reads REX_SPEAK_API_KEY from environment."""
        monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", "1")
        monkeypatch.setenv("REX_SPEAK_API_KEY", "speak-secret")
        json_cfg = {"models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"}}
        cfg = build_app_config(json_cfg)
        assert cfg.speak_api_key == "speak-secret"  # pragma: allowlist secret

    def test_json_config_takes_precedence_for_non_secrets(self):
        """Non-secret settings come from JSON config, not from env legacy vars."""
        json_cfg = {
            "models": {
                "llm_provider": "transformers",
                "llm_model": "sshleifer/tiny-gpt2",
                "llm_max_tokens": 99,
            }
        }
        cfg = build_app_config(json_cfg)
        assert cfg.llm_max_tokens == 99


# ---------------------------------------------------------------------------
# Criterion 3: missing config handled safely
# ---------------------------------------------------------------------------


class TestMissingConfigHandledSafely:
    """Missing or broken config files must not crash the loader."""

    def test_missing_config_file_creates_defaults(self, tmp_path: Path):
        """load_json_config creates default config when file is absent."""
        config_path = tmp_path / "config" / "rex_config.json"
        assert not config_path.exists()

        data = load_json_config(path=str(config_path))

        # File should be created
        assert config_path.exists()
        # Data should contain all default top-level sections
        for section in ("models", "runtime", "wakeword", "audio"):
            assert section in data, f"Missing default section: {section}"

    def test_missing_config_returns_dict(self, tmp_path: Path):
        """load_json_config returns a dict even when file was absent."""
        config_path = tmp_path / "subdir" / "rex_config.json"
        data = load_json_config(path=str(config_path))
        assert isinstance(data, dict)

    def test_invalid_json_config_falls_back_to_defaults(self, tmp_path: Path):
        """load_json_config handles corrupt JSON by backing up and returning defaults."""
        config_path = tmp_path / "config" / "rex_config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("{invalid json!!!}")

        data = load_json_config(path=str(config_path))

        # Must not crash and must return a usable dict
        assert isinstance(data, dict)
        assert "models" in data

    def test_build_app_config_with_empty_json(self):
        """build_app_config with an empty dict uses all defaults."""
        cfg = build_app_config({})
        assert isinstance(cfg, AppConfig)
        assert cfg.llm_provider == "transformers"
        assert cfg.wakeword == "hey_rex"
        assert cfg.memory_max_turns == 50
        assert cfg.session_ttl_hours == 8

    def test_build_app_config_with_partial_json(self):
        """build_app_config with partial config applies defaults for missing keys."""
        json_cfg = {"models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"}}
        cfg = build_app_config(json_cfg)
        # Defaults must be applied for unspecified keys
        assert cfg.sample_rate == 16000
        assert cfg.rate_limit == "30/minute"

    def test_load_config_with_minimal_json_does_not_raise(self):
        """load_config succeeds with a minimal valid json_config."""
        json_cfg = {"models": {"llm_provider": "transformers", "llm_model": "sshleifer/tiny-gpt2"}}
        cfg = load_config(json_config=json_cfg, reload=True)
        assert cfg is not None

    def test_default_config_has_required_keys(self):
        """DEFAULT_CONFIG contains all required top-level sections."""
        for section in ("models", "runtime", "wakeword", "audio", "api"):
            assert section in DEFAULT_CONFIG, f"DEFAULT_CONFIG missing section: {section}"


def test_build_app_config_reads_warm_runtime_policy() -> None:
    cfg = build_app_config(
        {
            "runtime": {
                "warm_runtime_max_cost_mb": 4096,
                "warm_runtime_idle_timeout_s": 300,
            },
            "models": {
                "llm_provider": "transformers",
                "llm_model": "sshleifer/tiny-gpt2",
            },
        }
    )

    assert cfg.warm_runtime_max_cost_mb == 4096.0
    assert cfg.warm_runtime_idle_timeout_s == 300.0


def test_warm_runtime_policy_rejects_negative_limits() -> None:
    import pytest

    with pytest.raises(ValueError, match="warm runtime"):
        AppConfig(warm_runtime_max_cost_mb=-1)
    with pytest.raises(ValueError, match="warm runtime"):
        AppConfig(warm_runtime_idle_timeout_s=-1)
