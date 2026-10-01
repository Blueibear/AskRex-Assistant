"""Tests for the memory and knowledge base CLI commands."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

import rex.memory
from rex.cli import _parse_ttl, cmd_kb, cmd_memory, main
from rex.knowledge_base import KnowledgeBase, set_knowledge_base
from rex.memory import (
    LongTermMemory,
    WorkingMemory,
    set_long_term_memory,
    set_working_memory,
)


@pytest.fixture(autouse=True)
def _isolated_registries(tmp_path, monkeypatch):
    """Keep per-user registries and the data dir isolated from the real ones."""
    monkeypatch.setattr(rex.memory, "_DATA_DIR", tmp_path / "memdata")
    monkeypatch.setattr(rex.memory, "_working_memories", {})
    monkeypatch.setattr(rex.memory, "_long_term_memories", {})


# =============================================================================
# TTL Parsing Tests
# =============================================================================


class TestParseTTL:
    """Tests for the TTL parsing utility."""

    def test_parse_days(self):
        """Test parsing days."""
        result = _parse_ttl("7d")
        assert result == timedelta(days=7)

    def test_parse_hours(self):
        """Test parsing hours."""
        result = _parse_ttl("24h")
        assert result == timedelta(hours=24)

    def test_parse_minutes(self):
        """Test parsing minutes."""
        result = _parse_ttl("30m")
        assert result == timedelta(minutes=30)

    def test_parse_seconds(self):
        """Test parsing seconds."""
        result = _parse_ttl("10s")
        assert result == timedelta(seconds=10)

    def test_parse_weeks(self):
        """Test parsing weeks."""
        result = _parse_ttl("2w")
        assert result == timedelta(weeks=2)

    def test_parse_bare_number_as_days(self):
        """Test parsing bare number as days."""
        result = _parse_ttl("5")
        assert result == timedelta(days=5)

    def test_parse_invalid(self):
        """Test parsing invalid TTL."""
        result = _parse_ttl("invalid")
        assert result is None

    def test_parse_empty(self):
        """Test parsing empty string."""
        result = _parse_ttl("")
        assert result is None


# =============================================================================
# Memory CLI Tests
# =============================================================================


class TestMemoryCLI:
    """Tests for memory CLI commands."""

    def test_memory_recent(self, tmp_path, capsys):
        """Test 'rex memory recent' command."""
        storage = tmp_path / "wm.json"
        wm = WorkingMemory(storage_path=storage)
        wm.add_entry("Entry 1")
        wm.add_entry("Entry 2")
        set_working_memory(wm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "recent"
        args.count = 5

        result = cmd_memory(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Entry 1" in captured.out
        assert "Entry 2" in captured.out

    def test_memory_recent_empty(self, tmp_path, capsys):
        """Test 'rex memory recent' with no entries."""
        storage = tmp_path / "wm.json"
        wm = WorkingMemory(storage_path=storage)
        set_working_memory(wm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "recent"
        args.count = 5

        result = cmd_memory(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "No working memory entries" in captured.out

    def test_memory_add(self, tmp_path, capsys):
        """Test 'rex memory add' command."""
        storage = tmp_path / "ltm.json"
        ltm = LongTermMemory(storage_path=storage)
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "add"
        args.category = "test"
        args.content = '{"key": "value"}'
        args.ttl = None
        args.sensitive = False

        result = cmd_memory(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Added memory entry" in captured.out
        assert "test" in captured.out

    def test_memory_add_with_ttl(self, tmp_path, capsys):
        """Test 'rex memory add' with TTL."""
        storage = tmp_path / "ltm.json"
        ltm = LongTermMemory(storage_path=storage)
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "add"
        args.category = "temp"
        args.content = '{"temp": true}'
        args.ttl = "7d"
        args.sensitive = False

        result = cmd_memory(args)
        assert result == 0

        # Verify entry has expiration
        entries = ltm.search(category="temp")
        assert len(entries) == 1
        assert entries[0].expires_at is not None

    def test_memory_add_sensitive(self, tmp_path, capsys):
        """Test 'rex memory add' with sensitive flag."""
        storage = tmp_path / "ltm.json"
        ltm = LongTermMemory(storage_path=storage)
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "add"
        args.category = "secrets"
        args.content = '{"api_key": "secret"}'  # pragma: allowlist secret
        args.ttl = None
        args.sensitive = True

        result = cmd_memory(args)
        assert result == 0

        entries = ltm.search(category="secrets")
        assert len(entries) == 1
        assert entries[0].sensitive is True

    def test_memory_add_invalid_json(self, tmp_path, capsys):
        """Test 'rex memory add' with invalid JSON."""
        storage = tmp_path / "ltm.json"
        ltm = LongTermMemory(storage_path=storage)
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "add"
        args.category = "test"
        args.content = "not valid json"
        args.ttl = None
        args.sensitive = False

        result = cmd_memory(args)
        assert result == 1

        captured = capsys.readouterr()
        assert "Invalid JSON" in captured.out

    def test_memory_add_invalid_ttl(self, tmp_path, capsys):
        """Test 'rex memory add' with invalid TTL."""
        storage = tmp_path / "ltm.json"
        ltm = LongTermMemory(storage_path=storage)
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "add"
        args.category = "test"
        args.content = '{"key": "value"}'
        args.ttl = "invalid"
        args.sensitive = False

        result = cmd_memory(args)
        assert result == 1

        captured = capsys.readouterr()
        assert "Invalid TTL" in captured.out

    def test_memory_search(self, tmp_path, capsys):
        """Test 'rex memory search' command."""
        storage = tmp_path / "ltm.json"
        ltm = LongTermMemory(storage_path=storage)
        ltm.add_entry(category="facts", content={"topic": "weather"})
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "search"
        args.keyword = "weather"
        args.category = None
        args.show_sensitive = False

        result = cmd_memory(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "weather" in captured.out

    def test_memory_search_by_category(self, tmp_path, capsys):
        """Test 'rex memory search' by category."""
        storage = tmp_path / "ltm.json"
        ltm = LongTermMemory(storage_path=storage)
        ltm.add_entry(category="preferences", content={"theme": "dark"})
        ltm.add_entry(category="facts", content={"fact": "test"})
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "search"
        args.keyword = None
        args.category = "preferences"
        args.show_sensitive = False

        result = cmd_memory(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "preferences" in captured.out
        assert "theme" in captured.out

    def test_memory_search_hides_sensitive(self, tmp_path, capsys):
        """Test 'rex memory search' hides sensitive content."""
        storage = tmp_path / "ltm.json"
        ltm = LongTermMemory(storage_path=storage)
        ltm.add_entry(
            category="secrets",
            content={"password": "secret123"},  # pragma: allowlist secret
            sensitive=True,
        )
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "search"
        args.keyword = "password"
        args.category = None
        args.show_sensitive = False

        result = cmd_memory(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "secret123" not in captured.out
        assert "hidden" in captured.out or "SENSITIVE" in captured.out

    def test_memory_search_shows_sensitive_with_flag(self, tmp_path, capsys):
        """Test 'rex memory search' shows sensitive with flag."""
        storage = tmp_path / "ltm.json"
        ltm = LongTermMemory(storage_path=storage)
        ltm.add_entry(
            category="secrets",
            content={"password": "secret123"},
            sensitive=True,
        )
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "search"
        args.keyword = "password"
        args.category = None
        args.show_sensitive = True

        result = cmd_memory(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "secret123" in captured.out

    def test_memory_forget(self, tmp_path, capsys):
        """Test 'rex memory forget' command."""
        storage = tmp_path / "ltm.json"
        ltm = LongTermMemory(storage_path=storage)
        entry = ltm.add_entry(category="temp", content={"temp": True})
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "forget"
        args.entry_id = entry.entry_id

        result = cmd_memory(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Deleted" in captured.out

    def test_memory_forget_not_found(self, tmp_path, capsys):
        """Test 'rex memory forget' with non-existent entry."""
        storage = tmp_path / "ltm.json"
        ltm = LongTermMemory(storage_path=storage)
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "forget"
        args.entry_id = "nonexistent"

        result = cmd_memory(args)
        assert result == 1

        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_memory_clear(self, tmp_path, capsys):
        """Test 'rex memory clear' command."""
        storage = tmp_path / "wm.json"
        wm = WorkingMemory(storage_path=storage)
        wm.add_entry("Entry 1")
        wm.add_entry("Entry 2")
        set_working_memory(wm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "clear"

        result = cmd_memory(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Cleared" in captured.out
        assert len(wm) == 0

    def test_memory_retention(self, tmp_path, capsys):
        """Test 'rex memory retention' command."""
        storage = tmp_path / "ltm.json"
        ltm = LongTermMemory(storage_path=storage)
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "retention"

        result = cmd_memory(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Retention policy" in captured.out

    def test_memory_stats(self, tmp_path, capsys):
        """Test 'rex memory stats' command."""
        wm_storage = tmp_path / "wm.json"
        ltm_storage = tmp_path / "ltm.json"
        wm = WorkingMemory(storage_path=wm_storage)
        ltm = LongTermMemory(storage_path=ltm_storage)
        wm.add_entry("Working memory entry")
        ltm.add_entry(category="facts", content={"fact": 1})
        set_working_memory(wm, user_id="alice")
        set_long_term_memory(ltm, user_id="alice")

        args = MagicMock()
        args.user = "alice"
        args.memory_command = "stats"

        result = cmd_memory(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Working Memory: 1" in captured.out
        assert "Long-Term Memory: 1" in captured.out


# =============================================================================
# Knowledge Base CLI Tests
# =============================================================================


class TestKBCLI:
    """Tests for knowledge base CLI commands."""

    def test_kb_ingest(self, tmp_path, capsys):
        """Test 'rex kb ingest' command."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        set_knowledge_base(kb)

        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content for ingestion.")

        args = MagicMock()
        args.kb_command = "ingest"
        args.path = str(test_file)
        args.title = "Test Document"
        args.tags = "test,docs"

        result = cmd_kb(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Ingested document" in captured.out
        assert "Test Document" in captured.out

    def test_kb_ingest_not_found(self, tmp_path, capsys):
        """Test 'rex kb ingest' with non-existent file."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "ingest"
        args.path = str(tmp_path / "nonexistent.txt")
        args.title = None
        args.tags = None

        result = cmd_kb(args)
        assert result == 1

        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_kb_search(self, tmp_path, capsys):
        """Test 'rex kb search' command."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        kb.ingest_text(content="Weather is sunny today.", title="Weather Report")
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "search"
        args.query = "weather"
        args.max_results = 5
        args.verbose = False

        result = cmd_kb(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Weather Report" in captured.out

    def test_kb_search_verbose(self, tmp_path, capsys):
        """Test 'rex kb search' with verbose flag."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        kb.ingest_text(content="Weather is sunny today.", title="Weather Report")
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "search"
        args.query = "weather"
        args.max_results = 5
        args.verbose = True

        result = cmd_kb(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Snippet" in captured.out

    def test_kb_search_no_results(self, tmp_path, capsys):
        """Test 'rex kb search' with no matches."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "search"
        args.query = "nonexistent"
        args.max_results = 5
        args.verbose = False

        result = cmd_kb(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "No matching documents" in captured.out

    def test_kb_list(self, tmp_path, capsys):
        """Test 'rex kb list' command."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        kb.ingest_text(content="Doc 1", title="First")
        kb.ingest_text(content="Doc 2", title="Second")
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "list"
        args.limit = 10

        result = cmd_kb(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "First" in captured.out
        assert "Second" in captured.out

    def test_kb_list_empty(self, tmp_path, capsys):
        """Test 'rex kb list' with no documents."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "list"
        args.limit = 10

        result = cmd_kb(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "No documents" in captured.out

    def test_kb_show(self, tmp_path, capsys):
        """Test 'rex kb show' command."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        doc = kb.ingest_text(content="Full content here.", title="Test Doc")
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "show"
        args.doc_id = doc.doc_id

        result = cmd_kb(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Test Doc" in captured.out
        assert "Full content here." in captured.out

    def test_kb_show_not_found(self, tmp_path, capsys):
        """Test 'rex kb show' with non-existent document."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "show"
        args.doc_id = "nonexistent"

        result = cmd_kb(args)
        assert result == 1

        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_kb_delete(self, tmp_path, capsys):
        """Test 'rex kb delete' command."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        doc = kb.ingest_text(content="To delete", title="Delete Me")
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "delete"
        args.doc_id = doc.doc_id

        result = cmd_kb(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Deleted" in captured.out

    def test_kb_delete_not_found(self, tmp_path, capsys):
        """Test 'rex kb delete' with non-existent document."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "delete"
        args.doc_id = "nonexistent"

        result = cmd_kb(args)
        assert result == 1

        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_kb_cite(self, tmp_path, capsys):
        """Test 'rex kb cite' command."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        kb.ingest_text(content="The deadline is next week.", title="Project")
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "cite"
        args.query = "deadline"

        result = cmd_kb(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Project" in captured.out

    def test_kb_cite_no_results(self, tmp_path, capsys):
        """Test 'rex kb cite' with no matches."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "cite"
        args.query = "nonexistent"

        result = cmd_kb(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "No citations" in captured.out

    def test_kb_tags(self, tmp_path, capsys):
        """Test 'rex kb tags' command."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        kb.ingest_text(content="Doc 1", title="First", tags=["alpha", "beta"])
        kb.ingest_text(content="Doc 2", title="Second", tags=["gamma"])
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "tags"

        result = cmd_kb(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "alpha" in captured.out
        assert "beta" in captured.out
        assert "gamma" in captured.out

    def test_kb_tags_empty(self, tmp_path, capsys):
        """Test 'rex kb tags' with no tags."""
        docs_path = tmp_path / "docs.json"
        index_path = tmp_path / "index.json"
        kb = KnowledgeBase(docs_path=docs_path, index_path=index_path)
        set_knowledge_base(kb)

        args = MagicMock()
        args.kb_command = "tags"

        result = cmd_kb(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "No tags" in captured.out


# =============================================================================
# Integration Tests via main()
# =============================================================================


class TestCLIIntegration:
    """Integration tests using the main() function."""

    def test_memory_help(self, capsys):
        """Test 'rex memory --help' works."""
        with pytest.raises(SystemExit) as exc_info:
            main(["memory", "--help"])
        assert exc_info.value.code == 0

    def test_kb_help(self, capsys):
        """Test 'rex kb --help' works."""
        with pytest.raises(SystemExit) as exc_info:
            main(["kb", "--help"])
        assert exc_info.value.code == 0
