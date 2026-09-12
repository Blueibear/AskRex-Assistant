from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")

from rex.wakeword.listener import WakeWordListener  # noqa: E402
from rex.wakeword.utils import WakeWordDetectionResult  # noqa: E402


def _detector_for(keyword: str):
    def detector(_frame):
        return WakeWordDetectionResult(
            triggered=False,
            threshold=0.5,
            predictions={keyword: 0.0},
            confidence=0.0,
            keyword=keyword,
            reason="below_threshold",
        )

    return detector


@pytest.mark.unit
def test_wakeword_listener_emits_rebuild_event_to_callback():
    events: list[dict[str, object]] = []
    generation = 0

    def make_detector():
        nonlocal generation
        generation += 1
        label = f"rex-{generation}"
        return _detector_for(label), None, label

    detector, reset_detector, keyword = make_detector()
    listener = WakeWordListener(
        detector,
        reset_detector=reset_detector,
        rebuild_detector=make_detector,
        threshold=0.5,
        keyword=keyword,
        backend="custom_embedding",
        event_callback=events.append,
    )

    listener.reset(reason="accepted_wake")
    listener.reset(reason="post_interaction")

    rebuild_events = [
        event
        for event in events
        if isinstance(event.get("extra"), dict)
        and event["extra"].get("event") == "wakeword_detector_rebuilt"
    ]
    assert rebuild_events
    assert rebuild_events[-1]["extra"]["keyword"] == "rex-2"
    assert rebuild_events[-1]["extra"]["backend"] == "custom_embedding"
    assert rebuild_events[-1]["extra"]["detector_generation"] == 2


@pytest.mark.unit
def test_wakeword_listener_emits_sanitized_rebuild_failure_to_callback(caplog):
    events: list[dict[str, object]] = []

    def rebuild_detector():
        raise RuntimeError("C:/private/models/rex.onnx")

    listener = WakeWordListener(
        _detector_for("rex"),
        rebuild_detector=rebuild_detector,
        threshold=0.5,
        keyword="rex",
        backend="custom_embedding",
        event_callback=events.append,
    )

    listener.reset(reason="accepted_wake")
    listener.reset(reason="post_interaction")

    failure_events = [
        event
        for event in events
        if isinstance(event.get("extra"), dict)
        and event["extra"].get("event") == "wakeword_detector_rebuild_failed"
    ]
    assert failure_events
    assert failure_events[-1]["extra"]["backend"] == "custom_embedding"
    assert failure_events[-1]["extra"]["detector_generation"] == 1
    assert "private/models" not in json.dumps(failure_events)
    assert "private/models" not in caplog.text
