"""Permission-aware hybrid retrieval over canonical Capability Tool Cards."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .registry import Capability, CapabilityRegistry

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "be",
        "can",
        "could",
        "for",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "that",
        "the",
        "this",
        "to",
        "with",
        "would",
        "you",
    }
)
_BLOCKED_HEALTH = frozenset({"unhealthy", "unavailable"})
_BLOCKED_INTEGRATION_STATES = frozenset({"unavailable", "unconfigured"})
_DEFAULT_ALLOWED_RISKS = frozenset({"safe", "sensitive"})
_LEXICAL_WEIGHT = 0.4
_SEMANTIC_WEIGHT = 0.6
_DEFAULT_SEMANTIC = object()

# Small, dependency-free concept groups provide a local semantic signal without
# network calls, paid embeddings, model downloads, or private payload indexing.
_CONCEPT_GROUPS: tuple[frozenset[str], ...] = (
    frozenset(
        {"search", "lookup", "find", "research", "browse", "web", "online", "internet", "google"}
    ),
    frozenset({"weather", "forecast", "temperature", "rain", "snow", "storm", "humidity", "wind"}),
    frozenset({"email", "mail", "inbox", "message", "compose"}),
    frozenset({"sms", "text", "message", "phone"}),
    frozenset({"calendar", "schedule", "meeting", "appointment", "event", "agenda"}),
    frozenset({"home", "smart", "light", "lights", "thermostat", "lock", "garage", "device"}),
    frozenset({"music", "song", "audio", "playback", "play", "pause", "track"}),
    frozenset({"file", "files", "folder", "document", "filesystem", "directory"}),
    frozenset({"computer", "pc", "machine", "windows", "system", "desktop"}),
    frozenset({"time", "date", "clock", "today", "timezone"}),
)


class SemanticScorer(Protocol):
    """Local semantic signal used to augment deterministic lexical ranking."""

    def score(self, query: str, capability: Capability) -> float:
        """Return a normalized score in the inclusive range 0..1."""


@dataclass(frozen=True)
class CapabilityMatch:
    """Inspectable retrieval evidence without private request payloads."""

    capability: Capability
    score: float
    lexical_score: float
    semantic_score: float
    reasons: tuple[str, ...]


class LocalConceptSemanticScorer:
    """Dependency-free local semantic scorer based on stable concept groups."""

    def score(self, query: str, capability: Capability) -> float:
        query_concepts = _concepts(_tokens(query))
        if not query_concepts:
            return 0.0
        card_concepts = _concepts(_tokens(_card_text(capability)))
        if not card_concepts:
            return 0.0
        overlap = len(query_concepts & card_concepts)
        return overlap / len(query_concepts)


class CapabilityRetriever:
    """Filter canonical Tool Cards for authority/health, then hybrid-rank them."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        config: Any = None,
        semantic_scorer: SemanticScorer | None | object = _DEFAULT_SEMANTIC,
        allowed_risks: frozenset[str] = _DEFAULT_ALLOWED_RISKS,
        candidate_filter: Callable[[Capability], bool] | None = None,
    ) -> None:
        self._registry = registry
        self._config = config
        self._semantic_scorer: SemanticScorer | None
        if semantic_scorer is _DEFAULT_SEMANTIC:
            self._semantic_scorer = LocalConceptSemanticScorer()
        else:
            self._semantic_scorer = semantic_scorer  # type: ignore[assignment]
        self._allowed_risks = allowed_risks
        self._candidate_filter = candidate_filter

    def retrieve(
        self,
        query: str,
        *,
        user_id: str | None = None,
        granted_permissions: set[str] | frozenset[str] | None = None,
        limit: int = 5,
    ) -> list[CapabilityMatch]:
        """Return a small ranked set after fail-closed authorization filtering."""
        if limit <= 0:
            return []
        permissions = self._resolve_permissions(user_id, granted_permissions)
        candidates = [
            card
            for card in self._registry.list(include_disabled=True)
            if self._candidate_allowed(card, user_id=user_id, permissions=permissions)
        ]

        lexical_scores = {card.id: _lexical_score(query, card) for card in candidates}
        semantic_scores, semantic_available = self._semantic_scores(query, candidates)

        matches: list[CapabilityMatch] = []
        for card in candidates:
            lexical = lexical_scores[card.id]
            semantic = semantic_scores[card.id]
            if lexical <= 0.0 and semantic <= 0.0:
                continue
            score = _round_score(
                _LEXICAL_WEIGHT * lexical + _SEMANTIC_WEIGHT * semantic
                if semantic_available
                else lexical
            )
            reasons: list[str] = []
            if lexical > 0:
                reasons.append("lexical")
            if semantic > 0:
                reasons.append("semantic")
            if card.health == "degraded":
                reasons.append("health:degraded")
            matches.append(
                CapabilityMatch(
                    capability=card,
                    score=score,
                    lexical_score=_round_score(lexical),
                    semantic_score=_round_score(semantic),
                    reasons=tuple(reasons),
                )
            )

        matches.sort(key=lambda match: (-match.score, match.capability.id))
        return matches[:limit]

    def retrieve_lexical_only(
        self,
        query: str,
        *,
        user_id: str | None = None,
        granted_permissions: set[str] | frozenset[str] | None = None,
        limit: int = 5,
    ) -> list[CapabilityMatch]:
        """Fast, semantic-free ranking usable before full hybrid retrieval resolves.

        Applies the identical authority/health filtering as :meth:`retrieve`
        but skips local semantic scoring, so it is cheap enough to run first
        and drive speculative candidate selection while the authoritative
        :meth:`retrieve` ranking is still computing. The final tool selection
        must always use :meth:`retrieve`; this method never determines which
        capability is actually used, only which ones may be spoken for
        speculatively in the meantime.
        """
        if limit <= 0:
            return []
        permissions = self._resolve_permissions(user_id, granted_permissions)
        candidates = [
            card
            for card in self._registry.list(include_disabled=True)
            if self._candidate_allowed(card, user_id=user_id, permissions=permissions)
        ]
        matches: list[CapabilityMatch] = []
        for card in candidates:
            lexical = _lexical_score(query, card)
            if lexical <= 0.0:
                continue
            matches.append(
                CapabilityMatch(
                    capability=card,
                    score=_round_score(lexical),
                    lexical_score=_round_score(lexical),
                    semantic_score=0.0,
                    reasons=("lexical",),
                )
            )
        matches.sort(key=lambda match: (-match.score, match.capability.id))
        return matches[:limit]

    def _resolve_permissions(
        self,
        user_id: str | None,
        granted_permissions: set[str] | frozenset[str] | None,
    ) -> frozenset[str]:
        if granted_permissions is not None:
            return frozenset(granted_permissions)
        if not user_id:
            return frozenset()
        try:
            from rex.permissions import get_permissions  # noqa: PLC0415

            return frozenset(get_permissions(user_id))
        except Exception:
            logger.exception(
                "capability_retrieval: failed to resolve permissions for user %r", user_id
            )
            return frozenset()

    def _candidate_allowed(
        self,
        card: Capability,
        *,
        user_id: str | None,
        permissions: frozenset[str],
    ) -> bool:
        if self._candidate_filter is not None and not self._candidate_filter(card):
            return False
        if not self._effectively_enabled(card):
            return False
        if card.integration_state in _BLOCKED_INTEGRATION_STATES:
            return False
        if card.health in _BLOCKED_HEALTH:
            return False
        if card.risk not in self._allowed_risks:
            return False
        if card.requires_identity and not user_id:
            return False
        return self._registry.is_authorized(card.id, permissions)

    def _effectively_enabled(self, card: Capability) -> bool:
        if card.enabled:
            return True
        if not card.requires_config or self._config is None:
            return False
        return all(bool(getattr(self._config, key, None)) for key in card.requires_config)

    def _semantic_scores(
        self, query: str, candidates: list[Capability]
    ) -> tuple[dict[str, float], bool]:
        if self._semantic_scorer is None:
            return {card.id: 0.0 for card in candidates}, False
        try:
            scores = {
                card.id: _bounded_score(self._semantic_scorer.score(query, card))
                for card in candidates
            }
        except Exception:
            logger.warning(
                "capability_retrieval: local semantic signal failed; using lexical fallback"
            )
            return {card.id: 0.0 for card in candidates}, False
        return scores, True


def _card_text(card: Capability) -> str:
    return " ".join(
        (
            card.id.replace("_", " "),
            card.description,
            card.category,
            *card.triggers,
            *card.examples,
        )
    )


def _tokens(text: str) -> frozenset[str]:
    return frozenset(
        token
        for token in _TOKEN_RE.findall(text.casefold().replace("_", " "))
        if token not in _STOPWORDS
    )


def _lexical_score(query: str, card: Capability) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    card_tokens = _tokens(_card_text(card))
    if not card_tokens:
        return 0.0
    overlap = len(query_tokens & card_tokens)
    return overlap / len(query_tokens)


def _concepts(tokens: frozenset[str]) -> frozenset[int]:
    return frozenset(
        index for index, group in enumerate(_CONCEPT_GROUPS) if not tokens.isdisjoint(group)
    )


def _bounded_score(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _round_score(value: float) -> float:
    return round(_bounded_score(value), 6)


__all__ = [
    "CapabilityMatch",
    "CapabilityRetriever",
    "LocalConceptSemanticScorer",
    "SemanticScorer",
]
