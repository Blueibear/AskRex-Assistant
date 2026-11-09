"""Tests for US-077: XTTS/transformers compatibility shim and edge-tts fallback."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── doctor: check_xtts_transformers_compat ──────────────────────────────────


def test_check_xtts_no_tts_installed():
    """Returns INFO when Coqui TTS is not installed."""
    from rex.doctor import Status, check_xtts_transformers_compat

    with patch("rex.doctor.find_spec", return_value=None):
        result = check_xtts_transformers_compat()
    assert result.status == Status.INFO
    assert "not installed" in result.message.lower()


def test_check_xtts_no_transformers():
    """Returns WARNING when transformers is not installed."""
    from rex.doctor import Status, check_xtts_transformers_compat

    def _mock_find_spec(name: str) -> object:
        return MagicMock() if name == "TTS" else None

    with patch("rex.doctor.find_spec", side_effect=_mock_find_spec):
        result = check_xtts_transformers_compat()
    assert result.status == Status.WARNING
    assert "transformers" in result.message.lower()
    assert "transformers>=5.5.0,<6.0" in result.details


def test_check_xtts_beamsearch_native():
    """Returns OK when BeamSearchScorer is natively available."""
    from rex.doctor import Status, check_xtts_transformers_compat

    mock_tf = MagicMock()
    mock_tf.__version__ = "4.36.0"
    mock_tf.BeamSearchScorer = MagicMock()  # native presence

    with patch("rex.doctor.find_spec", return_value=MagicMock()):
        with patch.dict(sys.modules, {"transformers": mock_tf}):
            result = check_xtts_transformers_compat()
    assert result.status == Status.OK
    assert "compatible" in result.message.lower()


def test_check_xtts_beamsearch_restored_by_shim():
    """Returns OK when the shim successfully patches BeamSearchScorer."""
    from rex.doctor import Status, check_xtts_transformers_compat

    # Use SimpleNamespace so hasattr gives us control
    mock_tf = SimpleNamespace(__version__="4.40.0")  # no BeamSearchScorer yet

    def _apply_shim() -> None:
        mock_tf.BeamSearchScorer = MagicMock()

    with patch("rex.doctor.find_spec", return_value=MagicMock()):
        with patch.dict(sys.modules, {"transformers": mock_tf}):  # type: ignore[dict-item]
            with patch("rex.compat.ensure_transformers_compatibility", side_effect=_apply_shim):
                result = check_xtts_transformers_compat()
    assert result.status == Status.OK
    assert "shim" in result.message.lower()


def test_check_xtts_beamsearch_missing_after_shim():
    """Returns WARNING when BeamSearchScorer is still missing after shim."""
    from rex.doctor import Status, check_xtts_transformers_compat

    mock_tf = SimpleNamespace(__version__="4.40.0")  # no BeamSearchScorer

    with patch("rex.doctor.find_spec", return_value=MagicMock()):
        with patch.dict(sys.modules, {"transformers": mock_tf}):  # type: ignore[dict-item]
            with patch("rex.compat.ensure_transformers_compatibility"):  # no-op shim
                result = check_xtts_transformers_compat()
    assert result.status == Status.WARNING
    assert "missing" in result.message.lower() or "fallback" in result.message.lower()


# ── voice_loop: edge-tts fallback when XTTS init fails ──────────────────────


def test_speak_xtts_falls_back_to_edge_tts_on_init_failure():
    """_speak_xtts falls back to edge-tts when XTTS init returns False."""
    pytest.importorskip("numpy")
    from rex.voice_loop import TextToSpeech

    tts = TextToSpeech.__new__(TextToSpeech)
    tts._tts = None
    tts._xtts_init_error = "BeamSearchScorer not found in transformers"
    tts._language = "en"
    tts._default_speaker = None
    tts._tts_speed = 1.0
    tts._tts_output_device = None

    edge_calls: list[str] = []

    async def _fake_edge(text: str) -> None:
        edge_calls.append(text)

    with patch.object(tts, "_initialize_xtts", return_value=False):
        with patch.object(tts, "_speak_edge", side_effect=_fake_edge):
            asyncio.run(tts._speak_xtts("hello world", None))

    assert edge_calls == ["hello world"], "edge-tts fallback was not called"


def test_speak_xtts_fallback_logs_warning(caplog: pytest.LogCaptureFixture):
    """A warning is logged when XTTS init fails and edge-tts fallback is used."""
    pytest.importorskip("numpy")
    import logging

    from rex.voice_loop import TextToSpeech

    tts = TextToSpeech.__new__(TextToSpeech)
    tts._tts = None
    tts._xtts_init_error = "test error"
    tts._language = "en"
    tts._default_speaker = None
    tts._tts_speed = 1.0
    tts._tts_output_device = None

    with caplog.at_level(logging.WARNING):
        with patch.object(tts, "_initialize_xtts", return_value=False):
            with patch.object(tts, "_speak_edge", new_callable=AsyncMock):
                asyncio.run(tts._speak_xtts("test", None))

    assert any("falling back to edge-tts" in r.message for r in caplog.records)
