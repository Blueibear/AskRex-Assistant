from dataclasses import dataclass, replace

from .alerts import AlertSink


@dataclass(frozen=True)
class UsageBudget:
    banked_resets_remaining: int = 3
    reserve_last_reset: bool = True

    def with_confirmed_remaining(self, remaining: int) -> "UsageBudget":
        if remaining < 0:
            raise ValueError("confirmed reset count cannot be negative")
        if remaining > self.banked_resets_remaining:
            raise ValueError("confirmed reset count cannot increase")
        return replace(self, banked_resets_remaining=remaining)


@dataclass(frozen=True)
class UsageDecision:
    action: str
    blocked_user: bool
    budget: UsageBudget


def handle_usage_limit(
    budget: UsageBudget,
    sink: AlertSink,
    *,
    role: str,
    provider: str,
    reason: str,
    claude_available: bool,
) -> UsageDecision:
    if provider == "codex" and claude_available:
        return UsageDecision("fallback_claude", False, budget)

    if provider == "claude":
        sink.emit(
            role=role,
            kind="usage_limit",
            message=f"{role} is blocked because Claude usage is unavailable: {reason}. Wait for Claude allowance to reset or intervene manually.",
        )
        return UsageDecision("wait_for_claude", True, budget)

    if provider != "codex":
        raise ValueError(f"unsupported usage provider: {provider}")

    if budget.banked_resets_remaining <= 0:
        sink.emit(
            role=role,
            kind="usage_limit",
            message=f"{role} is blocked by Codex usage with no banked resets remaining: {reason}.",
        )
        return UsageDecision("wait_for_codex", True, budget)

    if budget.reserve_last_reset and budget.banked_resets_remaining == 1:
        sink.emit(
            role=role,
            kind="usage_reset",
            message=f"{role} hit the Codex usage limit ({reason}). The final banked reset is reserved; James must explicitly override that reserve to use it.",
        )
        return UsageDecision("preserve_last_reset", True, budget)

    sink.emit(
        role=role,
        kind="usage_reset",
        message=f"{role} hit the Codex usage limit ({reason}). {budget.banked_resets_remaining} banked resets are recorded as available. Apply one reset manually if you want this workstream to resume now; the supervisor will not decrement the count until the platform-confirmed remaining count is recorded.",
    )
    return UsageDecision("request_reset", True, budget)
