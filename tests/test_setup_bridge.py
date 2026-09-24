"""Behavior tests for the canonical Electron setup bridge."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from bridge import rex_setup_bridge
from rex.assistant_errors import AudioDeviceError


def _install_setup_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], list[str]]:
    captured: dict[str, Any] = {}
    bootstrapped: list[str] = []
    monkeypatch.setattr(rex_setup_bridge, "_read_user_count", lambda: 0)

    def fake_create_user(username: str, password: str) -> dict[str, str]:
        assert username == "james"
        assert password == "securepass1"  # pragma: allowlist secret
        return {"id": "james"}

    def fake_persist(
        values: dict[str, str], *, config_path=None, update_config=None
    ) -> dict[str, str]:
        captured["values"] = dict(values)
        config: dict[str, Any] = {}
        if update_config is not None:
            update_config(config)
        captured["config"] = config
        return {}

    monkeypatch.setattr("rex.auth.create_user", fake_create_user)
    monkeypatch.setattr("rex.permissions.bootstrap_admin_if_first_user", bootstrapped.append)
    monkeypatch.setattr("rex.credential_persistence.persist_household_secrets", fake_persist)
    return captured, bootstrapped


def test_deferred_home_assistant_is_omitted_from_persistence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured, bootstrapped = _install_setup_fakes(monkeypatch)

    rex_setup_bridge._handle_complete(
        {
            "username": "james",
            "password": "securepass1",  # pragma: allowlist secret
            "llm_provider": "local",
            "tts_provider": "none",
            "ha_base_url": "http://ha.local:8123",
            "ha_token": "must-not-persist",  # pragma: allowlist secret
            "defer_home_assistant": True,
        }
    )

    assert json.loads(capsys.readouterr().out) == {"ok": True, "user_id": "james"}
    assert captured["values"] == {}
    assert "home_assistant" not in captured["config"]
    assert bootstrapped == ["james"]


def test_string_false_does_not_defer_home_assistant(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured, _ = _install_setup_fakes(monkeypatch)

    rex_setup_bridge._handle_complete(
        {
            "username": "james",
            "password": "securepass1",  # pragma: allowlist secret
            "llm_provider": "local",
            "tts_provider": "none",
            "ha_base_url": "http://ha.local:8123",
            "ha_token": "test-token",  # pragma: allowlist secret
            "defer_home_assistant": "false",
        }
    )

    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert captured["values"] == {"HA_TOKEN": "test-token"}  # pragma: allowlist secret
    assert captured["config"]["home_assistant"]["base_url"] == "http://ha.local:8123"


def test_household_voice_choices_persist_to_canonical_runtime_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured, _ = _install_setup_fakes(monkeypatch)

    rex_setup_bridge._handle_complete(
        {
            "username": "james",
            "password": "securepass1",  # pragma: allowlist secret
            "llm_provider": "local",
            "tts_provider": "edge",
            "tts_voice_id": "en-US-AriaNeural",
            "microphone_device_index": 2,
            "speaker_device_index": 4,
            "wake_word_id": "hey_jarvis",
            "local_device_id": "local_voice",
            "room_name": "Office",
            "background_voice_enabled": True,
            "ha_base_url": "",
            "ha_token": "",
        }
    )

    assert json.loads(capsys.readouterr().out)["ok"] is True
    config = captured["config"]
    assert config["models"]["tts_provider"] == "edge"
    assert config["models"]["tts_voice"] == "en-US-AriaNeural"
    assert config["audio"]["input_device_index"] == 2
    assert config["audio"]["output_device_index"] == 4
    assert config["wakeword"]["backend"] == "openwakeword"
    assert config["wakeword"]["wakeword"] == "hey jarvis"
    assert config["wakeword"]["keyword"] == "hey jarvis"
    assert config["wakeword"]["model_path"] is None
    assert config["wakeword"]["embedding_path"] is None
    assert config["device_room_map"] == {"local_voice": "Office"}
    assert config["runtime"]["active_user"] == "james"
    assert config["runtime"]["background_voice_enabled"] is True


def test_microphone_picker_canonical_index_is_persisted_without_name_remapping() -> None:
    choices = rex_setup_bridge._parse_setup_choices({"microphone_device_index": 17})
    config: dict[str, Any] = {}

    rex_setup_bridge._apply_voice_config(config, choices)

    assert config["audio"]["input_device_index"] == 17


def test_speaker_picker_canonical_index_is_persisted_without_name_remapping() -> None:
    """Mirrors the microphone canonical-index test for speakers (TEST-003):
    the exact PortAudio output index a user selected from the grouped picker
    must reach config unchanged, never a re-derived/name-matched index."""
    choices = rex_setup_bridge._parse_setup_choices({"speaker_device_index": 23})
    config: dict[str, Any] = {}

    rex_setup_bridge._apply_voice_config(config, choices)

    assert config["audio"]["output_device_index"] == 23


def test_lmstudio_provider_persists_as_openai_runtime_with_base_url_and_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured, _ = _install_setup_fakes(monkeypatch)

    rex_setup_bridge._handle_complete(
        {
            "username": "james",
            "password": "securepass1",  # pragma: allowlist secret
            "llm_provider": "lmstudio",
            "openai_base_url": "http://127.0.0.1:1234/v1",
            "openai_model": "openai/gpt-oss-20b",
            "tts_provider": "none",
            "ha_base_url": "",
            "ha_token": "",
        }
    )

    assert json.loads(capsys.readouterr().out)["ok"] is True
    config = captured["config"]
    assert config["models"]["llm_provider"] == "openai"
    assert config["openai"]["base_url"] == "http://127.0.0.1:1234/v1"
    assert config["openai"]["model"] == "openai/gpt-oss-20b"


def test_lmstudio_provider_without_base_url_fails_validation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_setup_fakes(monkeypatch)

    rex_setup_bridge._handle_complete(
        {
            "username": "james",
            "password": "securepass1",  # pragma: allowlist secret
            "llm_provider": "lmstudio",
            "openai_base_url": "",
            "openai_model": "openai/gpt-oss-20b",
            "tts_provider": "none",
            "ha_base_url": "",
            "ha_token": "",
        }
    )

    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is False
    assert "LM Studio" in response["error"]


def test_lmstudio_provider_without_model_fails_validation_instead_of_defaulting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """TEST-001: an untouched/missing LM Studio model must never silently
    persist the unrelated official-OpenAI default `gpt-4o`."""
    captured, _ = _install_setup_fakes(monkeypatch)

    rex_setup_bridge._handle_complete(
        {
            "username": "james",
            "password": "securepass1",  # pragma: allowlist secret
            "llm_provider": "lmstudio",
            "openai_base_url": "http://127.0.0.1:1234/v1",
            "openai_model": "",
            "tts_provider": "none",
            "ha_base_url": "",
            "ha_token": "",
        }
    )

    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is False
    assert "LM Studio" in response["error"]
    assert "model" in response["error"].lower()
    assert "config" not in captured


def test_openai_provider_still_defaults_model_without_lmstudio_override(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cloud OpenAI setup behavior is unchanged: it keeps the gpt-4o default."""
    captured, _ = _install_setup_fakes(monkeypatch)

    rex_setup_bridge._handle_complete(
        {
            "username": "james",
            "password": "securepass1",  # pragma: allowlist secret
            "llm_provider": "openai",
            "llm_api_key": "sk-test",  # pragma: allowlist secret
            "tts_provider": "none",
            "ha_base_url": "",
            "ha_token": "",
        }
    )

    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert captured["config"]["openai"]["model"] == "gpt-4o"


