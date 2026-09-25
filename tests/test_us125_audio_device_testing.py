"""US-125 contracts for first-run microphone and speaker functional tests."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import pytest

from rex import audio_config
from rex.assistant_errors import AudioDeviceError


class _FakeSoundDevice:
    def __init__(
        self,
        *,
        input_error: Exception | None = None,
        output_error: Exception | None = None,
        reject_target_rate: bool = False,
        native_rate: float = 48000.0,
    ):
        self.input_error = input_error
        self.output_error = output_error
        self.reject_target_rate = reject_target_rate
        self.native_rate = native_rate
        self.input_devices: list[int] = []
        self.input_stream_rates: list[float] = []
        self.output_devices: list[int] = []

    def check_input_settings(
        self, *, device: int | None, channels: int, dtype: str, samplerate: float
    ) -> None:
        assert channels == 1
        assert dtype == "float32"
        if self.reject_target_rate and float(samplerate) == 16000.0:
            raise RuntimeError("Invalid sample rate")

    def query_devices(self, device: int | None, kind: str) -> dict[str, float]:
        assert kind == "input"
        return {"default_samplerate": self.native_rate}

    def InputStream(  # noqa: N802
        self,
        *,
        device: int,
        samplerate: float,
        channels: int,
        dtype: str,
        blocksize: int,
    ) -> Any:
        assert blocksize == 0
        assert channels == 1
        assert dtype == "float32"
        self.input_devices.append(device)
        self.input_stream_rates.append(float(samplerate))
        if self.input_error is not None:
            raise self.input_error
        return nullcontext()

    def OutputStream(self, *, device: int, blocksize: int) -> Any:  # noqa: N802
        assert blocksize == 0
        self.output_devices.append(device)
        if self.output_error is not None:
            raise self.output_error
        return nullcontext()


def _devices() -> list[dict[str, object]]:
    return [
        {"name": "Microphone", "max_input_channels": 1, "max_output_channels": 0},
        {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Headset", "max_input_channels": 1, "max_output_channels": 2},
    ]


def _forbid_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_save(_config: dict[str, object]) -> None:
        raise AssertionError("functional audio probes must not persist configuration")

    monkeypatch.setattr(audio_config, "save_config", fail_save)
    monkeypatch.setattr(
        audio_config,
        "load_config",
        lambda: (_ for _ in ()).throw(
            AssertionError("functional audio probes must not load configuration")
        ),
    )


def test_input_device_probe_opens_selected_portaudio_device_without_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_sounddevice = _FakeSoundDevice()
    monkeypatch.setattr(audio_config, "list_devices", _devices)
    monkeypatch.setattr(audio_config, "_require_sounddevice", lambda: fake_sounddevice)
    _forbid_persistence(monkeypatch)

    audio_config.test_input_device(2)

    assert fake_sounddevice.input_devices == [2]
    assert fake_sounddevice.input_stream_rates == [16000.0]


def test_input_device_probe_accepts_native_rate_fallback_for_wasapi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_sounddevice = _FakeSoundDevice(reject_target_rate=True, native_rate=48000.0)
    monkeypatch.setattr(audio_config, "list_devices", _devices)
    monkeypatch.setattr(audio_config, "_require_sounddevice", lambda: fake_sounddevice)
    _forbid_persistence(monkeypatch)

    audio_config.test_input_device(2)

    assert fake_sounddevice.input_devices == [2]
    assert fake_sounddevice.input_stream_rates == [48000.0]


def test_output_device_probe_opens_selected_portaudio_device_without_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_sounddevice = _FakeSoundDevice()
    monkeypatch.setattr(audio_config, "list_devices", _devices)
    monkeypatch.setattr(audio_config, "_require_sounddevice", lambda: fake_sounddevice)
    _forbid_persistence(monkeypatch)

    audio_config.test_output_device(1)

    assert fake_sounddevice.output_devices == [1]


@pytest.mark.parametrize(
    ("probe_name", "device_id", "message"),
    [
        ("test_input_device", -1, "Invalid input device ID: -1"),
        ("test_input_device", 1, "Device 1 has no input channels."),
        ("test_output_device", 99, "Invalid output device ID: 99"),
        ("test_output_device", 0, "Device 0 has no output channels."),
    ],
)
def test_audio_device_probe_rejects_invalid_or_wrong_direction_devices(
    monkeypatch: pytest.MonkeyPatch,
    probe_name: str,
    device_id: int,
    message: str,
) -> None:
    monkeypatch.setattr(audio_config, "list_devices", _devices)
    _forbid_persistence(monkeypatch)

    with pytest.raises(AudioDeviceError, match=message):
        getattr(audio_config, probe_name)(device_id)


@pytest.mark.parametrize(
    ("probe_name", "device_id", "error_keyword"),
    [
        ("test_input_device", 0, "Failed to open input device 0"),
        ("test_output_device", 1, "Failed to open output device 1"),
    ],
)
def test_audio_device_probe_reports_stream_open_failure(
    monkeypatch: pytest.MonkeyPatch,
    probe_name: str,
    device_id: int,
    error_keyword: str,
) -> None:
    fake_sounddevice = _FakeSoundDevice(
        input_error=RuntimeError("input busy") if probe_name == "test_input_device" else None,
        output_error=RuntimeError("output busy") if probe_name == "test_output_device" else None,
    )
    monkeypatch.setattr(audio_config, "list_devices", _devices)
    monkeypatch.setattr(audio_config, "_require_sounddevice", lambda: fake_sounddevice)
    _forbid_persistence(monkeypatch)

    with pytest.raises(AudioDeviceError, match=error_keyword):
        getattr(audio_config, probe_name)(device_id)
