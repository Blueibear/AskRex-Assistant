"""Tests for the memory utility helpers."""

from __future__ import annotations

import pytest

import rex.config as config
from rex.memory_utils import (
    append_history_entry,
    export_transcript,
    extract_voice_reference,
    load_recent_history,
    load_users_map,
    resolve_user_key,
    trim_history,
)


@pytest.mark.unit
def test_resolve_user_key_from_email(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text('{"james@example.com": "james"}', encoding="utf-8")
    users_map = load_users_map(users_path)
    profiles = {}

    result = resolve_user_key("james@example.com", users_map, profiles=profiles)
    assert result == "james", "Failed to resolve correct user key from email"


@pytest.mark.unit
def test_extract_voice_reference_handles_absolute_and_relative(tmp_path):
    # Absolute voice path
    absolute_voice = tmp_path / "voice.wav"
    absolute_voice.write_bytes(b"fake")
    profile = {"voice": {"sample_path": str(absolute_voice)}}
    assert extract_voice_reference(profile) == str(absolute_voice)

    # Relative voice path in user dir
    user_dir = tmp_path / "james"
    user_dir.mkdir()
    relative_voice = user_dir / "voice.wav"
    relative_voice.write_bytes(b"demo")
    profile_relative = {"voice_sample": "voice.wav"}
    result = extract_voice_reference(
        profile_relative,
        user_key="james",
        memory_root=str(tmp_path),
    )
    assert result == str(relative_voice.resolve())

    # Missing or malformed profile
    profile_missing = {"voice": {}}
    assert (
        extract_voice_reference(
            profile_missing,
            user_key="james",
            memory_root=str(tmp_path),
        )
        is None
    )


@pytest.mark.unit
def test_trim_history_limits_entries():
    history = [{"id": i} for i in range(10)]
    trimmed = trim_history(history, limit=3)
    assert len(trimmed) == 3
    assert trimmed[0]["id"] == 7
    assert trimmed[-1]["id"] == 9


@pytest.mark.unit
def test_append_history_trims(monkeypatch, tmp_path):
    cfg = config.AppConfig(memory_max_turns=2, transcripts_dir=tmp_path / "transcripts")
    monkeypatch.setattr(config, "_cached_config", cfg, raising=False)

    for idx in range(4):
        append_history_entry(
            "tester",
            {"role": "user", "text": f"utterance {idx}"},
            memory_root=tmp_path,
        )

    entries = load_recent_history("tester", memory_root=tmp_path)
    assert len(entries) == 2
    assert entries[0]["text"] == "utterance 2"
    assert entries[1]["text"] == "utterance 3"


@pytest.mark.unit
def test_export_transcript_appends(monkeypatch, tmp_path):
    transcripts_dir = tmp_path / "transcripts"
    cfg = config.AppConfig(transcripts_dir=transcripts_dir)
    monkeypatch.setattr(config, "_cached_config", cfg, raising=False)

    conversation = [
        {"role": "user", "text": "Hello"},
        {"role": "assistant", "text": "Hi there"},
    ]

    path = export_transcript("tester", conversation, transcripts_dir=transcripts_dir)
    assert path.exists()
    contents = path.read_text(encoding="utf-8")
    assert "user: Hello" in contents
    assert "assistant: Hi there" in contents


@pytest.mark.unit
def test_export_transcript_creates_directory(monkeypatch, tmp_path):
    transcripts_dir = tmp_path / "not_yet_there"
    cfg = config.AppConfig(transcripts_dir=transcripts_dir)
    monkeypatch.setattr(config, "_cached_config", cfg, raising=False)

    conversation = [{"role": "assistant", "text": "Testing export..."}]
    path = export_transcript("tester", conversation, transcripts_dir=transcripts_dir)

    assert path.exists()
    assert path.parent.exists()


def test_load_recent_history_with_missing_file(tmp_path):
    entries = load_recent_history("ghost", memory_root=tmp_path)
    assert isinstance(entries, list)
    assert len(entries) == 0