def test_voice_config_replaces_stale_keyword_and_paths_for_builtin_selection() -> None:
    config: dict[str, Any] = {
        "wakeword": {
            "backend": "custom_embedding",
            "wakeword": "rex",
            "keyword": "rex",
            "model_path": "stale.onnx",
            "embedding_path": "stale.pt",
        }
    }
    choices = rex_setup_bridge._parse_setup_choices({"wake_word_id": "hey_jarvis"})

    rex_setup_bridge._apply_voice_config(config, choices)

    assert config["wakeword"] == {
        "backend": "openwakeword",
        "wakeword": "hey jarvis",
        "keyword": "hey jarvis",
        "model_path": None,
        "embedding_path": None,
    }


def test_builtin_wakeword_persistence_does_not_require_custom_training_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "rex.wakeword.trainer", None)
    config: dict[str, Any] = {"wakeword": {"keyword": "rex"}}
    choices = rex_setup_bridge._parse_setup_choices({"wake_word_id": "hey_jarvis"})

    rex_setup_bridge._apply_voice_config(config, choices)

    assert config["wakeword"]["backend"] == "openwakeword"
    assert config["wakeword"]["keyword"] == "hey jarvis"


def test_unresolved_custom_wakeword_fails_explicitly_when_training_inventory_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "rex.wakeword.trainer", None)

    with pytest.raises(ValueError, match="no longer available"):
        rex_setup_bridge._resolve_setup_wakeword_config("my_custom_wake_word")


def test_voice_config_uses_trained_embedding_metadata_for_custom_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "rex.wakeword.trainer.list_custom_wake_words",
        lambda: [
            {
                "id": "computer",
                "name": "Computer",
                "engine": "custom_embedding",
                "model_path": "C:/safe/wake_words/computer/embedding.pt",
            }
        ],
    )
    config: dict[str, Any] = {"wakeword": {"keyword": "rex"}}
    choices = rex_setup_bridge._parse_setup_choices({"wake_word_id": "computer"})

    rex_setup_bridge._apply_voice_config(config, choices)

    assert config["wakeword"] == {
        "backend": "custom_embedding",
        "wakeword": "Computer",
        "keyword": "Computer",
        "model_path": None,
        "embedding_path": "C:/safe/wake_words/computer/embedding.pt",
    }


