import argparse
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .completion import record_acceptance
from .handoff import (
    accept_handoff,
    request_handoff,
    validate_handoffs,
    worker_ack_handoff,
)
from .lifecycle import ControlPlaneLock, HeartbeatPump, SupervisorLock, read_heartbeat
from .paths import validate_runtime_paths
from .routing import validate_openai_policy
from .runner import CliAgentInvoker
from .storage import AtomicJsonStore
from .supervisor import Supervisor
from .types import OrchestratorConfig
from .usage import UsageBudget

_CONFIG_NAME = "orchestrator-config.json"
_HEARTBEAT_NAME = "supervisor-heartbeat.json"
_LOCK_NAME = "supervisor.lock"
_RUNTIME_DIRS = (
    "state",
    "queues",
    "alerts",
    "logs",
    "mailbox/supervisor",
    "handoff",
    "active-agents",
    "completion",
)


def _config_payload(config: OrchestratorConfig) -> dict:
    return {
        "backend_root": str(config.backend_root) if config.backend_root else "",
        "mobile_root": str(config.mobile_root) if config.mobile_root else "",
        "frozen_worktree": str(config.frozen_worktree) if config.frozen_worktree else "",
        "observe_only": config.observe_only,
        "banked_resets_remaining": config.banked_resets_remaining,
        "reserve_last_reset": config.reserve_last_reset,
        "poll_seconds": config.poll_seconds,
        "implementation_escalation_after": config.implementation_escalation_after,
        "review_escalation_after": config.review_escalation_after,
        "astra_adjudication_after": config.astra_adjudication_after,
        "openai_worker_enabled": config.openai_worker_enabled,
        "openai_review_model": config.openai_review_model,
        "openai_escalation_model": config.openai_escalation_model,
        "openai_planning_model": config.openai_planning_model,
        "openai_astra_model": config.openai_astra_model,
        "openai_astra_enabled": config.openai_astra_enabled,
        "openai_monthly_budget_usd": str(config.openai_monthly_budget_usd),
        "openai_project_id": config.openai_project_id,
        "openai_project_hard_limit_confirmed": config.openai_project_hard_limit_confirmed,
        "openai_timeout_seconds": config.openai_timeout_seconds,
        "openai_max_input_chars": config.openai_max_input_chars,
        "openai_max_output_tokens": config.openai_max_output_tokens,
        "openai_max_calls_per_cycle": config.openai_max_calls_per_cycle,
        "openai_max_astra_calls_per_escalation": config.openai_max_astra_calls_per_escalation,
        "metadata": config.metadata,
    }


def initialize_runtime(root: Path, backend: Path, mobile: Path, frozen: Path) -> OrchestratorConfig:
    root = root.resolve()
    backend = backend.resolve()
    mobile = mobile.resolve()
    frozen = frozen.resolve()
    validate_runtime_paths(root, backend, mobile, frozen)
    root.mkdir(parents=True, exist_ok=True)
    for relative in _RUNTIME_DIRS:
        (root / relative).mkdir(parents=True, exist_ok=True)
    config = OrchestratorConfig(
        coordination_root=root,
        backend_root=backend,
        mobile_root=mobile,
        frozen_worktree=frozen,
        observe_only=True,
    )
    config_path = root / _CONFIG_NAME
    if not config_path.exists():
        AtomicJsonStore(config_path).write(_config_payload(config))
    return load_config(root)


