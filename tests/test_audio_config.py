import pytest

import rex.audio_config as audio_config


class _DummyStream:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _DummySoundDevice:
    def __init__(self):
        self.devices = [
            {"name": "Mic", "max_input_channels": 2, "max_output_channels": 0},
            {"name": "Speaker", "max_input_channels": 0, "max_output_channels": 2},
        ]

    def query_devices(self, device=None, kind=None):
        if device is None:
            return self.devices
        return {**self.devices[device], "default_samplerate": 48000.0}

    def check_input_settings(self, **kwargs):
        return None

    def InputStream(self, *args, **kwargs):  # noqa: N802 - mimic sounddevice API
        return _DummyStream()

    def OutputStream(self, *args, **kwargs):  # noqa: N802 - mimic sounddevice API
        return _DummyStream()


def test_list_devices_requires_sounddevice(monkeypatch):
    monkeypatch.setattr(audio_config, "sd", None, raising=False)
    with pytest.raises(audio_config.AudioDeviceError):
        audio_config.list_devices()


def test_resolve_input_device_index_prefers_directsound_exact_match():
    devices = [
        {"name": "Headset Microphone (Bose Flex S", "max_input_channels": 1, "hostapi": 0},
        {
            "name": "Headset Microphone (Bose Flex SoundLink)",
            "max_input_channels": 1,
            "hostapi": 1,
        },
        {
            "name": "Headset Microphone (Bose Flex SoundLink)",
            "max_input_channels": 1,
            "hostapi": 2,
        },
    ]
    hostapis = [
        {"name": "MME"},
        {"name": "Windows DirectSound"},
        {"name": "Windows WASAPI"},
    ]

    result = audio_config.resolve_input_device_index_by_name(
        "Headset Microphone (Bose Flex SoundLink)",
        devices=devices,
        hostapis=hostapis,
    )

    assert result == 1


def test_resolve_input_device_index_handles_chromium_default_prefix():
    devices = [
        {
            "name": "Microphone (C922 Pro Stream Webcam)",
            "max_input_channels": 2,
            "hostapi": 0,
        }
    ]

    result = audio_config.resolve_input_device_index_by_name(
        "Default - Microphone (C922 Pro Stream Webcam)",
        devices=devices,
        hostapis=[{"name": "Windows WASAPI"}],
    )

    assert result == 0


def test_resolve_input_device_index_fails_closed_for_unknown_device():
    with pytest.raises(audio_config.AudioDeviceError, match="unavailable"):
        audio_config.resolve_input_device_index_by_name(
            "Missing microphone",
            devices=[{"name": "Other mic", "max_input_channels": 1, "hostapi": 0}],
            hostapis=[{"name": "MME"}],
        )


def test_main_updates_json_config(monkeypatch, tmp_path):
    """Test that audio_config.main updates rex_config.json instead of .env."""
    # Create a temporary config file
    tmp_path / "rex_config.json"

    # Mock the config module to use our temp path
    saved_config = None

    def mock_load_config():
        return {
            "audio": {
                "input_device_index": None,
                "output_device_index": None,
            }
        }

    def mock_save_config(config):
        nonlocal saved_config
        saved_config = config

    monkeypatch.setattr(audio_config, "sd", _DummySoundDevice(), raising=False)
    monkeypatch.setattr(audio_config, "load_config", mock_load_config)
    monkeypatch.setattr(audio_config, "save_config", mock_save_config)

    exit_code = audio_config.main(["--set-input", "0", "--set-output", "1"])

    assert exit_code == 0
    assert saved_config is not None
    assert saved_config["audio"]["input_device_index"] == 0
    assert saved_config["audio"]["output_device_index"] == 1
