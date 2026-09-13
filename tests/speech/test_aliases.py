"""AskRex-owned voice alias resolution tests (S35)."""

from __future__ import annotations

from rex.speech.aliases import known_aliases, resolve_alias


class TestResolveAlias:
    def test_known_alias_resolves_for_mapped_provider(self) -> None:
        assert resolve_alias("majel", "edge-tts") == "en-US-AriaNeural"
        assert resolve_alias("james", "edge-tts") == "en-US-AndrewNeural"
        assert resolve_alias("cole", "edge-tts") == "en-US-GuyNeural"

    def test_alias_is_case_insensitive(self) -> None:
        assert resolve_alias("MAJEL", "edge-tts") == "en-US-AriaNeural"

    def test_unknown_alias_returns_none(self) -> None:
        assert resolve_alias("not-a-real-alias", "edge-tts") is None

    def test_known_alias_without_provider_mapping_returns_none(self) -> None:
        assert resolve_alias("majel", "some-unmapped-provider") is None

    def test_none_or_empty_alias_returns_none(self) -> None:
        assert resolve_alias(None, "edge-tts") is None
        assert resolve_alias("", "edge-tts") is None

    def test_known_aliases_are_stable_and_sorted(self) -> None:
        assert known_aliases() == ["cole", "james", "majel"]
