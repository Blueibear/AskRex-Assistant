from __future__ import annotations

import json

import pytest

import rex_voice_bridge


@pytest.mark.unit
def test_wakeword_runtime_tracker_projects_armed_status(monkeypatch):
    events: list[dict[str, object]] = []
    monkeypatch.setattr(rex_voice_bridge, "emit", events.append)
    tracker = rex_voice_bridge.WakeWordRuntimeTracker(
        configured_phrase="rex",
        configured_backend="custom_embedding",
        fallback_phrase="hey jarvis",
        microphone_label="USB Microphone",
        portaudio_device_index=4,
    )

    tracker.handle(
        {
            "level": "INFO",
            "message": "Wake-word listening cycle started",
            "extra": {
                "event": "wakeword_listening_cycle_started",
                "threshold": 0.5,
                "keyword": "rex",
                "backend": "custom_embedding",
                "detector_generation": 1,
            },
        }
    )

    runtime_events = [event for event in events if event.get("type") == "wakeword_runtime_status"]
    assert runtime_events == [
        {
            "type": "wakeword_runtime_status",
            "runtime": {
                "reason": "wakeword_listening_cycle_started",
                "configured_phrase": "rex",
                "active_phrase": "rex",
                "configured_backend": "custom_embedding",
                "active_backend": "custom_embedding",
                "threshold": 0.5,
                "fallback_active": False,
                "fallback_phrase": "hey jarvis",
                "detector_generation": 1,
                "armed": True,
                "microphone_label": "USB Microphone",
                "portaudio_device_index": 4,
            },
        }
    ]


@pytest.mark.unit
def test_wakeword_runtime_tracker_disarms_on_listening_cycle_ended(monkeypatch):
    events: list[dict[str, object]] = []
    monkeypatch.setattr(rex_voice_bridge, "emit", events.append)
    tracker = rex_voice_bridge.WakeWordRuntimeTracker(
        configured_phrase="rex",
        configured_backend="custom_embedding",
        fallback_phrase="hey jarvis",
        microphone_label="USB Microphone",
        portaudio_device_index=4,
    )

    tracker.handle(
        {
            "level": "INFO",
            "message": "Wake-word listening cycle started",
            "extra": {
                "event": "wakeword_listening_cycle_started",
                "threshold": 0.5,
                "keyword": "rex",
                "backend": "custom_embedding",
                "detector_generation": 1,
            },
        }
    )
    tracker.handle(
        {
            "level": "INFO",
            "message": "Wake-word listening cycle ended",
            "extra": {
                "event": "wakeword_listening_cycle_ended",
                "reason": "accepted_wake",
                "accepted": True,
                "threshold": 0.5,
                "confidence": 0.91,
                "keyword": "rex",
                "backend": "custom_embedding",
                "detector_generation": 1,
                "listening_cycle": 1,
                "attempts": 3,
                "duration_s": 1.2,
            },
        }
    )

    runtime_events = [event for event in events if event.get("type") == "wakeword_runtime_status"]
    assert runtime_events, "wakeword_listening_cycle_ended must project a runtime status"
    ended_runtime = runtime_events[-1]["runtime"]
    assert ended_runtime["reason"] == "wakeword_listening_cycle_ended"
    assert ended_runtime["armed"] is False

    # The next listening cycle must restore the armed projection.
    tracker.handle(
        {
            "level": "INFO",
            "message": "Wake-word listening cycle started",
            "extra": {
                "event": "wakeword_listening_cycle_started",
                "threshold": 0.5,
                "keyword": "rex",
                "backend": "custom_embedding",
                "detector_generation": 1,
            },
        }
    )
    runtime_events = [event for event in events if event.get("type") == "wakeword_runtime_status"]
    assert runtime_events[-1]["runtime"]["armed"] is True


@pytest.mark.unit
def test_wakeword_runtime_tracker_projects_bounded_attempt_evidence(monkeypatch):
    events: list[dict[str, object]] = []
    monkeypatch.setattr(rex_voice_bridge, "emit", events.append)
    tracker = rex_voice_bridge.WakeWordRuntimeTracker(
        configured_phrase="rex",
        configured_backend="custom_embedding",
        fallback_phrase="hey jarvis",
        microphone_label="USB Microphone",
        portaudio_device_index=4,
    )

    tracker.handle(
        {
            "level": "INFO",
            "message": "Wake-word attempt 1: rejected",
            "extra": {
                "event": "wakeword_attempt",
                "attempt": 1,
                "threshold": 0.5,
                "confidence": 0.2,
                "keyword": "rex",
                "backend": "custom_embedding",
                "accepted": False,
                "reject_reason": "below_threshold",
                "detector_generation": 1,
                "audio_rms": 0.001,
                "audio_peak": 0.004,
            },
        }
    )

    evidence_events = [
        event for event in events if event.get("type") == "wakeword_attempt_evidence"
    ]
    assert evidence_events[-1]["evidence"] == {
        "attempt_count": 1,
        "latest_confidence": 0.2,
        "max_confidence": 0.2,
        "threshold": 0.5,
        "audio_rms": 0.001,
        "audio_peak": 0.004,
        "reject_reason": "below_threshold",
        "active_phrase": "rex",
        "active_backend": "custom_embedding",
        "detector_generation": 1,
        "accepted": False,
        "microphone_label": "USB Microphone",
        "portaudio_device_index": 4,
    }


@pytest.mark.unit
def test_wakeword_runtime_tracker_updates_fallback_without_leaking_paths(monkeypatch):
    events: list[dict[str, object]] = []
    monkeypatch.setattr(rex_voice_bridge, "emit", events.append)
    tracker = rex_voice_bridge.WakeWordRuntimeTracker(
        configured_phrase="rex",
        configured_backend="custom_embedding",
        fallback_phrase="hey jarvis",
        microphone_label=None,
        portaudio_device_index=None,
    )

    tracker.handle(
        {
            "level": "INFO",
            "message": "Wake-word backend fallback activated",
            "extra": {
                "event": "wakeword_backend_fallback_activated",
                "fallback_keyword": "hey jarvis",
                "fallback_backend": "openwakeword",
                "detector_generation": 2,
                "original_keyword": "rex",
                "original_backend": "custom_embedding",
                "requested_model_path": "C:/private/models/rex.onnx",
            },
        }
    )

    runtime = [event for event in events if event.get("type") == "wakeword_runtime_status"][-1]
    assert runtime["runtime"]["active_phrase"] == "hey jarvis"
    assert runtime["runtime"]["active_backend"] == "openwakeword"
    assert runtime["runtime"]["fallback_active"] is True
    assert runtime["runtime"]["detector_generation"] == 2
    assert "private/models" not in json.dumps(events)
