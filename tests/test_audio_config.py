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


def test_microphone_picker_labels_host_api_duplicates_without_merging_canonical_indices():
    """Distinct PortAudio entries that share a name must stay separately
    selectable: a shared name is not proof of shared physical identity, so
    the picker must never collapse them into one preferred alias that hides
    the other canonical indices (see TEST-002 review)."""
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
            "index": 0,
            "name": "USB Mic (MME 1)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "host_api": "MME",
            "alias_count": 1,
        },
        {
            "index": 1,
            "name": "USB Mic (Windows DirectSound 1)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "host_api": "Windows DirectSound",
            "alias_count": 1,
        },
        {
            "index": 2,
            "name": "USB Mic (Windows WASAPI 1)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "host_api": "Windows WASAPI",
            "alias_count": 1,
        },
    ]
    # All three canonical PortAudio indices remain independently selectable
    # rather than being collapsed into one preferred alias.
    assert {choice["index"] for choice in choices} == {0, 1, 2, 3}


def test_microphone_picker_preserves_both_indices_for_identical_names_on_different_host_apis():
    """Two physically distinct microphones can coincidentally report an
    identical driver name while each appearing exactly once on a different
    host API. PortAudio exposes no physical-identity signal beyond
    name/host-API, so this exact shape must never be treated as one device
    redundantly exposed twice: each canonical index must remain its own
    choice (see TEST-002 review)."""
    choices = audio_config.build_microphone_picker_devices(
        devices=[
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 0},
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 1},
        ],
        hostapis=[{"name": "MME"}, {"name": "Windows WASAPI"}],
    )

    assert [choice["index"] for choice in choices] == [0, 1]
    assert [choice["name"] for choice in choices] == [
        "USB Mic (MME 1)",
        "USB Mic (Windows WASAPI 1)",
    ]
    assert [choice["alias_count"] for choice in choices] == [1, 1]
    assert {choice["index"] for choice in choices} == {0, 1}


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


def test_speaker_picker_labels_host_api_duplicates_without_merging_canonical_indices():
    """Distinct output-capable PortAudio entries that share a name must stay
    separately selectable: a shared name is not proof of shared physical
    identity, so the picker must never collapse them into one preferred
    alias that hides the other canonical indices (see TEST-003)."""
    choices = audio_config.build_speaker_picker_devices(
        devices=[
            {"name": "USB Speakers", "max_output_channels": 2, "hostapi": 0},
            {"name": "USB Speakers", "max_output_channels": 2, "hostapi": 1},
            {"name": "USB Speakers", "max_output_channels": 2, "hostapi": 2},
            {"name": "Headphones", "max_output_channels": 2, "hostapi": 2},
            {"name": "Microphone Array", "max_input_channels": 2, "hostapi": 2},
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
            "name": "Headphones",
            "max_input_channels": 0,
            "max_output_channels": 2,
            "host_api": "Windows WASAPI",
            "alias_count": 1,
        },
        {
            "index": 0,
            "name": "USB Speakers (MME 1)",
            "max_input_channels": 0,
            "max_output_channels": 2,
            "host_api": "MME",
            "alias_count": 1,
        },
        {
            "index": 1,
            "name": "USB Speakers (Windows DirectSound 1)",
            "max_input_channels": 0,
            "max_output_channels": 2,
            "host_api": "Windows DirectSound",
            "alias_count": 1,
        },
        {
            "index": 2,
            "name": "USB Speakers (Windows WASAPI 1)",
            "max_input_channels": 0,
            "max_output_channels": 2,
            "host_api": "Windows WASAPI",
            "alias_count": 1,
        },
    ]
    # All three canonical PortAudio indices remain independently selectable
    # rather than being collapsed into one preferred alias. The input-only
    # device is excluded, and it does not participate in name grouping.
    assert {choice["index"] for choice in choices} == {0, 1, 2, 3}


def test_speaker_picker_preserves_both_indices_for_identical_names_on_different_host_apis():
    """Two physically distinct speakers can coincidentally report an
    identical driver name while each appearing exactly once on a different
    host API; each canonical index must remain its own choice (TEST-003)."""
    choices = audio_config.build_speaker_picker_devices(
        devices=[
            {"name": "USB Speakers", "max_output_channels": 2, "hostapi": 0},
            {"name": "USB Speakers", "max_output_channels": 2, "hostapi": 1},
        ],
        hostapis=[{"name": "MME"}, {"name": "Windows WASAPI"}],
    )

    assert [choice["index"] for choice in choices] == [0, 1]
    assert [choice["name"] for choice in choices] == [
        "USB Speakers (MME 1)",
        "USB Speakers (Windows WASAPI 1)",
    ]
    assert [choice["alias_count"] for choice in choices] == [1, 1]
    assert {choice["index"] for choice in choices} == {0, 1}


