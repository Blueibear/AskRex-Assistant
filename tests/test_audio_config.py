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

    def query_devices(self):
        return self.devices

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


def test_microphone_picker_groups_host_aliases_and_keeps_preferred_canonical_index():
    choices = audio_config.build_microphone_picker_devices(
        devices=[
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 0},
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 1},
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 2},
            {"name": "Line In", "max_input_channels": 1, "hostapi": 2},
            {"name": "Speakers (Loopback)", "max_input_channels": 2, "hostapi": 2},
        ],
        hostapis=[
            {"name": "MME"},
            {"name": "Windows DirectSound"},
            {"name": "Windows WASAPI"},
        ],
    )

    assert choices == [
        {
            "index": 3,
            "name": "Line In",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "host_api": "Windows WASAPI",
            "alias_count": 1,
        },
        {
            "index": 1,
            "name": "USB Mic (preferred via Windows DirectSound; 3 Windows audio entries)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "host_api": "Windows DirectSound",
            "alias_count": 3,
        },
    ]


def test_microphone_picker_labels_same_host_name_conflicts_without_deduplicating():
    choices = audio_config.build_microphone_picker_devices(
        devices=[
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 0},
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 0},
        ],
        hostapis=[{"name": "Windows WASAPI"}],
    )

    assert [choice["index"] for choice in choices] == [0, 1]
    assert [choice["name"] for choice in choices] == [
        "USB Mic (Windows WASAPI 1)",
        "USB Mic (Windows WASAPI 2)",
    ]


def test_microphone_picker_keeps_distinct_canonical_devices_on_cross_host_name_conflict():
    """A same-host duplicate name must not be paired with a same-named device
    on another host API: that pairing is ambiguous, so merging it could
    silently hide a distinct canonical PortAudio device behind an alias
    group for an unrelated device (see TEST-002 review)."""
    choices = audio_config.build_microphone_picker_devices(
        devices=[
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 0},
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 0},
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 1},
        ],
        hostapis=[{"name": "Windows WASAPI"}, {"name": "Windows DirectSound"}],
    )

    assert choices == [
        {
            "index": 2,
            "name": "USB Mic (Windows DirectSound 1)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "host_api": "Windows DirectSound",
            "alias_count": 1,
        },
        {
            "index": 0,
            "name": "USB Mic (Windows WASAPI 1)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "host_api": "Windows WASAPI",
            "alias_count": 1,
        },
        {
            "index": 1,
            "name": "USB Mic (Windows WASAPI 2)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "host_api": "Windows WASAPI",
            "alias_count": 1,
        },
    ]
    # Every physically distinct canonical PortAudio index remains selectable.
    assert {choice["index"] for choice in choices} == {0, 1, 2}


def test_microphone_picker_choice_persists_its_canonical_portaudio_index():
    choice = audio_config.build_microphone_picker_devices(
        devices=[
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 0},
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 1},
        ],
        hostapis=[{"name": "Windows WASAPI"}, {"name": "Windows DirectSound"}],
    )[0]

    config: dict[str, object] = {}
    audio_config.set_selected_input_device_index(config, choice["index"])

    assert choice["index"] == 1
    assert config == {"audio": {"input_device_index": 1}}


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
