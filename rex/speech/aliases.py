"""AskRex-owned voice identity aliases (S35).

Canonical voice identities such as ``majel``, ``james``, and ``cole`` are
AskRex application concepts, not provider IDs. This module is the single
place that maps an alias to a provider-specific voice ID; provider/tool code
elsewhere must never hard-code a VoiceStudio/OpenAI/ElevenLabs voice ID.

Per-provider mappings are intentionally sparse: a provider without an
explicit mapping for an alias simply has no alias resolution for it, and the
caller falls back to that provider's own default/explicit voice selection.
"""

from __future__ import annotations

VOICE_ALIASES: dict[str, dict[str, str]] = {
    "majel": {
        "edge-tts": "en-US-AriaNeural",
    },
    "james": {
        "edge-tts": "en-US-AndrewNeural",
    },
    "cole": {
        "edge-tts": "en-US-GuyNeural",
    },
}


def known_aliases() -> list[str]:
    """Return the canonical AskRex voice alias names."""
    return sorted(VOICE_ALIASES)


def resolve_alias(alias: str | None, provider_id: str) -> str | None:
    """Resolve ``alias`` to a provider-specific voice ID.

    Returns ``None`` when ``alias`` is not a known AskRex voice identity, or
    when the given provider has no explicit mapping for it -- callers must
    fall back to their own default/explicit voice resolution rather than
    treating that as an error.
    """
    if not alias:
        return None
    per_provider = VOICE_ALIASES.get(alias.strip().lower())
    if per_provider is None:
        return None
    return per_provider.get(provider_id)


__all__ = ["VOICE_ALIASES", "known_aliases", "resolve_alias"]