def load_config(root: Path) -> OrchestratorConfig:
    payload = AtomicJsonStore(root / _CONFIG_NAME).read()
    if not payload:
        raise FileNotFoundError(f"missing {root / _CONFIG_NAME}")
    config = OrchestratorConfig(
        coordination_root=root.resolve(),
        backend_root=Path(payload["backend_root"]).resolve(),
        mobile_root=Path(payload["mobile_root"]).resolve(),
        frozen_worktree=Path(payload["frozen_worktree"]).resolve(),
        observe_only=bool(payload.get("observe_only", True)),
        banked_resets_remaining=int(payload.get("banked_resets_remaining", 3)),
        reserve_last_reset=bool(payload.get("reserve_last_reset", True)),
        poll_seconds=int(payload.get("poll_seconds", 60)),
        implementation_escalation_after=int(payload.get("implementation_escalation_after", 2)),
        review_escalation_after=int(payload.get("review_escalation_after", 2)),
        astra_adjudication_after=int(payload.get("astra_adjudication_after", 4)),
        openai_worker_enabled=bool(payload.get("openai_worker_enabled", False)),
        openai_review_model=str(payload.get("openai_review_model", "gpt-5.6-terra")),
        openai_escalation_model=str(payload.get("openai_escalation_model", "gpt-5.6-sol")),
        openai_planning_model=str(payload.get("openai_planning_model", "gpt-5.6-sol")),
        openai_astra_model=str(payload.get("openai_astra_model", "gpt-6-astra")),
        openai_astra_enabled=bool(payload.get("openai_astra_enabled", True)),
        openai_monthly_budget_usd=Decimal(str(payload.get("openai_monthly_budget_usd", "30.00"))),
        openai_project_id=str(payload.get("openai_project_id", "")),
        openai_project_hard_limit_confirmed=bool(
            payload.get("openai_project_hard_limit_confirmed", False)
        ),
        openai_timeout_seconds=int(payload.get("openai_timeout_seconds", 120)),
        openai_max_input_chars=int(payload.get("openai_max_input_chars", 120_000)),
        openai_max_output_tokens=int(payload.get("openai_max_output_tokens", 4_000)),
        openai_max_calls_per_cycle=int(payload.get("openai_max_calls_per_cycle", 4)),
        openai_max_astra_calls_per_escalation=int(
            payload.get("openai_max_astra_calls_per_escalation", 1)
        ),
        metadata=dict(payload.get("metadata") or {}),
    )
    assert config.backend_root is not None
    assert config.mobile_root is not None
    assert config.frozen_worktree is not None
    validate_runtime_paths(
        config.coordination_root,
        config.backend_root,
        config.mobile_root,
        config.frozen_worktree,
    )
    validate_openai_policy(config)
    return config


def save_config(config: OrchestratorConfig) -> None:
    validate_openai_policy(config)
    AtomicJsonStore(config.coordination_root / _CONFIG_NAME).write(_config_payload(config))


def record_openai_project_limit_confirmation(
    root: Path, project_id: str, monthly_usd: Decimal
) -> OrchestratorConfig:
    if monthly_usd != Decimal("30.00"):
        raise ValueError("OpenAI project hard limit must be exactly $30.00/month")
    normalized_project_id = project_id.strip()
    if not normalized_project_id:
        raise ValueError("OpenAI project ID is required")
    with ControlPlaneLock(root / "control-plane.lock"):
        config = load_config(root)
        updated = replace(
            config,
            openai_project_id=normalized_project_id,
            openai_project_hard_limit_confirmed=True,
        )
        save_config(updated)
        return updated


def set_observe_only(root: Path, observe_only: bool) -> OrchestratorConfig:
    with ControlPlaneLock(root / "control-plane.lock"):
        config = replace(load_config(root), observe_only=observe_only)
        save_config(config)
        return config


def set_reset_policy(root: Path, reserve_last_reset: bool) -> OrchestratorConfig:
    with ControlPlaneLock(root / "control-plane.lock"):
        config = replace(load_config(root), reserve_last_reset=reserve_last_reset)
        save_config(config)
        return config


def activate_runtime(root: Path) -> OrchestratorConfig:
    with ControlPlaneLock(root / "control-plane.lock"):
        config = load_config(root)
        validate_handoffs(config)
        updated = replace(config, observe_only=False)
        save_config(updated)
        return updated


def record_confirmed_resets(root: Path, remaining: int) -> OrchestratorConfig:
    with ControlPlaneLock(root / "control-plane.lock"):
        config = load_config(root)
        budget = UsageBudget(
            config.banked_resets_remaining,
            config.reserve_last_reset,
        ).with_confirmed_remaining(remaining)
        updated = replace(config, banked_resets_remaining=budget.banked_resets_remaining)
        save_config(updated)
        for role in ("backend", "mobile"):
            store = AtomicJsonStore(root / "state" / f"{role}.json")
            state = store.read(default=None)
            if not state or state.get("status") != "blocked_user":
                continue
            if state.get("blocker_kind") != "usage_limit":
                continue
            resume = state.get("resume_status") or "implementing"
            state["status"] = resume
            state["blocked_reason"] = ""
            state["blocker_kind"] = ""
            state["resume_status"] = None
            store.write(state)
        return updated


def record_confirmed_acceptance(root: Path, role: str, kind: str, evidence: str) -> Path:
    with ControlPlaneLock(root / "control-plane.lock"):
        path = record_acceptance(root, role, kind, evidence)
        store = AtomicJsonStore(root / "state" / f"{role}.json")
        state = store.read(default=None)
        if (
            state
            and state.get("status") == "blocked_user"
            and state.get("blocker_kind") == "acceptance"
        ):
            state["status"] = state.get("resume_status") or "planning"
            state["blocked_reason"] = ""
            state["blocker_kind"] = ""
            state["resume_status"] = None
            store.write(state)
        return path