def test_speaker_picker_explains_unambiguous_truncated_mme_aliases_without_merging():
    """TEST-003: output aliases are understandable but retain every index."""
    choices = audio_config.build_speaker_picker_devices(
        devices=[
            {
                "name": "Speakers (Studio Monitor Seri",
                "max_output_channels": 2,
                "hostapi": 0,
            },
            {
                "name": "Speakers (Studio Monitor Series)",
                "max_output_channels": 2,
                "hostapi": 1,
            },
            {
                "name": "Speakers (Studio Monitor Series)",
                "max_output_channels": 2,
                "hostapi": 2,
            },
        ],
        hostapis=[
            {"name": "MME"},
            {"name": "Windows DirectSound"},
            {"name": "Windows WASAPI"},
        ],
    )

    assert [choice["index"] for choice in choices] == [0, 1, 2]
    assert [choice["name"] for choice in choices] == [
        "Speakers (Studio Monitor Series) (MME output, possible shortened-name "
        "alias of 2 same-named host-API entries; separate device 1)",
        "Speakers (Studio Monitor Series) (Windows DirectSound 1)",
        "Speakers (Studio Monitor Series) (Windows WASAPI 1)",
    ]
    assert {choice["index"] for choice in choices} == {0, 1, 2}


def test_speaker_picker_leaves_ambiguous_truncated_mme_alias_distinct():
    choices = audio_config.build_speaker_picker_devices(
        devices=[
            {
                "name": "Speakers (Living Room Device",
                "max_output_channels": 2,
                "hostapi": 0,
            },
            {
                "name": "Speakers (Living Room Device A)",
                "max_output_channels": 2,
                "hostapi": 1,
            },
            {
                "name": "Speakers (Living Room Device B)",
                "max_output_channels": 2,
                "hostapi": 2,
            },
        ],
        hostapis=[
            {"name": "MME"},
            {"name": "Windows DirectSound"},
            {"name": "Windows WASAPI"},
        ],
    )

    assert [choice["index"] for choice in choices] == [0, 1, 2]
    assert choices[0]["name"] == (
        "Speakers (Living Room Device (MME output, shortened name is ambiguous; separate device)"
    )
    assert {choice["index"] for choice in choices} == {0, 1, 2}


def test_speaker_picker_labels_same_host_name_conflicts_without_deduplicating():
    choices = audio_config.build_speaker_picker_devices(
        devices=[
            {"name": "USB Speakers", "max_output_channels": 2, "hostapi": 0},
            {"name": "USB Speakers", "max_output_channels": 2, "hostapi": 0},
        ],
        hostapis=[{"name": "Windows WASAPI"}],
    )

    assert [choice["index"] for choice in choices] == [0, 1]
    assert [choice["name"] for choice in choices] == [
        "USB Speakers (Windows WASAPI 1)",
        "USB Speakers (Windows WASAPI 2)",
    ]


def test_speaker_picker_excludes_input_only_devices_and_keeps_input_output_devices():
    choices = audio_config.build_speaker_picker_devices(
        devices=[
            {"name": "Mic Only", "max_input_channels": 1, "max_output_channels": 0, "hostapi": 0},
            {
                "name": "Headset",
                "max_input_channels": 1,
                "max_output_channels": 2,
                "hostapi": 0,
            },
        ],
        hostapis=[{"name": "Windows WASAPI"}],
    )

    assert [choice["index"] for choice in choices] == [1]
    assert choices[0]["name"] == "Headset"
    assert choices[0]["max_input_channels"] == 1
    assert choices[0]["max_output_channels"] == 2


def test_speaker_picker_choice_persists_its_canonical_portaudio_index():
    choice = audio_config.build_speaker_picker_devices(
        devices=[
            {"name": "USB Speakers", "max_output_channels": 2, "hostapi": 0},
            {"name": "USB Speakers", "max_output_channels": 2, "hostapi": 1},
        ],
        hostapis=[{"name": "Windows WASAPI"}, {"name": "Windows DirectSound"}],
    )[0]

    config: dict[str, object] = {}
    audio_config.set_selected_output_device_index(config, choice["index"])

    assert choice["index"] == 1
    assert config == {"audio": {"output_device_index": 1}}


def test_speaker_picker_orders_display_choices_by_label_then_canonical_index():
    """TEST-003: presentation order is stable without changing playback IDs."""
    choices = audio_config.build_speaker_picker_devices(
        devices=[
            {"name": "Zulu Speakers", "max_output_channels": 2, "hostapi": 0},
            {"name": "Alpha Speakers", "max_output_channels": 2, "hostapi": 1},
            {"name": "Alpha Speakers", "max_output_channels": 2, "hostapi": 0},
        ],
        hostapis=[{"name": "MME"}, {"name": "Windows WASAPI"}],
    )

    assert [(choice["name"], choice["index"]) for choice in choices] == [
        ("Alpha Speakers (MME 1)", 2),
        ("Alpha Speakers (Windows WASAPI 1)", 1),
        ("Zulu Speakers", 0),
    ]


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


