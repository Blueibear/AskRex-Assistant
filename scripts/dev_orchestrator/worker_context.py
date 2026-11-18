"""Task-bound host facts and source-aware input snapshots for model workers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from .completed_tasks import CompletedTaskLedger
from .coordination import coordination_sources, render_coordination_source
from .coordination_checks import CoordinationCheckError, read_check_summary
from .storage import AtomicJsonStore
from .types import OrchestratorConfig, TaskItem, WorkerState
from .validation import load_validation_receipt

_CONTEXT_LIMIT = 100_000


class SuppliedContext(str):
    """Keep trusted source selection separate from text inside mailbox bodies."""

    def __new__(cls, text: str, critical_text: str):
        value = super().__new__(cls, text)
        value.critical_text = critical_text
        return value


@dataclass(frozen=True)
class WorkerContext:
    text: str
    external_sources: tuple[tuple[str, str], ...]

    def fingerprint(self, role: str, phase: str, state: WorkerState, head: str) -> str:
        # Generated blocker feedback, invocation IDs, admission times and outgoing
        # publications are deliberately not wake events.
        payload = {
            "schema_version": 1,
            "role": role,
            "phase": phase,
            "task": state.task.to_dict() if state.task else None,
            "task_base_head": state.task_base_head,
            "head": head,
            "external_sources": sorted(self.external_sources),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()


def _stamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("host evidence timestamp is missing")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("host evidence timestamp must be timezone-aware")
    return stamp


def _clip(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    marker = "\n[bounded excerpt; full captured output remains in the named artifact]\n"
    space = limit - len(marker)
    return text[: space // 2] + marker + text[-(space - space // 2) :]


def _failure_records(
    config: OrchestratorConfig, role: str, head: str, state: WorkerState
) -> dict[str, dict]:
    newest: dict[str, dict] = {}
    root = config.coordination_root
    for path in sorted((root / "validation-failures").glob("*/manifest.json")):
        value = AtomicJsonStore(path).read()
        if not isinstance(value, dict):
            raise ValueError("validation failure manifest is malformed")
        if value.get("role") != role or value.get("head") != head:
            continue
        if value.get("kind") != "validation_gate_failure" or (
            value.get("exit_code") == 0 and not value.get("timed_out")
        ):
            raise ValueError("matching validation failure outcome is malformed")
        task_id = value.get("task_id")
        if (
            state.task is not None
            and task_id == state.task.task_id
            and value.get("base_head") != state.task_base_head
        ):
            continue
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("matching validation failure task is missing")
        stamp = _stamp(value.get("recorded_at"))
        previous = newest.get(task_id)
        if previous is not None and _stamp(previous["recorded_at"]) >= stamp:
            continue
        excerpts = []
        for stream in ("stdout", "stderr"):
            descriptor = value.get(stream)
            if not isinstance(descriptor, dict) or not isinstance(descriptor.get("path"), str):
                raise ValueError("validation failure stream descriptor is malformed")
            artifact = (root / descriptor["path"]).resolve()
            if artifact.parent != path.parent.resolve():
                raise ValueError("validation failure stream escapes its attempt directory")
            digest = hashlib.sha256()
            size = 0
            leading = b""
            trailing = b""
            with artifact.open("rb") as handle:
                while data := handle.read(65536):
                    digest.update(data)
                    size += len(data)
                    leading = (leading + data)[:1200]
                    trailing = (trailing + data)[-1200:]
            if digest.hexdigest() != descriptor.get("sha256") or size != descriptor.get("bytes"):
                raise ValueError("validation failure stream integrity mismatch")
            excerpt = leading if size <= 1200 else leading + b"\n[bounded excerpt]\n" + trailing
            excerpts.append(
                stream.upper() + ": " + _clip(excerpt.decode("utf-8", errors="replace"))
            )
        newest[task_id] = {
            **value,
            "manifest": path.relative_to(root).as_posix(),
            "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "excerpt": "\n".join(excerpts),
        }
    return newest


def _gate_status(
    config: OrchestratorConfig,
    role: str,
    task_id: str,
    head: str,
    failure: dict | None,
    external: list[tuple[str, str]],
    expected_base_head: str | None = None,
) -> str:
    root = config.coordination_root
    receipt_path = root / "validation" / f"{role}-{task_id}.json"
    if receipt_path.resolve().parent != (root / "validation").resolve():
        raise ValueError("task receipt path escapes validation directory")
    raw_receipt = receipt_path.read_bytes() if receipt_path.exists() else None
    external.append(
        (
            receipt_path.relative_to(root).as_posix(),
            hashlib.sha256(raw_receipt).hexdigest() if raw_receipt is not None else "absent",
        )
    )
    payload = json.loads(raw_receipt) if raw_receipt is not None else None
    if failure is not None:
        external.append((failure["manifest"], failure["manifest_sha256"]))
    receipt = None
    if payload is not None:
        if not isinstance(payload, dict):
            raise ValueError("matching validation receipt is malformed")
        if (
            payload.get("role") == role
            and payload.get("task_id") == task_id
            and payload.get("head") == head
            and (expected_base_head is None or payload.get("base_head") == expected_base_head)
        ):
            receipt = load_validation_receipt(
                config, role, task_id, base_head=str(payload.get("base_head", "")), head=head
            )
    if failure is not None and (
        receipt is None or _stamp(failure["recorded_at"]) >= _stamp(receipt.passed_at)
    ):
        return (
            f"FAILED for task={task_id}, HEAD={head}, recorded_at={failure['recorded_at']}.\n"
            f"Gate={failure['gate']}; command={failure['command']}; exit={failure['exit_code']}; "
            f"timed_out={failure['timed_out']}.\n"
            f"Failure manifest={failure['manifest']}; SHA256={failure['manifest_sha256']}.\n"
            "This matching failure is not superseded by a pass for another task or revision.\n"
            + failure["excerpt"]
        )
    if receipt is None:
        return f"No matching current validation receipt for task={task_id}, HEAD={head}."
    # A positive claim requires the same receipt-byte and lease-backed capture
    # validation used by review. A raw success-looking JSON file is not enough.
    try:
        checks = read_check_summary(
            config, role, TaskItem(task_id, ""), receipt.base_head, head, strict=True
        )
    except CoordinationCheckError as exc:
        # Opening a new implementation attempt replaces only its capture manifest.
        # Preserve the older receipt as history without claiming a current pass or
        # treating the worker's own capture refresh as an external dependency reply.
        return (
            f"Earlier receipt exists for task={task_id}, HEAD={head}, passed_at={receipt.passed_at}; "
            f"complete current host captures are unavailable: {_clip(str(exc), 500)}. "
            "This receipt is not a verified current-task pass."
        )
    manifest = root / "evidence/supervisor-checks" / f"{role}-{task_id}.json"
    if not manifest.is_file() or "final: exit=0" not in checks:
        return f"Receipt exists for task={task_id}, HEAD={head}, but complete host captures are unavailable."
    details = "\n".join(
        f"Gate={gate.name}; exit={gate.exit_code}; command={list(gate.command)}.\n"
        + _clip(gate.stdout + "\n" + gate.stderr)
        for gate in receipt.gates
    )
    return (
        f"PASSED for task={task_id}, HEAD={head}, passed_at={receipt.passed_at}.\n"
        f"Receipt={receipt_path.relative_to(root).as_posix()}; "
        f"SHA256={hashlib.sha256(raw_receipt).hexdigest()}.\n"
        f"Complete coordination manifest={manifest.relative_to(root).as_posix()}; "
        f"SHA256={hashlib.sha256(manifest.read_bytes()).hexdigest()}.\n" + details
    )


def _host_status(
    config: OrchestratorConfig, role: str, state: WorkerState, head: str
) -> tuple[str, list[tuple[str, str]]]:
    failures = _failure_records(config, role, head, state)
    external: list[tuple[str, str]] = []
    lines = [
        "Host status is bound to the named task and revision. A prior-task baseline "
        "does not validate the current task, close a broader issue, authorize new permissions, "
        "or establish peer agreement. A newer matching failure takes precedence over an older pass.",
        f"Current leased repository HEAD: {head or 'unavailable'}.",
    ]
    if state.task is not None:
        lines.extend(
            (
                "### Current-task validation",
                _gate_status(
                    config,
                    role,
                    state.task.task_id,
                    head,
                    failures.get(state.task.task_id),
                    external,
                    expected_base_head=state.task_base_head,
                ),
            )
        )
    records = CompletedTaskLedger(config.coordination_root).records(role)
    for record in records[-12:]:
        task_id = str(record.get("task_id", ""))
        completed_head = str(record.get("completed_head", ""))
        if not task_id or completed_head != head:
            continue
        external.append(("Accepted prior task " + task_id, json.dumps(record, sort_keys=True)))
        lines.extend(
            (
                "### Prior-task baseline",
                f"Accepted task={task_id}; HEAD={completed_head}; source={record.get('source')}; "
                f"invocation={record.get('invocation_id')}; completed_at={record.get('completed_at')}.",
                _gate_status(config, role, task_id, head, failures.get(task_id), external),
            )
        )
    return "\n".join(lines), external


def build_worker_context(
    config: OrchestratorConfig, role: str, state: WorkerState, head: str
) -> WorkerContext:
    task_text = (
        f"{state.task.task_id}\n{state.task.prompt}\n{state.task.feedback}" if state.task else ""
    )
    sources = coordination_sources(
        config.coordination_root,
        role,
        deferred_issue_ids=config.deferred_issue_ids,
        deferred_task_prefixes=config.deferred_task_prefixes,
        task_text=task_text,
    )
    host_text, host_inputs = _host_status(config, role, state, head)
    sources.insert(0, ("Current host evidence", host_text, "host"))
    required = [source for source in sources if source[2] in {"host", "required"}]
    required += [source for source in sources if source[2] == "incoming"]
    required += [source for source in sources if source[2] == "outgoing"]
    optional = [source for source in sources if source[2] == "history"]
    limit = min(_CONTEXT_LIMIT, config.openai_max_input_chars)
    rendered: list[str] = []
    critical: list[str] = []
    external: list[tuple[str, str]] = list(host_inputs)
    used = 0
    for name, body, category in [*required, *optional]:
        text = render_coordination_source(name, body, category)
        needed = len(text) + (2 if rendered else 0)
        if used + needed > limit:
            if category in {"host", "required", "incoming", "outgoing"}:
                raise ValueError(
                    "required worker evidence exceeds bounded context capacity: " + name
                )
            continue
        rendered.append(text)
        used += needed
        if category != "history":
            critical.append(text)
        if category in {"required", "incoming"} or (category == "history" and state.task is None):
            external.append((name, body))
    return WorkerContext(
        SuppliedContext("\n\n".join(rendered), "\n\n".join(critical)), tuple(external)
    )
