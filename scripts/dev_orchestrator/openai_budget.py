from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

from .lifecycle import ControlPlaneLock
from .storage import AtomicJsonStore

CONSERVATIVE_PRICE_PER_MILLION = {
    "gpt-5.6-terra": (Decimal("2.50"), Decimal("12.00")),
    "gpt-5.6-sol": (Decimal("5.00"), Decimal("20.00")),
    "gpt-6-astra": (Decimal("12.50"), Decimal("50.00")),
}
LONG_CONTEXT_THRESHOLD_TOKENS = 272_000
LONG_INPUT_MULTIPLIER = Decimal("2")
LONG_OUTPUT_MULTIPLIER = Decimal("1.5")
HARD_MONTHLY_CAP_USD = Decimal("30.00")
_MILLION = Decimal("1000000")


class OpenAIBudgetPolicyError(ValueError):
    pass


class OpenAIBudgetExceeded(OpenAIBudgetPolicyError):
    pass


@dataclass(frozen=True)
class OpenAIUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    month: str
    model: str
    reserved_usd: Decimal


class OpenAIBudgetLedger:
    def __init__(
        self,
        coordination_root: Path,
        *,
        monthly_cap_usd: Decimal = HARD_MONTHLY_CAP_USD,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if monthly_cap_usd <= 0 or monthly_cap_usd > HARD_MONTHLY_CAP_USD:
            raise OpenAIBudgetPolicyError("OpenAI monthly cap must be within (0, $30.00]")
        self.coordination_root = coordination_root
        self.monthly_cap_usd = monthly_cap_usd
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._store = AtomicJsonStore(coordination_root / "usage" / "openai-api-spend.json")
        self._lock_path = coordination_root / "openai-api-budget.lock"

    def _now(self) -> datetime:
        stamp = self._now_fn()
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise OpenAIBudgetPolicyError("budget clock must be timezone-aware")
        return stamp.astimezone(UTC)

    @staticmethod
    def _month(stamp: datetime) -> str:
        return stamp.strftime("%Y-%m")

    @staticmethod
    def _blank_ledger() -> dict[str, Any]:
        return {"version": 1, "months": {}}

    def _load(self) -> dict[str, Any]:
        data = self._store.read(default=None)
        if data is None:
            return self._blank_ledger()
        if not isinstance(data, dict) or data.get("version") != 1:
            raise OpenAIBudgetPolicyError("OpenAI budget ledger is malformed")
        months = data.get("months")
        if not isinstance(months, dict):
            raise OpenAIBudgetPolicyError("OpenAI budget ledger months are malformed")
        return data

    @staticmethod
    def _validate_tokens(value: int, label: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise OpenAIBudgetPolicyError(f"{label} must be a non-negative integer")

    @classmethod
    def estimate_cost(
        cls,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> Decimal:
        cls._validate_tokens(input_tokens, "input tokens")
        cls._validate_tokens(output_tokens, "output tokens")
        prices = CONSERVATIVE_PRICE_PER_MILLION.get(model)
        if prices is None:
            raise OpenAIBudgetPolicyError(f"unknown OpenAI pricing for model: {model}")
        input_rate, output_rate = prices
        if input_tokens > LONG_CONTEXT_THRESHOLD_TOKENS:
            input_rate *= LONG_INPUT_MULTIPLIER
            output_rate *= LONG_OUTPUT_MULTIPLIER
        return (
            Decimal(input_tokens) * input_rate / _MILLION
            + Decimal(output_tokens) * output_rate / _MILLION
        )

    @staticmethod
    def _parse_money(value: Any, label: str) -> Decimal:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise OpenAIBudgetPolicyError(f"malformed {label} in OpenAI budget ledger") from exc
        if not amount.is_finite() or amount < 0:
            raise OpenAIBudgetPolicyError(f"malformed {label} in OpenAI budget ledger")
        return amount

    @staticmethod
    def _month_entries(data: dict[str, Any], month: str) -> list[dict[str, Any]]:
        months = data["months"]
        bucket = months.setdefault(month, {"reservations": []})
        if not isinstance(bucket, dict) or not isinstance(bucket.get("reservations"), list):
            raise OpenAIBudgetPolicyError("OpenAI budget month bucket is malformed")
        return bucket["reservations"]

    def _spent_or_reserved(self, entries: list[dict[str, Any]]) -> Decimal:
        total = Decimal("0")
        for entry in entries:
            if not isinstance(entry, dict):
                raise OpenAIBudgetPolicyError("OpenAI budget reservation is malformed")
            status = entry.get("status")
            if status == "reconciled":
                total += self._parse_money(entry.get("actual_usd"), "actual spend")
            elif status in {"reserved", "uncertain"}:
                total += self._parse_money(entry.get("reserved_usd"), "reservation")
            else:
                raise OpenAIBudgetPolicyError("OpenAI budget reservation status is malformed")
        return total

    def reserve(
        self,
        *,
        model: str,
        input_token_ceiling: int,
        output_token_ceiling: int,
    ) -> BudgetReservation:
        reserved_usd = self.estimate_cost(
            model=model,
            input_tokens=input_token_ceiling,
            output_tokens=output_token_ceiling,
        )
        stamp = self._now()
        month = self._month(stamp)
        reservation_id = uuid4().hex
        with ControlPlaneLock(self._lock_path):
            data = self._load()
            entries = self._month_entries(data, month)
            total = self._spent_or_reserved(entries)
            if total + reserved_usd > self.monthly_cap_usd:
                raise OpenAIBudgetExceeded("OpenAI monthly API budget would be exceeded")
            entry = {
                "reservation_id": reservation_id,
                "model": model,
                "reserved_usd": str(reserved_usd),
                "actual_usd": None,
                "input_tokens": None,
                "output_tokens": None,
                "status": "reserved",
                "created_at": stamp.isoformat(),
                "updated_at": stamp.isoformat(),
            }
            entries.append(entry)
            self._store.write(data)
        return BudgetReservation(reservation_id, month, model, reserved_usd)

    @staticmethod
    def _find_reservation(data: dict[str, Any], reservation_id: str) -> tuple[str, dict[str, Any]]:
        for month, bucket in data["months"].items():
            if not isinstance(bucket, dict):
                raise OpenAIBudgetPolicyError("OpenAI budget month bucket is malformed")
            entries = bucket.get("reservations")
            if not isinstance(entries, list):
                raise OpenAIBudgetPolicyError("OpenAI budget month bucket is malformed")
            for entry in entries:
                if isinstance(entry, dict) and entry.get("reservation_id") == reservation_id:
                    return str(month), entry
        raise OpenAIBudgetPolicyError("unknown OpenAI budget reservation")

    @staticmethod
    def _valid_usage(usage: OpenAIUsage | None) -> bool:
        if not isinstance(usage, OpenAIUsage):
            return False
        values = (usage.input_tokens, usage.output_tokens)
        return all(
            not isinstance(value, bool) and isinstance(value, int) and value >= 0
            for value in values
        )

    def _mark_entry_uncertain(self, entry: dict[str, Any], stamp: datetime) -> None:
        if entry.get("status") != "reconciled":
            entry["status"] = "uncertain"
            entry["updated_at"] = stamp.isoformat()

    def reconcile(self, reservation_id: str, usage: OpenAIUsage | None) -> Decimal:
        stamp = self._now()
        with ControlPlaneLock(self._lock_path):
            data = self._load()
            _, entry = self._find_reservation(data, reservation_id)
            if entry.get("status") == "reconciled":
                return self._parse_money(entry.get("actual_usd"), "actual spend")
            if not self._valid_usage(usage):
                self._mark_entry_uncertain(entry, stamp)
                self._store.write(data)
                raise OpenAIBudgetPolicyError("provider usage is missing or malformed")
            assert usage is not None
            try:
                actual_usd = self.estimate_cost(
                    model=str(entry.get("model", "")),
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
            except OpenAIBudgetPolicyError:
                self._mark_entry_uncertain(entry, stamp)
                self._store.write(data)
                raise
            reserved_usd = self._parse_money(entry.get("reserved_usd"), "reservation")
            if actual_usd > reserved_usd:
                self._mark_entry_uncertain(entry, stamp)
                self._store.write(data)
                raise OpenAIBudgetPolicyError(
                    "provider usage exceeded the reserved worst-case cost"
                )
            entry["actual_usd"] = str(actual_usd)
            entry["input_tokens"] = usage.input_tokens
            entry["output_tokens"] = usage.output_tokens
            entry["status"] = "reconciled"
            entry["updated_at"] = stamp.isoformat()
            self._store.write(data)
            return actual_usd

    def mark_uncertain(self, reservation_id: str) -> None:
        stamp = self._now()
        with ControlPlaneLock(self._lock_path):
            data = self._load()
            _, entry = self._find_reservation(data, reservation_id)
            self._mark_entry_uncertain(entry, stamp)
            self._store.write(data)

    def status(self) -> dict[str, Decimal | str]:
        stamp = self._now()
        month = self._month(stamp)
        with ControlPlaneLock(self._lock_path):
            data = self._load()
            entries = self._month_entries(data, month)
            spent = self._spent_or_reserved(entries)
        remaining = self.monthly_cap_usd - spent
        if remaining < 0:
            remaining = Decimal("0")
        return {
            "month": month,
            "cap_usd": self.monthly_cap_usd,
            "spent_or_reserved_usd": spent,
            "remaining_usd": remaining,
        }
