"""Regression coverage for canonical identity-boundary hardening (issue #303)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rex.calendar_accounts import CalendarIdentityError
from rex.calendar_accounts import require_user_id as calendar_user
from rex.email_accounts import EmailIdentityError
from rex.email_accounts import require_user_id as email_user
from rex.identity import get_user_profile, resolve_active_user, validate_user_id
from rex.memory_utils import append_history_entry, export_transcript, resolve_user_key
from rex.openclaw.identity_adapter import IdentityAdapter
from rex.response_cache import ResponseCache
from rex.user_facts import store as store_fact
from rex.voice_identity.embeddings_store import EmbeddingsStore
from rex.voice_identity.types import VoiceEmbedding


@pytest.mark.parametrize(
    "user_id",
    [
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        *[f"COM{number}" for number in range(1, 10)],
        *[f"LPT{number}" for number in range(1, 10)],
        "con.txt",
        "NUL.json",
        "aux.profile",
        "COM1.data",
        "lpt9.backup",
        "con.",
        "nul ",
        "COM1...",
        "LPT1 .txt",
    ],
)
def test_windows_reserved_user_ids_are_rejected_on_all_platforms(user_id: str) -> None:
    with pytest.raises(ValueError, match="Invalid user_id"):
        validate_user_id(user_id)


@pytest.mark.parametrize(
    "user_id",
    [
        "console",
        "contact",
        "null",
        "auxiliary",
        "com0",
        "com10",
        "lpt0",
        "lpt10",
        "company1",
        "connor",
        "james",
        "cole",
        "default",
    ],
)
def test_valid_near_matches_remain_accepted(user_id: str) -> None:
    assert validate_user_id(user_id) == user_id


def test_history_and_transcript_reject_instead_of_sanitizing_reserved_id(tmp_path) -> None:
    with pytest.raises(ValueError, match="Invalid user_id"):
        append_history_entry("CON", {"role": "user", "text": "hello"}, memory_root=tmp_path)
    with pytest.raises(ValueError, match="Invalid user_id"):
        export_transcript("CON", [], transcripts_dir=tmp_path / "transcripts")
    assert not list(tmp_path.rglob("*"))


def test_voice_embedding_save_rejects_reserved_id_before_creating_directory(tmp_path) -> None:
    store = EmbeddingsStore(tmp_path)
    embedding = VoiceEmbedding(vector=[1.0], model_id="test", sample_count=1, updated_at="")
    with pytest.raises(ValueError, match="Invalid user_id"):
        store.save("NUL", embedding)
    assert not list(tmp_path.iterdir())


def test_profile_facts_history_and_cache_reject_reserved_ids_before_access(tmp_path) -> None:
    from rex.history_store import HistoryStore

    with pytest.raises(ValueError, match="Invalid user_id"):
        get_user_profile("COM1", memory_dir=tmp_path)
    with pytest.raises(ValueError, match="Invalid user_id"):
        store_fact("LPT1", "key", "value", memory_root=tmp_path)
    history = HistoryStore(tmp_path / "history.db")
    with pytest.raises(ValueError, match="Invalid user_id"):
        history.load_history("AUX")
    cache = ResponseCache()
    with pytest.raises(ValueError, match="Invalid user_id"):
        cache.put("question", "answer", user_id="NUL")


def test_invalid_persisted_or_configured_identity_fails_closed(tmp_path) -> None:
    session_file = tmp_path / "session.json"
    session_file.write_text('{"active_user": "CON"}', encoding="utf-8")
    with patch("rex.identity._session_state_path", return_value=session_file):
        assert resolve_active_user(config={"runtime": {"active_user": "james"}}) is None
    session_file.unlink()
    with patch("rex.identity._session_state_path", return_value=session_file):
        assert resolve_active_user(config={"runtime": {"active_user": "COM1"}}) is None


def test_display_name_is_not_reinterpreted_as_a_user_id(tmp_path) -> None:
    profiles = {"james": {"name": "James Example"}}
    assert resolve_user_key("James Example", {}, profiles=profiles, memory_root=tmp_path) is None
    assert resolve_user_key("james", {}, profiles=profiles, memory_root=tmp_path) == "james"


def test_email_and_calendar_resolvers_reject_reserved_identity_before_lookup() -> None:
    with pytest.raises(EmailIdentityError):
        email_user("CON")
    with pytest.raises(CalendarIdentityError):
        calendar_user("LPT9")


def test_openclaw_adapter_missing_identity_never_becomes_rex() -> None:
    adapter = IdentityAdapter()
    with patch("rex.openclaw.identity_adapter.resolve_active_user", return_value=None):
        with pytest.raises(PermissionError, match="identity"):
            adapter.get_openclaw_user_key()


def test_openclaw_adapter_invalid_identity_never_becomes_default_or_rex() -> None:
    adapter = IdentityAdapter()
    with patch("rex.openclaw.identity_adapter.resolve_active_user", return_value=None):
        with pytest.raises(PermissionError, match="identity"):
            adapter.get_openclaw_user_key("CON")