def test_background_voice_defaults_off_in_setup_runtime_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured, _ = _install_setup_fakes(monkeypatch)

    rex_setup_bridge._handle_complete(
        {
            "username": "james",
            "password": "securepass1",  # pragma: allowlist secret
            "llm_provider": "local",
            "tts_provider": "none",
            "ha_base_url": "",
            "ha_token": "",
        }
    )

    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert captured["config"]["runtime"]["background_voice_enabled"] is False

    repo_root = Path(__file__).resolve().parents[1]
    schema = json.loads((repo_root / "config" / "rex_config.schema.json").read_text())
    background_voice_schema = schema["properties"]["runtime"]["properties"][
        "background_voice_enabled"
    ]
    assert background_voice_schema["type"] == "boolean"
    assert background_voice_schema["default"] is False


def test_existing_legacy_profile_is_not_routed_to_first_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, _query: str):
            return self

        def fetchone(self) -> tuple[int]:
            return (0,)

    monkeypatch.setattr("rex.auth._open_db", lambda: _Connection())
    monkeypatch.setattr("rex.identity.list_known_users", lambda: [{"id": "james"}])

    assert rex_setup_bridge._read_user_count() == 1


def test_audio_devices_returns_sanitized_portaudio_inventory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "rex.audio_config.list_devices",
        lambda: [
            {
                "name": "USB Microphone",
                "hostapi": 2,
                "max_input_channels": 1,
                "max_output_channels": 0,
                "default_samplerate": 48000.0,
            },
            {
                "name": "Desk Speakers",
                "hostapi": 2,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            },
        ],
    )
    monkeypatch.setattr(
        "rex.audio_config.build_microphone_picker_devices",
        lambda *, devices: [
            {
                "index": 0,
                "name": "USB Microphone Full Name (MME shortened-name hint 1)",
                "max_input_channels": 1,
                "max_output_channels": 0,
                "host_api": "Windows WASAPI",
                "alias_count": 1,
            }
        ],
    )
    monkeypatch.setattr(
        "rex.audio_config.build_speaker_picker_devices",
        lambda *, devices: [
            {
                "index": 1,
                "name": "Desk Speakers",
                "max_input_channels": 0,
                "max_output_channels": 2,
                "host_api": "Windows WASAPI",
                "alias_count": 1,
            }
        ],
    )

    rex_setup_bridge._handle_audio_devices()

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "devices": [
            {
                "index": 0,
                "name": "USB Microphone Full Name (MME shortened-name hint 1)",
                "max_input_channels": 1,
                "max_output_channels": 0,
            },
            {
                "index": 1,
                "name": "Desk Speakers",
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
        ],
        "microphones": [
            {
                "index": 0,
                "name": "USB Microphone",
                "max_input_channels": 1,
                "max_output_channels": 0,
                "host_api": "Windows WASAPI",
                "alias_count": 1,
            }
        ],
        "speakers": [
            {
                "index": 1,
                "name": "Desk Speakers",
                "max_input_channels": 0,
                "max_output_channels": 2,
                "host_api": "Windows WASAPI",
                "alias_count": 1,
            }
        ],
    }


@pytest.mark.parametrize(
    ("kind", "expected_probe"),
    [("microphone", "input"), ("speaker", "output")],
)
def test_audio_device_test_uses_non_persisting_canonical_probe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    expected_probe: str,
) -> None:
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "rex.audio_config.test_input_device", lambda index: calls.append(("input", index))
    )
    monkeypatch.setattr(
        "rex.audio_config.test_output_device", lambda index: calls.append(("output", index))
    )

    rex_setup_bridge._handle_test_audio_device({"kind": kind, "device_index": 3})

    assert json.loads(capsys.readouterr().out) == {"ok": True}
    assert calls == [(expected_probe, 3)]


def test_audio_device_test_rejects_invalid_kind_without_probing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "rex.audio_config.test_input_device",
        lambda _index: pytest.fail("invalid kind must not probe input"),
    )
    monkeypatch.setattr(
        "rex.audio_config.test_output_device",
        lambda _index: pytest.fail("invalid kind must not probe output"),
    )

    rex_setup_bridge._handle_test_audio_device({"kind": "camera", "device_index": 1})

    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is False
    assert "microphone or speaker" in response["error"]


def test_audio_device_test_reports_probe_failure_without_persistence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_probe(_index: int) -> None:
        raise AudioDeviceError("microphone is busy")

    monkeypatch.setattr("rex.audio_config.test_input_device", fail_probe)

    rex_setup_bridge._handle_test_audio_device({"kind": "microphone", "device_index": 2})

    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "microphone is busy",
    }
