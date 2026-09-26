"""Configuration and secret validation tests.

Matrix rows: FND-003, FND-005, FND-006, FND-007.
"""

from __future__ import annotations

import pytest


class TestMobileApiConfigDefaults:
    def test_safe_defaults(self) -> None:
        from rex.config import MobileApiConfig

        config = MobileApiConfig()
        assert config.enabled is False
        assert config.host == "127.0.0.1"
        assert config.port == 8765
        assert config.allowed_origins == []
        assert config.require_tls is False
        assert config.api_version == "1.0"
        assert config.access_token_ttl_seconds == 900
        assert config.refresh_token_ttl_days == 30
        assert config.max_json_bytes == 1_048_576

    def test_parsed_from_json_group(self) -> None:
        from rex.config import _parse_mobile_api_config

        config = _parse_mobile_api_config(
            {"port": 9000, "access_token_ttl_seconds": 300, "unknown_key": 1}
        )
        assert config.port == 9000
        assert config.access_token_ttl_seconds == 300

    def test_none_group_yields_defaults(self) -> None:
        from rex.config import _parse_mobile_api_config

        assert _parse_mobile_api_config(None).port == 8765


class TestMobileApiConfigValidation:
    @pytest.mark.parametrize("port", [0, -1, 65536])
    def test_invalid_port_rejected(self, port: int) -> None:
        from rex.config import MobileApiConfig

        with pytest.raises(ValueError):
            MobileApiConfig(port=port)

    @pytest.mark.parametrize(
        "field",
        [
            "access_token_ttl_seconds",
            "refresh_token_ttl_days",
            "max_json_bytes",
            "max_audio_bytes",
            "max_audio_seconds",
        ],
    )
    def test_non_positive_limits_rejected(self, field: str) -> None:
        from rex.config import MobileApiConfig

        with pytest.raises(ValueError):
            MobileApiConfig(**{field: 0})

    @pytest.mark.parametrize("value", ["", "lots", "per minute", "10 per parsec"])
    def test_invalid_rate_limit_rejected(self, value: str) -> None:
        from rex.config import MobileApiConfig

        with pytest.raises(ValueError):
            MobileApiConfig(rate_limit_default=value)

    def test_valid_rate_limits_accepted(self) -> None:
        from rex.config import MobileApiConfig

        config = MobileApiConfig(rate_limit_default="120 per hour", rate_limit_login="5/minute")
        assert config.rate_limit_default == "120 per hour"

    def test_wildcard_origin_rejected(self) -> None:
        from rex.config import MobileApiConfig

        with pytest.raises(ValueError, match="deny-by-default"):
            MobileApiConfig(allowed_origins=["*"])

    def test_empty_host_rejected(self) -> None:
        from rex.config import MobileApiConfig

        with pytest.raises(ValueError):
            MobileApiConfig(host="  ")

    def test_invalid_group_raises_configuration_error(self) -> None:
        from rex.assistant_errors import ConfigurationError
        from rex.config import _parse_mobile_api_config

        with pytest.raises(ConfigurationError):
            _parse_mobile_api_config({"port": 999999})
        with pytest.raises(ConfigurationError):
            _parse_mobile_api_config("not-a-dict")


class TestMobileApiConfigSerialization:
    """mobile_api is canonical nested config and must survive serialization."""

    def _build_config(self):
        from rex.config import build_app_config

        return build_app_config(
            {
                "mobile_api": {
                    "host": "192.168.1.50",
                    "port": 9001,
                    "access_token_ttl_seconds": 600,
                    "allowed_origins": ["https://app.askrex.local"],
                }
            }
        )

    def test_to_dict_includes_mobile_api(self) -> None:
        from rex.config import AppConfig

        raw = AppConfig().to_dict()
        assert "mobile_api" in raw
        assert raw["mobile_api"]["host"] == "127.0.0.1"
        assert raw["mobile_api"]["port"] == 8765

    def test_loaded_values_survive_serialization(self) -> None:
        raw = self._build_config().to_dict()
        group = raw["mobile_api"]
        assert group["host"] == "192.168.1.50"
        assert group["port"] == 9001
        assert group["access_token_ttl_seconds"] == 600
        assert group["allowed_origins"] == ["https://app.askrex.local"]

    def test_serialized_group_is_json_safe_and_secret_free(self) -> None:
        import json

        raw = self._build_config().to_dict()
        text = json.dumps(raw["mobile_api"])
        # The JWT secret lives in .env only and has no config field at all.
        assert "secret" not in text.lower()
        assert "REX_JWT" not in text
        assert set(raw["mobile_api"].keys()) == {
            "enabled",
            "host",
            "port",
            "advertised_host",
            "advertised_port",
            "allowed_origins",
            "require_tls",
            "api_version",
            "access_token_ttl_seconds",
            "refresh_token_ttl_days",
            "max_json_bytes",
            "max_audio_bytes",
            "max_audio_seconds",
            "rate_limit_default",
            "rate_limit_login",
            "rate_limit_refresh",
            "rate_limit_chat",
            "rate_limit_voice",
            "idempotency_retention_hours",
            "max_attachment_bytes",
            "max_attachments_per_conversation",
            "attachment_retention_seconds",
        }

    def test_show_config_output_includes_mobile_api(self, capsys) -> None:
        """`rex-config show` prints the mobile_api group."""
        import json

        from rex.config import show_config

        show_config(self._build_config())
        printed = json.loads(capsys.readouterr().out)
        assert printed["mobile_api"]["port"] == 9001
        assert printed["mobile_api"]["host"] == "192.168.1.50"


class TestJwtSecret:
    def test_missing_secret_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from rex.mobile_api.auth import MobileAuthConfigurationError, load_jwt_secret

        monkeypatch.delenv("REX_JWT_SECRET", raising=False)
        with pytest.raises(MobileAuthConfigurationError, match="REX_JWT_SECRET"):
            load_jwt_secret()

    def test_weak_secret_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from rex.mobile_api.auth import MobileAuthConfigurationError, load_jwt_secret

        monkeypatch.setenv("REX_JWT_SECRET", "short-secret")
        with pytest.raises(MobileAuthConfigurationError) as excinfo:
            load_jwt_secret()
        # The error must not echo the configured secret value.
        assert "short-secret" not in str(excinfo.value)

    def test_strong_secret_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from rex.mobile_api.auth import load_jwt_secret

        secret = "a" * 64
        monkeypatch.setenv("REX_JWT_SECRET", secret)
        assert load_jwt_secret() == secret

    def test_app_factory_fails_closed_without_secret(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rex.mobile_api.app import create_mobile_app
        from rex.mobile_api.auth import MobileAuthConfigurationError

        monkeypatch.setenv("REX_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.delenv("REX_JWT_SECRET", raising=False)
        with pytest.raises(MobileAuthConfigurationError):
            create_mobile_app()
