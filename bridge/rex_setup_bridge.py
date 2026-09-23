"""Setup IPC bridge for the Electron GUI.

Reads a JSON command from stdin and writes a JSON response to stdout.

Supported commands:
  {"command": "status"}
    -> {"needs_setup": true|false, "background_voice_enabled": true|false}

  {"command": "audio_devices"}
    -> {"ok": true, "devices": [...]} | {"ok": false, "error": "..."}

  {"command": "test_audio_device", "kind": "microphone|speaker", "device_index": 0}
    -> {"ok": true} | {"ok": false, "error": "..."}

  {"command": "complete", "username": "...", "password": "...",
   "llm_provider": "...", "llm_api_key": "...", "openai_base_url": "...",
   "openai_model": "...", "tts_provider": "...", "ha_base_url": "...",
   "ha_token": "..."}
    -> {"ok": true, "user_id": "..."} | {"ok": false, "error": "..."}

  llm_provider "lmstudio" selects Rex's existing OpenAI-compatible runtime
  path (config `models.llm_provider = "openai"` plus `openai.base_url` set
  to the supplied `openai_base_url`, e.g. http://127.0.0.1:1234/v1). LM
  Studio also requires an explicit `openai_model` (discovered from the
  configured endpoint's `/v1/models` or typed manually) so setup never
  silently persists the unrelated official-OpenAI default model.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SetupChoices:
    username: str
    password: str
    llm_provider: str
    llm_api_key: str
    openai_base_url: str
    openai_model: str
    tts_provider: str
    tts_voice_id: str
    microphone_device_index: Any
    speaker_device_index: Any
    wake_word_id: str
    local_device_id: str
    room_name: str
    background_voice_enabled: bool
    ha_base_url: str
    ha_token: str
    ollama_base_url: str


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.stdout.write(json.dumps({"ok": False, "error": "Invalid setup request"}))
        sys.exit(1)

    command = str(payload.get("command", ""))

    try:
        if command == "status":
            _handle_status()
        elif command == "audio_devices":
            _handle_audio_devices()
        elif command == "test_audio_device":
            _handle_test_audio_device(payload)
        elif command == "complete":
            _handle_complete(payload)
        else:
            sys.stdout.write(json.dumps({"ok": False, "error": f"unknown command: {command}"}))
    except Exception:
        sys.stdout.write(json.dumps({"ok": False, "error": "Secure setup persistence failed"}))


def _read_user_count() -> int:
    """Read all canonical desktop-user stores for first-run routing."""
    from rex.auth import _open_db  # noqa: PLC2701
    from rex.identity import list_known_users

    with _open_db() as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    auth_user_count = int(row[0]) if row else 0
    # Legacy completed installs may have a user profile but predate the auth
    # database.  They are established households, not first-run installs.
    return max(auth_user_count, len(list_known_users()))


def _handle_status() -> None:
    from rex.config_manager import load_config

    try:
        count = _read_user_count()
    except Exception:
        count = 0

    try:
        config = load_config()
    except Exception:
        config = {}
    runtime = config.get("runtime") if isinstance(config, dict) else None
    background_voice_enabled = (
        isinstance(runtime, dict) and runtime.get("background_voice_enabled") is True
    )

    sys.stdout.write(
        json.dumps(
            {
                "needs_setup": count == 0,
                "background_voice_enabled": background_voice_enabled,
            }
        )
    )


def _handle_audio_devices() -> None:
    from rex.assistant_errors import AudioDeviceError
    from rex.audio_config import list_devices

    try:
        raw_devices = list_devices()
    except AudioDeviceError as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}))
        return

    devices = [
        {
            "index": index,
            "name": str(device.get("name", "")),
            "max_input_channels": int(device.get("max_input_channels", 0) or 0),
            "max_output_channels": int(device.get("max_output_channels", 0) or 0),
        }
        for index, device in enumerate(raw_devices)
    ]
    sys.stdout.write(json.dumps({"ok": True, "devices": devices}))


def _handle_test_audio_device(payload: dict[str, Any]) -> None:
    from rex.assistant_errors import AudioDeviceError
    from rex.audio_config import test_input_device, test_output_device

    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in {"microphone", "speaker"}:
        sys.stdout.write(json.dumps({"ok": False, "error": "kind must be microphone or speaker"}))
        return

    device_index = payload.get("device_index")
    if isinstance(device_index, bool) or not isinstance(device_index, int):
        sys.stdout.write(json.dumps({"ok": False, "error": "device_index must be an integer"}))
        return

    try:
        if kind == "microphone":
            test_input_device(device_index)
        else:
            test_output_device(device_index)
    except AudioDeviceError as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}))
        return

    sys.stdout.write(json.dumps({"ok": True}))


def _parse_setup_choices(payload: dict[str, Any]) -> SetupChoices:
    defer_home_assistant = payload.get("defer_home_assistant") is True
    return SetupChoices(
        username=str(payload.get("username") or "").strip(),
        password=str(payload.get("password") or ""),
        llm_provider=str(payload.get("llm_provider") or "local").strip(),
        llm_api_key=str(payload.get("llm_api_key") or "").strip(),
        openai_base_url=str(payload.get("openai_base_url") or "").strip(),
        openai_model=str(payload.get("openai_model") or "").strip(),
        tts_provider=str(payload.get("tts_provider") or "none").strip(),
        tts_voice_id=str(payload.get("tts_voice_id") or "").strip(),
        microphone_device_index=payload.get("microphone_device_index"),
        speaker_device_index=payload.get("speaker_device_index"),
        wake_word_id=str(payload.get("wake_word_id") or "").strip(),
        local_device_id=str(payload.get("local_device_id") or "local_voice").strip(),
        room_name=str(payload.get("room_name") or "").strip(),
        background_voice_enabled=payload.get("background_voice_enabled") is True,
        ha_base_url=("" if defer_home_assistant else str(payload.get("ha_base_url") or "").strip()),
        ha_token="" if defer_home_assistant else str(payload.get("ha_token") or "").strip(),
        ollama_base_url=str(payload.get("ollama_base_url") or "").strip(),
    )


def _validate_setup_choices(choices: SetupChoices) -> str | None:
    if not choices.username or not choices.password:
        return "username and password are required"
    if choices.llm_provider not in {
        "local",
        "openai",
        "openrouter",
        "anthropic",
        "ollama",
        "lmstudio",
    }:
        return "unsupported LLM provider"
    if choices.llm_provider == "lmstudio" and not choices.openai_base_url:
        return "LM Studio requires a base URL"
    if choices.llm_provider == "lmstudio" and not choices.openai_model:
        return "LM Studio requires a model. Discover it from the configured endpoint or enter it manually."
    return None


def _build_setup_secrets(choices: SetupChoices) -> dict[str, str]:
    secrets_to_store: dict[str, str] = {}
    if choices.ha_token:
        secrets_to_store["HA_TOKEN"] = choices.ha_token
    if not choices.llm_api_key:
        return secrets_to_store
    logical_name = {
        "openai": "OPENAI_API_KEY",
        "lmstudio": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "ollama": "OLLAMA_API_KEY",
    }.get(choices.llm_provider)
    if logical_name is None:
        raise ValueError("Selected LLM provider does not accept an API key")
    secrets_to_store[logical_name] = choices.llm_api_key
    return secrets_to_store


def _apply_model_config(config: dict[str, Any], choices: SetupChoices) -> None:
    if choices.llm_provider == "local":
        runtime_provider = "transformers"
    elif choices.llm_provider == "lmstudio":
        # LM Studio has no separate runtime provider: it is Rex's existing
        # OpenAI-compatible path, selected only by a configured base URL.
        runtime_provider = "openai"
    else:
        runtime_provider = choices.llm_provider
    models = config.setdefault("models", {})
    models["llm_provider"] = runtime_provider
    models["tts_provider"] = choices.tts_provider
    if choices.tts_voice_id:
        models["tts_voice"] = choices.tts_voice_id
    if choices.llm_provider in {"openai", "lmstudio"}:
        openai_config = config.setdefault("openai", {})
        if choices.llm_provider == "lmstudio" and choices.openai_model:
            # LM Studio serves whatever model is actually loaded locally, so
            # its explicit discovered/typed model always wins over the
            # unrelated official-OpenAI default (TEST-001).
            openai_config["model"] = choices.openai_model
        else:
            openai_config.setdefault("model", "gpt-4o")
        if choices.llm_provider == "lmstudio" and choices.openai_base_url:
            openai_config["base_url"] = choices.openai_base_url
    elif choices.llm_provider == "openrouter":
        openrouter = config.setdefault("openrouter", {})
        openrouter.setdefault("model", "openai/gpt-4o")
        openrouter.setdefault("base_url", "https://openrouter.ai/api/v1")
    elif choices.llm_provider == "ollama" and choices.ollama_base_url:
        config.setdefault("ollama", {})["base_url"] = choices.ollama_base_url


def _builtin_setup_wakeword_config(phrase: str) -> dict[str, Any]:
    return {
        "backend": "openwakeword",
        "wakeword": phrase,
        "keyword": phrase,
        "model_path": None,
        "embedding_path": None,
    }


def _resolve_setup_wakeword_config(wake_word_id: str) -> dict[str, Any]:
    from rex.wakeword_catalog import (
        DEFAULT_OPENWAKEWORD_KEYWORDS,
        list_openwakeword_keywords,
        normalize_keyword,
    )

    custom_inventory_unavailable = False
    try:
        from rex.wakeword.trainer import list_custom_wake_words

        custom_wake_words = list_custom_wake_words()
    except Exception:
        custom_wake_words = []
        custom_inventory_unavailable = True

    for wake_word in custom_wake_words:
        if str(wake_word.get("id") or "") != wake_word_id:
            continue
        if wake_word.get("engine") != "custom_embedding":
            continue
        phrase = str(wake_word.get("name") or wake_word_id.replace("_", " ")).strip()
        embedding_path = wake_word.get("model_path")
        return {
            "backend": "custom_embedding",
            "wakeword": phrase,
            "keyword": phrase,
            "model_path": None,
            "embedding_path": str(embedding_path) if embedding_path else None,
        }

    requested_phrase = wake_word_id.replace("_", " ").strip()
    normalized_phrase = normalize_keyword(requested_phrase)
    fallback_map = {normalize_keyword(item): item for item in DEFAULT_OPENWAKEWORD_KEYWORDS}
    if normalized_phrase in fallback_map:
        return _builtin_setup_wakeword_config(fallback_map[normalized_phrase])

    try:
        import openwakeword as openwakeword_module

        live_keywords = list_openwakeword_keywords(openwakeword_module)
    except Exception:
        live_keywords = []
    live_map = {normalize_keyword(item): item for item in live_keywords}
    if normalized_phrase in live_map:
        return _builtin_setup_wakeword_config(live_map[normalized_phrase])

    if custom_inventory_unavailable:
        raise ValueError("Selected custom wake word is no longer available")
    raise ValueError("Selected wake word is no longer available")


def _apply_voice_config(config: dict[str, Any], choices: SetupChoices) -> None:
    audio = config.setdefault("audio", {})
    if choices.microphone_device_index is not None:
        audio["input_device_index"] = choices.microphone_device_index
    if choices.speaker_device_index is not None:
        audio["output_device_index"] = choices.speaker_device_index
    if choices.wake_word_id:
        wakeword = config.setdefault("wakeword", {})
        wakeword.update(_resolve_setup_wakeword_config(choices.wake_word_id))
    if choices.local_device_id and choices.room_name:
        config.setdefault("device_room_map", {})[choices.local_device_id] = choices.room_name


def _apply_setup_config(config: dict[str, Any], choices: SetupChoices, user_id: str) -> None:
    _apply_model_config(config, choices)
    _apply_voice_config(config, choices)
    runtime = config.setdefault("runtime", {})
    runtime["active_user"] = user_id
    runtime["background_voice_enabled"] = choices.background_voice_enabled
    if choices.ha_base_url:
        config.setdefault("home_assistant", {})["base_url"] = choices.ha_base_url


def _rollback_setup_user(user_id: str) -> None:
    from rex.auth import _open_db  # noqa: PLC2701

    try:
        with _open_db() as conn:
            conn.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
    except Exception:
        logger.error("Setup rollback could not remove the incomplete user")


def _handle_complete(payload: dict[str, Any]) -> None:
    choices = _parse_setup_choices(payload)
    validation_error = _validate_setup_choices(choices)
    if validation_error is not None:
        sys.stdout.write(json.dumps({"ok": False, "error": validation_error}))
        return

    if _read_user_count() > 0:
        sys.stdout.write(json.dumps({"ok": False, "error": "setup is already complete"}))
        return

    from rex.auth import create_user
    from rex.credential_persistence import persist_household_secrets
    from rex.permissions import bootstrap_admin_if_first_user

    user_id: str | None = None
    try:
        secrets_to_store = _build_setup_secrets(choices)
        user = create_user(choices.username, choices.password)
        user_id = str(user["id"])
        bootstrap_admin_if_first_user(user_id)
        persist_household_secrets(
            secrets_to_store,
            update_config=lambda config: _apply_setup_config(config, choices, user_id),
        )
    except Exception:
        if user_id is not None:
            _rollback_setup_user(user_id)
        raise
    sys.stdout.write(json.dumps({"ok": True, "user_id": user_id}))


if __name__ == "__main__":
    main()