def resume_confirmed_human_blocker(root: Path, role: str, expected_kind: str) -> Path:
    with ControlPlaneLock(root / "control-plane.lock"):
        allowed = {"github_auth", "auth", "human"}
        if expected_kind not in allowed:
            raise ValueError(f"unsupported human blocker kind: {expected_kind}")
        store = AtomicJsonStore(root / "state" / f"{role}.json")
        state = store.read(default=None)
        if not state or state.get("status") != "blocked_user":
            raise ValueError(f"{role} is not blocked on a user action")
        actual = str(state.get("blocker_kind", ""))
        if actual != expected_kind:
            raise ValueError(f"blocker kind mismatch: expected {expected_kind}, found {actual}")
        state["status"] = state.get("resume_status") or "planning"
        state["blocked_reason"] = ""
        state["blocker_kind"] = ""
        state["resume_status"] = None
        store.write(state)
        return store.path


def heartbeat_is_stale(
    path: Path,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 180,
    max_future_skew_seconds: int = 5,
) -> bool:
    payload = read_heartbeat(path)
    expected_fields = {"pid", "process_started_filetime", "timestamp"}
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        return True
    pid = payload["pid"]
    started_filetime = payload["process_started_filetime"]
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return True
    if (
        isinstance(started_filetime, bool)
        or not isinstance(started_filetime, int)
        or started_filetime <= 0
    ):
        return True
    try:
        stamp = datetime.fromisoformat(str(payload["timestamp"]))
    except (TypeError, ValueError):
        return True
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return True
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        return True
    stamp = stamp.astimezone(UTC)
    current = current.astimezone(UTC)
    age_seconds = (current - stamp).total_seconds()
    if age_seconds < -max_future_skew_seconds:
        return True
    return age_seconds > max_age_seconds


def render_status(config: OrchestratorConfig) -> str:
    workers: dict[str, dict] = {}
    for role in ("backend", "mobile"):
        payload = AtomicJsonStore(config.coordination_root / "state" / f"{role}.json").read(
            default={}
        )
        task = payload.get("task") or {}
        workers[role] = {
            "status": payload.get("status", "idle"),
            "task_id": task.get("task_id", ""),
            "blocked_reason": payload.get("blocked_reason", ""),
        }
    data = {
        "observe_only": config.observe_only,
        "banked_resets_remaining": config.banked_resets_remaining,
        "reserve_last_reset": config.reserve_last_reset,
        "workers": workers,
    }
    return json.dumps(data, indent=2, sort_keys=True)


class _ObserveOnlyInvoker:
    def _unexpected(self):
        raise RuntimeError("observe-only supervisor attempted a model invocation")

    def implement(self, *args, **kwargs):
        return self._unexpected()

    def review(self, *args, **kwargs):
        return self._unexpected()

    def lead(self, *args, **kwargs):
        return self._unexpected()


def run_observe_cycle(config: OrchestratorConfig) -> None:
    if not config.observe_only:
        raise RuntimeError("active orchestration requires a configured agent invoker")
    Supervisor(config, _ObserveOnlyInvoker()).run_cycle()


def run_cycle(config: OrchestratorConfig, *, invoker=None) -> None:
    if config.observe_only:
        Supervisor(config, _ObserveOnlyInvoker()).run_cycle()
        return
    validate_handoffs(config)
    active_invoker = invoker or CliAgentInvoker(config)
    Supervisor(config, active_invoker).run_cycle()


def run_locked_cycle(config: OrchestratorConfig, *, invoker=None) -> None:
    lock = SupervisorLock(config.coordination_root / _LOCK_NAME)
    heartbeat_path = config.coordination_root / _HEARTBEAT_NAME
    with lock, HeartbeatPump(heartbeat_path):
        current = load_config(config.coordination_root)
        run_cycle(current, invoker=invoker)


