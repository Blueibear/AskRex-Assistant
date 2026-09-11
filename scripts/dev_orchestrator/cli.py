import argparse
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .lifecycle import SupervisorLock, read_heartbeat, write_heartbeat
from .runner import CliAgentInvoker
from .storage import AtomicJsonStore
from .supervisor import Supervisor
from .types import OrchestratorConfig
from .usage import UsageBudget

_CONFIG_NAME = "orchestrator-config.json"
_HEARTBEAT_NAME = "supervisor-heartbeat.json"
_LOCK_NAME = "supervisor.lock"
_RUNTIME_DIRS = ("state", "queues", "alerts", "logs", "mailbox/supervisor")


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
    }


def initialize_runtime(root: Path, backend: Path, mobile: Path, frozen: Path) -> OrchestratorConfig:
    root = root.resolve()
    backend = backend.resolve()
    mobile = mobile.resolve()
    frozen = frozen.resolve()
    if backend == frozen or mobile == frozen:
        raise ValueError("frozen worktree cannot be configured as a development root")
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
    return OrchestratorConfig(
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
    )


def save_config(config: OrchestratorConfig) -> None:
    AtomicJsonStore(config.coordination_root / _CONFIG_NAME).write(
        _config_payload(config)
    )


def set_observe_only(root: Path, observe_only: bool) -> OrchestratorConfig:
    config = replace(load_config(root), observe_only=observe_only)
    save_config(config)
    return config


def record_confirmed_resets(root: Path, remaining: int) -> OrchestratorConfig:
    config = load_config(root)
    budget = UsageBudget(
        config.banked_resets_remaining,
        config.reserve_last_reset,
    ).with_confirmed_remaining(remaining)
    updated = replace(config, banked_resets_remaining=budget.banked_resets_remaining)
    save_config(updated)
    return updated


def heartbeat_is_stale(
    path: Path,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 180,
) -> bool:
    payload = read_heartbeat(path)
    if not payload or "timestamp" not in payload:
        return True
    try:
        stamp = datetime.fromisoformat(str(payload["timestamp"]))
    except ValueError:
        return True
    current = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (current - stamp).total_seconds() > max_age_seconds


def render_status(config: OrchestratorConfig) -> str:
    workers: dict[str, dict] = {}
    for role in ("backend", "mobile"):
        payload = AtomicJsonStore(
            config.coordination_root / "state" / f"{role}.json"
        ).read(default={})
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
    active_invoker = invoker or CliAgentInvoker(config)
    Supervisor(config, active_invoker).run_cycle()


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
    with lock:
        while max_cycles is None or cycles < max_cycles:
            write_heartbeat(heartbeat_path)
            run_cycle(config, invoker=invoker)
            write_heartbeat(heartbeat_path)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            sleep_fn(config.poll_seconds)


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

    resets = sub.add_parser("confirm-resets")
    resets.add_argument("--coordination-root", type=Path, required=True)
    resets.add_argument("--remaining", type=int, required=True)
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
        print(render_status(set_observe_only(root, False)))
        return 0
    if args.command == "pause":
        print(render_status(set_observe_only(root, True)))
        return 0
    if args.command == "confirm-resets":
        print(render_status(record_confirmed_resets(root, args.remaining)))
        return 0
    config = load_config(root)
    if args.command == "cycle":
        run_cycle(config)
        print(render_status(config))
        return 0
    if args.command == "run":
        run_loop(config)
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
