"""Canonical interface-agnostic turn runtime contracts."""

from rex.runtime.cancellation import (
    TurnCancellation,
    TurnCancelledError,
    await_with_cancellation,
    current_turn_cancellation,
    turn_cancellation_scope,
)
from rex.runtime.events import EventKind, TerminalStateError, TurnEvent, TurnEventStream
from rex.runtime.invocation import TurnInvocation, current_turn_invocation, turn_invocation
from rex.runtime.speculation import (
    PrefetchHandle,
    SpeculationBudget,
    SpeculationOutcome,
    SpeculativeAttempt,
    SpeculativePrefetcher,
    SpeculativeResult,
)
from rex.runtime.turn import (
    AuthorizationSnapshotRef,
    ResponseMode,
    TurnContext,
    TurnScope,
    TurnSource,
)
from rex.runtime.turn_engine import TurnEngine

__all__ = [
    "AuthorizationSnapshotRef",
    "EventKind",
    "PrefetchHandle",
    "ResponseMode",
    "SpeculationBudget",
    "SpeculationOutcome",
    "SpeculativeAttempt",
    "SpeculativePrefetcher",
    "SpeculativeResult",
    "TerminalStateError",
    "TurnCancellation",
    "TurnCancelledError",
    "TurnContext",
    "await_with_cancellation",
    "TurnInvocation",
    "TurnEngine",
    "TurnEvent",
    "TurnEventStream",
    "TurnScope",
    "TurnSource",
    "current_turn_cancellation",
    "current_turn_invocation",
    "turn_cancellation_scope",
    "turn_invocation",
]