def run_loop(
    config: OrchestratorConfig,
    *,
    invoker=None,
    max_cycles: int | None = None,
    sleep_fn=time.sleep,
) -> None:
    lock = SupervisorLock(config.coordination_root / _LOCK_NAME)
    heartbeat_path = config.coordination_root / _HEARTBEAT_NAME
    cycles = 0
    with lock, HeartbeatPump(heartbeat_path):
        while max_cycles is None or cycles < max_cycles:
            current = load_config(config.coordination_root)
            run_cycle(current, invoker=invoker)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            sleep_fn(current.poll_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="askrex-dev-orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init")
    init_parser.add_argument("--coordination-root", type=Path, required=True)
    init_parser.add_argument("--backend-root", type=Path, required=True)
    init_parser.add_argument("--mobile-root", type=Path, required=True)
    init_parser.add_argument("--frozen-worktree", type=Path, required=True)

    for name in ("status", "cycle", "run", "activate", "pause"):
        command = sub.add_parser(name)
        command.add_argument("--coordination-root", type=Path, required=True)

    for name in ("request-handoff", "accept-handoff"):
        handoff = sub.add_parser(name)
        handoff.add_argument("--coordination-root", type=Path, required=True)
        handoff.add_argument("--role", choices=("backend", "mobile"), required=True)
        if name == "request-handoff":
            handoff.add_argument("--worker-session", required=True)
        if name == "accept-handoff":
            handoff.add_argument("--nonce", required=True)

    worker_ack = sub.add_parser("operator-ack-handoff")
    worker_ack.add_argument("--coordination-root", type=Path, required=True)
    worker_ack.add_argument("--role", choices=("backend", "mobile"), required=True)
    worker_ack.add_argument("--nonce", required=True)
    worker_ack.add_argument("--worker-session", required=True)

    openai_limit = sub.add_parser("confirm-openai-project-limit")
    openai_limit.add_argument("--coordination-root", type=Path, required=True)
    openai_limit.add_argument("--project-id", required=True)
    openai_limit.add_argument("--monthly-usd", type=Decimal, required=True)

    resets = sub.add_parser("confirm-resets")
    resets.add_argument("--coordination-root", type=Path, required=True)
    resets.add_argument("--remaining", type=int, required=True)

    reset_policy = sub.add_parser("set-reset-policy")
    reset_policy.add_argument("--coordination-root", type=Path, required=True)
    reset_policy.add_argument("--reserve-last-reset", choices=("true", "false"), required=True)

    acceptance = sub.add_parser("confirm-acceptance")
    acceptance.add_argument("--coordination-root", type=Path, required=True)
    acceptance.add_argument("--role", choices=("backend", "mobile"), required=True)
    acceptance.add_argument(
        "--kind", choices=("physical", "cross_repo", "documentation"), required=True
    )
    acceptance.add_argument("--evidence", required=True)

    resume = sub.add_parser("resume-human")
    resume.add_argument("--coordination-root", type=Path, required=True)
    resume.add_argument("--role", choices=("backend", "mobile"), required=True)
    resume.add_argument("--blocker-kind", choices=("github_auth", "auth", "human"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.coordination_root
    if args.command == "init":
        config = initialize_runtime(
            root,
            args.backend_root,
            args.mobile_root,
            args.frozen_worktree,
        )
        print(render_status(config))
        return 0
    if args.command == "status":
        print(render_status(load_config(root)))
        return 0
    if args.command == "activate":
        print(render_status(activate_runtime(root)))
        return 0
    if args.command == "request-handoff":
        path = request_handoff(load_config(root), args.role, worker_session=args.worker_session)
        print(str(path))
        return 0
    if args.command == "operator-ack-handoff":
        path = worker_ack_handoff(
            load_config(root),
            args.role,
            nonce=args.nonce,
            source="trusted-operator",
            worker_session=args.worker_session,
        )
        print(str(path))
        return 0
    if args.command == "accept-handoff":
        path = accept_handoff(load_config(root), args.role, nonce=args.nonce)
        print(str(path))
        return 0
    if args.command == "pause":
        print(render_status(set_observe_only(root, True)))
        return 0
    if args.command == "confirm-openai-project-limit":
        print(
            render_status(
                record_openai_project_limit_confirmation(root, args.project_id, args.monthly_usd)
            )
        )
        return 0
    if args.command == "confirm-resets":
        print(render_status(record_confirmed_resets(root, args.remaining)))
        return 0
    if args.command == "set-reset-policy":
        reserve = args.reserve_last_reset == "true"
        print(render_status(set_reset_policy(root, reserve)))
        return 0
    if args.command == "confirm-acceptance":
        path = record_confirmed_acceptance(root, args.role, args.kind, args.evidence)
        print(str(path))
        return 0
    if args.command == "resume-human":
        path = resume_confirmed_human_blocker(root, args.role, args.blocker_kind)
        print(str(path))
        return 0
    config = load_config(root)
    if args.command == "cycle":
        run_locked_cycle(config)
        print(render_status(load_config(root)))
        return 0
    if args.command == "run":
        run_loop(config)
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