def test_microphone_picker_expands_only_unambiguous_truncated_mme_name_matches():
    """A physical Windows MME truncation is explained but never merged."""
    choices = audio_config.build_microphone_picker_devices(
        devices=[
            {
                "name": "Microphone (C922 Pro Stream Web",
                "max_input_channels": 1,
                "hostapi": 0,
            },
            {
                "name": "Microphone (C922 Pro Stream Webcam)",
                "max_input_channels": 1,
                "hostapi": 1,
            },
            {
                "name": "Microphone (C922 Pro Stream Webcam)",
                "max_input_channels": 1,
                "hostapi": 2,
            },
            {
                "name": "Microphone (C922 Pro Stream Webcam)",
                "max_input_channels": 1,
                "hostapi": 3,
            },
        ],
        hostapis=[
            {"name": "MME"},
            {"name": "Windows DirectSound"},
            {"name": "Windows WASAPI"},
            {"name": "Windows WDM-KS"},
        ],
    )

    assert {choice["index"] for choice in choices} == {0, 1, 2, 3}
    assert (
        choices[0]["name"]
        == "Microphone (C922 Pro Stream Webcam) (MME input, possible shortened-name "
        "alias of 3 same-named host-API entries; separate device 1)"
    )
    assert [choice["name"] for choice in choices[1:]] == [
        "Microphone (C922 Pro Stream Webcam) (Windows DirectSound 1)",
        "Microphone (C922 Pro Stream Webcam) (Windows WASAPI 1)",
        "Microphone (C922 Pro Stream Webcam) (Windows WDM-KS 1)",
    ]


def test_microphone_picker_does_not_expand_ambiguous_truncated_mme_name_match():
    """A prefix shared by distinct devices cannot safely identify either one."""
    choices = audio_config.build_microphone_picker_devices(
        devices=[
            {"name": "Microphone (Studio", "max_input_channels": 1, "hostapi": 0},
            {"name": "Microphone (Studio A)", "max_input_channels": 1, "hostapi": 1},
            {"name": "Microphone (Studio B)", "max_input_channels": 1, "hostapi": 2},
        ],
        hostapis=[
            {"name": "MME"},
            {"name": "Windows DirectSound"},
            {"name": "Windows WASAPI"},
        ],
    )

    assert {choice["index"] for choice in choices} == {0, 1, 2}
    # A short shared prefix is not enough evidence that Windows truncated an
    # MME endpoint. Do not make it look like either full-name microphone.
    assert choices[0]["name"] == "Microphone (Studio"


def test_microphone_picker_keeps_same_name_collision_as_separate_shortened_name_hint():
    """A matching full name is only a display hint, even when it repeats.

    The MME entry must retain its canonical index and cannot be presented as
    proof that it is either of two distinct same-named host-API devices.
    """
    choices = audio_config.build_microphone_picker_devices(
        devices=[
            {
                "name": "Microphone (USB Capture Device Seri",
                "max_input_channels": 1,
                "hostapi": 0,
            },
            {
                "name": "Microphone (USB Capture Device Series Pro)",
                "max_input_channels": 1,
                "hostapi": 1,
            },
            {
                "name": "Microphone (USB Capture Device Series Pro)",
                "max_input_channels": 1,
                "hostapi": 2,
            },
        ],
        hostapis=[
            {"name": "MME"},
            {"name": "Windows DirectSound"},
            {"name": "Windows WASAPI"},
        ],
    )

    assert {choice["index"] for choice in choices} == {0, 1, 2}
    assert choices[0]["name"] == (
        "Microphone (USB Capture Device Series Pro) (MME input, possible shortened-name alias "
        "of 2 same-named host-API entries; separate device 1)"
    )
    assert [choice["name"] for choice in choices[1:]] == [
        "Microphone (USB Capture Device Series Pro) (Windows DirectSound 1)",
        "Microphone (USB Capture Device Series Pro) (Windows WASAPI 1)",
    ]


def test_microphone_picker_gives_repeated_truncated_mme_aliases_stable_separate_labels():
    choices = audio_config.build_microphone_picker_devices(
        devices=[
            {"name": "Headset Microphone (Bose Flex S", "max_input_channels": 1, "hostapi": 0},
            {"name": "Headset Microphone (Bose Flex S", "max_input_channels": 1, "hostapi": 0},
            {
                "name": "Headset Microphone (Bose Flex SoundLink)",
                "max_input_channels": 1,
                "hostapi": 1,
            },
        ],
        hostapis=[{"name": "MME"}, {"name": "Windows DirectSound"}],
    )

    by_index = {choice["index"]: choice["name"] for choice in choices}
    assert by_index[0] == (
        "Headset Microphone (Bose Flex SoundLink) (MME input, possible shortened-name "
        "alias; separate device 1)"
    )
    assert by_index[1] == (
        "Headset Microphone (Bose Flex SoundLink) (MME input, possible shortened-name "
        "alias; separate device 2)"
    )
    assert by_index[2] == "Headset Microphone (Bose Flex SoundLink)"
    assert {choice["index"] for choice in choices} == {0, 1, 2}


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
