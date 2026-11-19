from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .alerts import AlertSink
from .completed_tasks import CompletedTaskLedger
from .completion import (
    COMPLETION_MANIFESTS,
    CompletionGate,
    completion_observation_fingerprint,
    completion_preflight,
    prepare_final_review,
    recheck_final_completion,
)
from .coordination import (
    _issue_status,
    active_owned_issues,
    apply_agent_updates,
    recover_coordination_transactions,
    validate_agent_updates,
)
from .coordination_checks import (
    CoordinationCheckError,
    begin_checks,
    final_preparation_checkpoint,
    finish_checks,
    publication_preparation_checkpoint,
    retire_final_preparation,
)
from .evidence import EvidenceError, build_review_evidence
from .handoff import (
    clear_pending_result,
    read_pending_result,
    role_lease_lock_path,
    validate_handoff,
)
from .lifecycle import ControlPlaneLock
from .model_blockers import (
    archive_model_blocker,
    blocker_feedback,
    context_sha256,
    plan_input_fingerprint,
    read_consumed_completion_input,
    record_dependency_check,
    require_planning_hold_lineage,
    validate_consumed_blocker,
    validate_consumed_completion_result,
)
from .postprocessing import (
    UnclassifiedPostprocessingFailure,
    failure_feedback,
    retire_prepublication_marker,
)
from .provider_routing import (
    FABLE_REVIEW_MODEL,
    temporary_fable_policy_active,
    temporary_fable_review_active,
)
from .result_contract import (
    RESULT_CONTRACT_RETRY_LIMIT,
    archive_rejected_result,
    existing_rejection_reason,
    rejection_feedback,
    validate_consumed_rejection,
)
from .review_fallback import fallback_active, load_hourly_review, review_receipt_path
from .routing import SOL_MODEL, select_implementer_model, select_reviewer_model
from .runner import (
    AgentInvocationError,
    is_transient_runner_failure,
    recover_codex_windows_terminal_runner,
)
from .schema import validate_agent_result, validate_phase_outcome
from .storage import AtomicJsonStore
from .types import AgentResult, OrchestratorConfig, TaskItem, WorkerState, WorkerStatus
from .usage import UsageBudget, handle_usage_limit, is_cli_usage_provider
from .validation import load_validation_receipt, run_iteration_validation
from .worker_context import SuppliedContext, WorkerContext, build_worker_context


class AgentInvoker(Protocol):
    def implement(self, role, state, task, context, model) -> AgentResult: ...

    def poll_existing_cloud(self, role, task, context) -> AgentResult: ...

    def review(self, role, state, task, context, model) -> AgentResult: ...

    def lead(self, role, state, context, task=None) -> AgentResult: ...


def _state_from_dict(role: str, data: dict | None) -> WorkerState:
    if not data:
        return WorkerState(role=role)
    host_action = data.get("completion_host_action", "")
    if host_action not in {"", "observe", "publication"}:
        raise ValueError("unknown completion host action")
    task_data = data.get("task")
    task = TaskItem(**task_data) if task_data else None
    return WorkerState(
        role=role,
        status=WorkerStatus(data.get("status", "idle")),
        task=task,
        task_base_head=str(data.get("task_base_head", "")),
        iteration=int(data.get("iteration", 0)),
        implementation_failures=int(data.get("implementation_failures", 0)),
        review_failures=int(data.get("review_failures", 0)),
        blocked_reason=str(data.get("blocked_reason", "")),
        blocker_kind=str(data.get("blocker_kind", "")),
        resume_status=(WorkerStatus(data["resume_status"]) if data.get("resume_status") else None),
        claude_session_id=str(data.get("claude_session_id", "")),
        codex_session_id=str(data.get("codex_session_id", "")),
        last_result_invocation_id=str(data.get("last_result_invocation_id", "")),
        idle_context_fingerprint=str(data.get("idle_context_fingerprint", "")),
        planning_input_fingerprint=str(data.get("planning_input_fingerprint", "")),
        completion_preflight_fingerprint=str(data.get("completion_preflight_fingerprint", "")),
        completion_host_action=host_action,
        result_contract_phase=str(data.get("result_contract_phase", "")),
        result_contract_feedback=str(data.get("result_contract_feedback", "")),
        result_contract_failures=int(data.get("result_contract_failures", 0)),
        model_blocker_phase=str(data.get("model_blocker_phase", "")),
        model_blocker_feedback=str(data.get("model_blocker_feedback", "")),
        model_blocker_fingerprint=str(data.get("model_blocker_fingerprint", "")),
    )


_COORDINATION_ONLY_MARKERS = (
    "coordination only",
    "coordination closeout",
    "closeout evidence",
    "evidence closeout",
    "mailbox closeout",
    "evidence audit",
    "evidence reaudit",
    "evidence re audit",
    "evidence packet",
    "read only bounded audit",
    "read only bounded reaudit",
    "read only bounded re audit",
)

_NO_PRODUCT_MUTATION_MARKERS = (
    "do not change the accepted implementation",
    "do not change backend source",
    "do not modify backend source tests",
    "do not modify source tests",
    "do not modify source",
    "do not modify or create any file",
    "do not modify or create file",
    "do not change source tests",
    "no implementation change",
    "no source changes",
    "without changing implementation",
    "without modifying implementation",
    "accepted implementation unchanged",
    "do not modify the mobile, backend, or frozen testing repositories",
)


def _task_classification_text(task: TaskItem) -> str:
    text = f"{task.task_id}\n{task.prompt}".casefold()
    for punctuation in ("-", "_", "/"):
        text = text.replace(punctuation, " ")
    return " ".join(text.split())


def _is_coordination_only_task(task: TaskItem) -> bool:
    text = _task_classification_text(task)
    return any(marker in text for marker in _COORDINATION_ONLY_MARKERS) and any(
        marker in text for marker in _NO_PRODUCT_MUTATION_MARKERS
    )


def _updates_are_retest_handoff(updates) -> bool:
    statuses = {
        str(update.status).strip().casefold().replace("_", "-")
        for update in updates
        if str(update.status).strip()
    }
    return bool(statuses) and statuses <= {"fixed-needs-retest", "needs-retest"}


class Supervisor:
    def __init__(self, config: OrchestratorConfig, invoker: AgentInvoker) -> None:
        self.config = config
        self.invoker = invoker
        self.root = config.coordination_root
        recover_coordination_transactions(self.root)
        self.alerts = AlertSink(self.root / "alerts")
        self.completed_tasks = CompletedTaskLedger(self.root)
        self._result_context = threading.local()

    def _state_store(self, role: str) -> AtomicJsonStore:
        return AtomicJsonStore(self.root / "state" / f"{role}.json")

    def _queue_store(self, role: str) -> AtomicJsonStore:
        return AtomicJsonStore(self.root / "queues" / f"{role}.json")

    def _repo_for_role(self, role: str) -> Path:
        if role == "backend":
            root = self.config.backend_root
        elif role == "mobile":
            root = self.config.mobile_root
        else:
            raise ValueError(f"unsupported role: {role}")
        if root is None:
            raise ValueError(f"{role} repository root is not configured")
        return root.resolve()

    def _completion_context(self, role: str) -> str:
        repo = self._repo_for_role(role)
        return self.completed_tasks.planning_summary(
            role,
            repo=repo,
            current_head=self._capture_task_base_head(role),
        )

    def _worker_context(self, role: str, state: WorkerState) -> str:
        packet = build_worker_context(self.config, role, state, self._capture_task_base_head(role))
        self._result_context.worker_context = packet
        return packet.text

    def _input_fingerprint(
        self,
        role: str,
        state: WorkerState,
        phase: str,
        context: str,
        *,
        legacy_taskless_base: bool = False,
    ) -> str:
        packet = getattr(self._result_context, "worker_context", None)
        if not isinstance(packet, WorkerContext):
            packet = WorkerContext(context, (("Supplied context", context),))
        if legacy_taskless_base:
            return packet.fingerprint(
                role, phase, state, self._capture_task_base_head(role), legacy_taskless_base=True
            )
        return packet.fingerprint(role, phase, state, self._capture_task_base_head(role))

    def _blocker_record(
        self, role: str, state: WorkerState, result: AgentResult, phase: str
    ) -> dict:
        pending = read_pending_result(self.config, role)
        provenance = (
            pending.get("input_provenance")
            if pending is not None
            else getattr(self._result_context, "input_provenance", None)
        )
        return archive_model_blocker(
            self.config,
            role,
            state,
            result,
            phase=phase,
            pending_result=pending,
            input_provenance=provenance,
        )

    def _completed_assignment(self, role: str, task: TaskItem):
        repo = self._repo_for_role(role)
        return self.completed_tasks.matching_record(
            role,
            task,
            repo=repo,
            current_head=self._capture_task_base_head(role),
        )

    def _record_completed_task(
        self,
        role: str,
        task: TaskItem,
        *,
        source: str,
        invocation_id: str = "",
    ) -> None:
        with ControlPlaneLock(self.root / "control-plane.lock"):
            self.completed_tasks.record(
                role,
                task,
                completed_head=self._capture_task_base_head(role),
                invocation_id=invocation_id,
                source=source,
            )

    def _last_provider(self, role: str) -> str:
        record = AtomicJsonStore(self.root / "handoff" / f"{role}.json").read(default={})
        return str(record.get("last_provider", "")) if isinstance(record, dict) else ""

    def load_state(self, role: str) -> WorkerState:
        return _state_from_dict(role, self._state_store(role).read())

    @staticmethod
    def revalidate_review_evidence(config: OrchestratorConfig, role: str) -> dict:
        """Paused operator recovery without initializing an invoker or transactions."""
        from .review_recovery import revalidate_review_evidence

        return revalidate_review_evidence(config, role)

    def save_state(self, state: WorkerState) -> None:
        if getattr(self._result_context, "accepted_role", "") == state.role:
            state = replace(
                state,
                result_contract_phase="",
                result_contract_feedback="",
                result_contract_failures=0,
            )
        if (
            getattr(self._result_context, "accepted_role", "") == state.role
            and state.blocker_kind != "model_dependency"
        ):
            state = replace(
                state,
                model_blocker_phase="",
                model_blocker_feedback="",
                model_blocker_fingerprint="",
            )
        context_role = getattr(self._result_context, "role", "")
        invocation_id = getattr(self._result_context, "invocation_id", "")
        if invocation_id and context_role == state.role:
            state = replace(state, last_result_invocation_id=invocation_id)
        with ControlPlaneLock(self.root / "control-plane.lock"):
            self._state_store(state.role).write(state.to_dict())
        self._result_context.accepted_role = ""
        if invocation_id and context_role == state.role:
            clear_pending_result(self.config, state.role, invocation_id=invocation_id)
            self._result_context.role = ""
            self._result_context.invocation_id = ""

    def _save_state_preserving_pending_result(self, state: WorkerState) -> None:
        self._result_context.accepted_role = ""
        self._result_context.role = ""
        self._result_context.invocation_id = ""
        with ControlPlaneLock(self.root / "control-plane.lock"):
            self._state_store(state.role).write(state.to_dict())

    @staticmethod
    def _assigned_queue_state(state: WorkerState, task: TaskItem, head: str) -> WorkerState:
        return replace(
            state,
            status=WorkerStatus.IMPLEMENTING,
            task=task,
            task_base_head=head,
            blocked_reason="",
            blocker_kind="",
            resume_status=None,
            idle_context_fingerprint="",
            planning_input_fingerprint="",
            completion_preflight_fingerprint="",
            completion_host_action="",
            result_contract_phase="",
            result_contract_feedback="",
            result_contract_failures=0,
            model_blocker_phase="",
            model_blocker_feedback="",
            model_blocker_fingerprint="",
        )

    def _recover_queue_claim_locked(self, role: str) -> bool:
        """Finish an exact host-selected assignment while holding the control lock."""
        path = self.root / "queue-claims" / f"{role}.json"
        if not path.exists():
            return False
        claim = AtomicJsonStore(path).read()
        if not isinstance(claim, dict) or (
            claim.get("schema_version") != 1
            or claim.get("kind") != "queued_task_assignment"
            or claim.get("role") != role
        ):
            raise ValueError("queued task claim is malformed")
        before = claim.get("queue_before")
        after = claim.get("queue_after")
        index = claim.get("selected_index")
        if (
            not isinstance(before, list)
            or not isinstance(after, list)
            or type(index) is not int
            or not 0 <= index < len(before)
            or after != before[:index] + before[index + 1 :]
        ):
            raise ValueError("queued task claim does not remove exactly its selected task")
        original_state = _state_from_dict(role, claim.get("state_before"))
        task = TaskItem(**before[index])
        head = claim.get("task_base_head")
        if original_state.task is not None or not isinstance(head, str):
            raise ValueError("queued task claim has no unassigned source state")
        assigned = self._assigned_queue_state(original_state, task, head).to_dict()
        if claim.get("state_after") != assigned:
            raise ValueError("queued task claim changed fields outside assignment")
        state_store = self._state_store(role)
        queue_store = self._queue_store(role)
        current_state = state_store.read()
        current_queue = queue_store.read(default=[])
        if current_state not in (claim["state_before"], assigned) or current_queue not in (
            before,
            after,
        ):
            raise ValueError("queued task claim conflicts with current state or queue")
        # Assignment is durable before removal. The retained claim makes either
        # interrupted write idempotent; enqueues recover it before appending.
        if current_state != assigned:
            state_store.write(assigned)
        if current_queue != after:
            queue_store.write(after)
        path.unlink()
        return True

    def enqueue(self, role: str, task: TaskItem) -> None:
        with ControlPlaneLock(self.root / "control-plane.lock"):
            self._recover_queue_claim_locked(role)
            store = self._queue_store(role)
            queue = list(store.read(default=[]))
            queue.append(task.to_dict())
            store.write(queue)

    def _task_is_deferred(self, task_id: str) -> bool:
        normalized = task_id.strip().lower()
        if not normalized:
            return False
        if normalized in {value.lower() for value in self.config.deferred_issue_ids}:
            return True
        return any(
            normalized.startswith(prefix.strip().lower())
            for prefix in self.config.deferred_task_prefixes
            if prefix.strip()
        )

    def _dequeue(self, role: str) -> TaskItem | None:
        head = self._capture_task_base_head(role)
        with ControlPlaneLock(self.root / "control-plane.lock"):
            if self._recover_queue_claim_locked(role):
                return self.load_state(role).task
            state_before = self._state_store(role).read()
            state = _state_from_dict(role, state_before)
            if state.task is not None:
                raise ValueError("cannot claim queued work over an assigned task")
            queue = list(self._queue_store(role).read(default=[]))
            for index, payload in enumerate(queue):
                task = TaskItem(**payload)
                if self._task_is_deferred(task.task_id):
                    continue
                assigned = self._assigned_queue_state(state, task, head)
                AtomicJsonStore(self.root / "queue-claims" / f"{role}.json").write(
                    {
                        "schema_version": 1,
                        "kind": "queued_task_assignment",
                        "role": role,
                        "selected_index": index,
                        "task_base_head": head,
                        "queue_before": queue,
                        "queue_after": queue[:index] + queue[index + 1 :],
                        "state_before": state_before,
                        "state_after": assigned.to_dict(),
                    }
                )
                self._recover_queue_claim_locked(role)
                return task
            return None

    def _set_result_context(self, role: str, result: AgentResult) -> None:
        self._result_context.accepted_role = ""
        if not result.invocation_id:
            return
        pending = read_pending_result(self.config, role)
        if not pending:
            return
        if str(pending.get("invocation_id", "")) != result.invocation_id:
            raise ValueError("durable result receipt invocation mismatch")
        self._result_context.role = role
        self._result_context.invocation_id = result.invocation_id

    def _recover_result(
        self, role: str, state: WorkerState, *, phase: str, task_id: str
    ) -> AgentResult | None:
        pending = read_pending_result(self.config, role)
        if not pending:
            return None
        invocation_id = str(pending.get("invocation_id", ""))
        if state.last_result_invocation_id and state.last_result_invocation_id == invocation_id:
            clear_pending_result(self.config, role, invocation_id=invocation_id)
            return None
        if str(pending.get("role", "")) != role:
            raise ValueError("pending result role mismatch")
        if str(pending.get("phase", "")) != phase:
            raise ValueError("pending result phase mismatch")
        if str(pending.get("task_id", "")) != task_id:
            raise ValueError("pending result task mismatch")
        raw_result = pending.get("result")
        if not isinstance(raw_result, dict):
            raise ValueError("pending result payload is invalid")
        result = validate_agent_result(raw_result)
        if result.role != role or result.invocation_id != invocation_id:
            raise ValueError("pending result identity mismatch")
        if phase in {"implement", "review"} and result.task_id != task_id:
            raise ValueError("pending result bound task mismatch")
        return result

    def _admit_phase(
        self, role: str, *, phase: str, task_id: str, input_provenance: dict | None = None
    ) -> dict | None:
        """Durably start a phase under the same short lock used to commit pause."""
        if role not in {"backend", "mobile"}:
            raise ValueError(f"unsupported workstream: {role}")
        with ControlPlaneLock(self.root / "control-plane.lock", timeout_seconds=2.0):
            try:
                current = AtomicJsonStore(self.root / "orchestrator-config.json").read()
            except (OSError, ValueError) as exc:
                raise AgentInvocationError(
                    "supervisor", "paused", "current model-phase policy is unavailable"
                ) from exc
            if (
                self.config.observe_only
                or not isinstance(current, dict)
                or current.get("observe_only") is not False
            ):
                raise AgentInvocationError("supervisor", "paused", "new model phase paused")
            # This write is the admission boundary. The last record is audit-only:
            # never reuse it as permission or treat it as a busy/recovery marker.
            admission_id = uuid.uuid4().hex
            if input_provenance is not None:
                input_provenance = {**input_provenance, "admission_id": admission_id}
            AtomicJsonStore(self.root / "phase-admissions" / f"{role}.json").write(
                {
                    "schema_version": 1,
                    "kind": "model_phase_admission",
                    "admission_id": admission_id,
                    "role": role,
                    "phase": phase,
                    "task_id": task_id,
                    "pid": os.getpid(),
                    "admitted_at": datetime.now(UTC).isoformat(),
                    "input_provenance": input_provenance,
                }
            )

            return input_provenance

    def _invoke_or_recover(
        self,
        role: str,
        state: WorkerState,
        *,
        phase: str,
        task_id: str,
        invoke,
        context: str = "",
    ) -> AgentResult:
        result = self._recover_result(role, state, phase=phase, task_id=task_id)
        self._result_context.phase = phase
        if result is None:
            provenance = {
                "role": role,
                "phase": phase,
                "task_id": task_id,
                "head": self._capture_task_base_head(role),
                "source_context_sha256": context_sha256(context),
                "fingerprint": self._input_fingerprint(role, state, phase, context),
            }
            self._result_context.input_provenance = self._admit_phase(
                role, phase=phase, task_id=task_id, input_provenance=provenance
            )
            # This snapshot precedes lock waits and model execution. Never replace
            # its baseline with a post-result observation that could swallow a reply.
            result = invoke()
        else:
            pending = read_pending_result(self.config, role)
            self._result_context.input_provenance = (
                pending.get("input_provenance") if pending else None
            )
        self._set_result_context(role, result)
        return result

    def _repo_root(self, role: str) -> Path:
        root = self.config.backend_root if role == "backend" else self.config.mobile_root
        if root is None:
            raise ValueError(f"missing repository root for {role}")
        if self.config.frozen_worktree and root.resolve() == self.config.frozen_worktree.resolve():
            raise ValueError("frozen worktree cannot be used by supervisor")
        return root

    def _capture_task_base_head(self, role: str) -> str:
        repo = self._repo_root(role)
        try:
            with ControlPlaneLock(role_lease_lock_path(self.config, role)):
                return subprocess.check_output(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo,
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    def _idle_fingerprint(self, role: str, context: str) -> str:
        head = self._capture_task_base_head(role)
        payload = f"{head}\0{context}".encode()
        return hashlib.sha256(payload).hexdigest()

    def record_role_failure(self, role: str, error: Exception) -> None:
        """Park one role without consuming its durable result or replacing an existing gate."""
        if role not in {"backend", "mobile"}:
            raise ValueError(f"unsupported workstream: {role}")
        message = f"supervisor workstream failure: {error}"
        # Preserve the first cause before a retry can replace its state reason.
        self.alerts.emit(role=role, kind="workstream_failure", message=message)
        with ControlPlaneLock(self.root / "control-plane.lock"):
            if (self.root / "queue-claims" / f"{role}.json").exists():
                # Do not change either side of a recoverable assignment transaction.
                return
            state = self.load_state(role)
            if (
                state.blocker_kind == "model_dependency"
                or state.status is WorkerStatus.BLOCKED_USER
                or (state.status is WorkerStatus.BLOCKED_SYSTEM and state.resume_status is None)
            ):
                self._save_state_preserving_pending_result(state)
                return
            resume_status = (
                state.resume_status if state.status is WorkerStatus.BLOCKED_SYSTEM else state.status
            )
            unclassified = isinstance(error, UnclassifiedPostprocessingFailure)
            self._save_state_preserving_pending_result(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=message,
                    blocker_kind="postprocessing_evidence" if unclassified else "system",
                    resume_status=None if unclassified else resume_status,
                )
            )

    def run_role(self, role: str) -> None:
        """Advance one role through the same failure boundary as a manual cycle."""
        if role not in {"backend", "mobile"}:
            raise ValueError(f"unsupported workstream: {role}")
        if self.config.observe_only:
            return
        try:
            self._run_role(role)
        except Exception as exc:
            self.record_role_failure(role, exc)

    def run_cycle(self) -> None:
        if self.config.observe_only:
            return
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="askrex-workstream") as pool:
            futures = [pool.submit(self.run_role, role) for role in ("backend", "mobile")]
            for future in futures:
                future.result()

    @staticmethod
    def _task_family(task_id: str) -> str:
        return task_id.rsplit("-", 1)[0] if "-" in task_id else task_id

    @staticmethod
    def _message_field(text: str, field: str) -> str:
        prefix = field.lower() + ":"
        for line in text.splitlines():
            if line.lower().startswith(prefix):
                return line.split(":", 1)[1].strip()
        return ""

    def _dependency_ready(self, role: str, state: WorkerState) -> bool:
        if state.task is None:
            return False
        peer = "mobile" if role == "backend" else "backend"
        family = self._task_family(state.task.task_id)
        peer_state = self.load_state(peer)
        if peer_state.task and self._task_family(peer_state.task.task_id) == family:
            return False

        mailbox = self.root / "mailbox" / role
        if not mailbox.is_dir():
            return False
        needle = f"-{family.lower()}-"
        for path in sorted(mailbox.glob("*.md"), reverse=True):
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            if self._message_field(text, "From").lower() != peer:
                continue
            if self._message_field(text, "Needs response").lower() != "no":
                continue
            related = self._message_field(text, "Related")
            if needle in f"-{related.lower()}-":
                return True
        return False

    def _run_role(self, role: str) -> None:
        with ControlPlaneLock(self.root / "control-plane.lock"):
            self._recover_queue_claim_locked(role)
        if self.recover_postprocessing_failure(role):
            return
        state = self.load_state(role)
        if state.result_contract_phase and state.last_result_invocation_id:
            pending = read_pending_result(self.config, role)
            if pending and pending.get("invocation_id") == state.last_result_invocation_id:
                validate_consumed_rejection(self.config, role, state, pending)
                clear_pending_result(
                    self.config, role, invocation_id=state.last_result_invocation_id
                )
        if state.model_blocker_phase and state.last_result_invocation_id:
            pending = read_pending_result(self.config, role)
            if pending and pending.get("invocation_id") == state.last_result_invocation_id:
                validate_consumed_blocker(self.config, role, state, pending)
                clear_pending_result(
                    self.config, role, invocation_id=state.last_result_invocation_id
                )
        if (
            state.status is WorkerStatus.BLOCKED_SYSTEM
            and state.blocker_kind == "completion_remediation"
            and state.task is None
        ):
            # A reviewed obligation is not repeated automatically. Exact consumed
            # completion lineage may admit an explicitly queued new regression.
            self._settle_completion_result(role, state)
            self._claim_completion_queue(role, state)
            return
        if state.completion_host_action and (
            state.task is None or state.task.task_id != f"FINAL-VERIFY-{role}"
        ):
            raise ValueError("host publication intent lost its reserved completion task")
        if (
            (
                state.completion_host_action
                or state.blocker_kind
                in {
                    "host_publication",
                    "host_publication_system",
                    "completion_ci_wait",
                    "completion_observation_wait",
                }
            )
            and state.task is not None
            and state.task.task_id == f"FINAL-VERIFY-{role}"
        ):
            if state.status is WorkerStatus.BLOCKED_USER:
                # Existing resume-human is the only release for a definitive
                # authorization hold. Mailbox changes never authorize another write.
                return
            self._reconcile_completion_host(role, state)
            return
        if state.status is WorkerStatus.BLOCKED_SYSTEM and state.blocker_kind == "model_dependency":
            queued = state.task is None and any(
                not self._task_is_deferred(str(item.get("task_id", "")))
                for item in self._queue_store(role).read(default=[])
            )
            if queued:
                state = replace(
                    state,
                    status=WorkerStatus.IDLE,
                    blocked_reason="",
                    blocker_kind="",
                    resume_status=None,
                    model_blocker_phase="",
                    model_blocker_feedback="",
                    model_blocker_fingerprint="",
                )
                self.save_state(state)
            else:
                if state.task is not None and state.task.task_id == f"FINAL-VERIFY-{role}":
                    try:
                        self._require_final_evidence(role, state, "")
                    except EvidenceError as exc:
                        self._handle_invocation_error(
                            role,
                            state,
                            "review",
                            AgentInvocationError("supervisor", "incomplete_evidence", str(exc)),
                            "",
                        )
                        return
                context = self._worker_context(role, state)
                fingerprint = self._input_fingerprint(
                    role, state, state.model_blocker_phase, context
                )
                if (
                    state.task is None
                    and state.model_blocker_phase == "plan"
                    and fingerprint != state.model_blocker_fingerprint
                ):
                    legacy = self._input_fingerprint(
                        role, state, "plan", context, legacy_taskless_base=True
                    )
                    if legacy == state.model_blocker_fingerprint:
                        fingerprint = legacy
                if (
                    state.model_blocker_fingerprint
                    and fingerprint == state.model_blocker_fingerprint
                ):
                    record_dependency_check(self.config, role, state, fingerprint)
                    return
        if state.status is WorkerStatus.BLOCKED_SYSTEM:
            if (
                not state.result_contract_feedback
                and state.blocker_kind == "system"
                and state.resume_status is WorkerStatus.REVIEWING
                and state.task is not None
                and state.blocked_reason.startswith(
                    "agent result rejected by supervisor safety gate: invalid live-test issue id: "
                )
            ):
                # Older runtimes consumed the rejected result. Preserve only the known
                # host reason; never reconstruct a journal or accept that prior result.
                state = replace(
                    state,
                    result_contract_phase="review",
                    result_contract_feedback=rejection_feedback(
                        "review",
                        state.task.task_id,
                        state.blocked_reason,
                        invocation_id=state.last_result_invocation_id,
                        legacy=True,
                    ),
                )
            if state.blocker_kind in {"review_fallback_wait", "review_fallback_invalid"}:
                # A newly active Fable delegation supersedes old hourly receipt waits.
                # Normal review eligibility is still checked before invoking Fable.
                if not temporary_fable_policy_active(self.config, role) and fallback_active(
                    self.config
                ):
                    if state.blocker_kind == "review_fallback_invalid" or state.task is None:
                        return
                    head = self._capture_task_base_head(role)
                    receipt_path = review_receipt_path(self.config, role, state.task.task_id, head)
                    if not receipt_path.is_file():
                        return
            if state.resume_status is None:
                return
            if (
                not state.model_blocker_feedback
                and state.blocker_kind == "system"
                and state.resume_status in {WorkerStatus.IMPLEMENTING, WorkerStatus.REVIEWING}
                and state.blocked_reason
                and not state.blocked_reason.startswith(
                    (
                        "supervisor workstream failure:",
                        "agent result rejected",
                        "coordination validation unavailable:",
                        "agent coordination publication failed:",
                    )
                )
            ):
                phase = (
                    "implement" if state.resume_status is WorkerStatus.IMPLEMENTING else "review"
                )
                state = replace(
                    state,
                    model_blocker_phase=phase,
                    model_blocker_feedback=blocker_feedback(
                        AgentResult(
                            "blocked_system",
                            state.blocked_reason,
                            "",
                            blocker_reason=state.blocked_reason,
                            invocation_id=state.last_result_invocation_id,
                        ),
                        phase,
                        legacy=True,
                    ),
                )
            state = replace(
                state,
                status=state.resume_status,
                blocked_reason="",
                blocker_kind="",
                resume_status=None,
            )
            self.save_state(state)
        if state.status is WorkerStatus.BLOCKED_USER and state.blocker_kind == "dependency":
            if not self._dependency_ready(role, state):
                return
            state = replace(
                state,
                status=state.resume_status or WorkerStatus.IMPLEMENTING,
                blocked_reason="",
                blocker_kind="",
                resume_status=None,
            )
            self.save_state(state)
        if (
            state.status is WorkerStatus.BLOCKED_USER
            and state.blocker_kind == "acceptance"
            and state.task is None
            and not self.config.observe_only
        ):
            pending = read_pending_result(self.config, role)
            if pending and pending.get("invocation_id") == state.last_result_invocation_id:
                validate_consumed_completion_result(
                    self.config, role, state, pending, self._capture_task_base_head(role)
                )
                self._recover_result(
                    role, state, phase=pending["phase"], task_id=pending["task_id"]
                )
            if read_pending_result(self.config, role) is not None:
                raise ValueError("taskless acceptance hold has an unresolved result")
            queued = self._dequeue(role)
            if queued is not None:
                state = self.load_state(role)
            elif state.resume_status is WorkerStatus.PLANNING:
                # This is a final-release hold, not a task-specific user approval.
                # New work may be planned without confirming any acceptance gate.
                head = self._capture_task_base_head(role)
                admitted = read_consumed_completion_input(self.config, role, state, head)
                if admitted is None:
                    return
                baseline = state.planning_input_fingerprint
                if not baseline and admitted["phase"] == "plan":
                    baseline = admitted["fingerprint"]
                require_planning_hold_lineage(self.config, role, state, head)
                if baseline:
                    if len(baseline) != 64 or any(c not in "0123456789abcdef" for c in baseline):
                        raise ValueError("taskless planning input fingerprint is malformed")
                    context = self._worker_context(role, state)
                    current = self._input_fingerprint(role, state, "plan", context)
                    unchanged = current == baseline
                    if not state.planning_input_fingerprint and not unchanged:
                        # Compare old taskless scopes without replacing the admitted baseline.
                        unchanged = baseline == self._input_fingerprint(
                            role, state, "plan", context, legacy_taskless_base=True
                        )
                    if not unchanged:
                        state = replace(
                            state,
                            status=WorkerStatus.PLANNING,
                            blocked_reason="",
                            blocker_kind="",
                            resume_status=None,
                            idle_context_fingerprint="",
                        )
                        self.save_state(state)
                if state.status is WorkerStatus.BLOCKED_USER:
                    observed = completion_observation_fingerprint(self.config, role, head)
                    if state.completion_preflight_fingerprint != observed:
                        # A legacy mixed hold gets one classified observation. Only
                        # changed material local inputs permit another observation.
                        gate = completion_preflight(self.config, role)
                        self._block_final_gate(state, gate)
                    return
        if state.status is WorkerStatus.BLOCKED_USER and state.blocker_kind == "retest":
            if state.task is not None:
                if state.task.task_id.lower() in {
                    issue_id.lower() for issue_id in self.config.deferred_issue_ids
                }:
                    state = WorkerState(
                        role=role,
                        status=WorkerStatus.IDLE,
                        claude_session_id=state.claude_session_id,
                        codex_session_id=state.codex_session_id,
                    )
                    self.save_state(state)
                else:
                    issue = self.root / "issues" / f"{state.task.task_id}.md"
                    if (
                        issue.is_file()
                        and _issue_status(issue.read_text(encoding="utf-8", errors="replace"))
                        == "verified"
                    ):
                        state = WorkerState(role=role, status=WorkerStatus.IDLE)
                        self.save_state(state)
                    else:
                        return
            else:
                pending_retests = [
                    issue
                    for issue in active_owned_issues(
                        self.root, role, deferred_issue_ids=self.config.deferred_issue_ids
                    )
                    if _issue_status(issue.read_text(encoding="utf-8", errors="replace"))
                    == "fixed-needs-retest"
                ]
                if pending_retests:
                    return
                state = WorkerState(
                    role=role,
                    status=WorkerStatus.IDLE,
                    claude_session_id=state.claude_session_id,
                    codex_session_id=state.codex_session_id,
                )
                self.save_state(state)
        if state.status in {WorkerStatus.BLOCKED_USER, WorkerStatus.DONE}:
            return
        if self.config.observe_only:
            return
        from .publication_review import is_publication_review

        if state.task is not None and is_publication_review(state.task):
            self._publication_verify(role, state)
            return
        if state.task is not None and state.task.task_id == f"FINAL-VERIFY-{role}":
            # The reserved phase owns malformed/partial checkpoint classification.
            # Do not let generic context reads preempt its preserving failure gate.
            self._final_verify(role, state, None)
            return

        context = self._worker_context(role, state)
        if state.task is None:
            pending = read_pending_result(self.config, role)
            if pending and pending.get("invocation_id") != state.last_result_invocation_id:
                # Consume the durable planner result before selecting queued work.
                # PLANNING bypasses the idle fingerprint, while _recover_result still
                # enforces the exact pending role/phase/task binding.
                self._plan(role, replace(state, status=WorkerStatus.PLANNING), context)
                return
            queued = self._dequeue(role)
            if queued is not None:
                state = self.load_state(role)
                context = self._worker_context(role, state)

            else:
                # IDLE is a transient scheduling state in an active campaign.
                # After a reviewed task clears, immediately ask the planner for
                # the next bounded task. Only DONE or a real blocker parks work.
                self._plan(role, state, context)
                return

        if state.result_contract_phase == "adjudicate" or state.model_blocker_phase == "adjudicate":
            self._adjudicate(role, state, context)
            return
        if state.task.task_id == f"FINAL-VERIFY-{role}":
            self._final_verify(role, state, context)
            return
        if is_publication_review(state.task):
            self._publication_verify(role, state)
            return
        if state.status is WorkerStatus.REVIEWING:
            self._review(role, state, context)
            return
        self._implement(role, state, context)

    def _reject_result_contract(
        self, role: str, state: WorkerState, result: AgentResult, *, phase: str, reason: str
    ) -> None:
        archive_rejected_result(
            self.config,
            role,
            state,
            result,
            phase=phase,
            reason=reason,
            pending_result=read_pending_result(self.config, role),
        )
        # Archive first, then atomically persist the same-phase correction and
        # invocation ledger before save_state clears the real pending journal.
        failures = state.result_contract_failures + 1
        retrying = failures < RESULT_CONTRACT_RETRY_LIMIT
        self.save_state(
            replace(
                state,
                status=WorkerStatus.BLOCKED_SYSTEM,
                blocked_reason=(
                    f"agent result contract rejected ({failures}/{RESULT_CONTRACT_RETRY_LIMIT}): "
                    + ("automatic correction exhausted; " if not retrying else "")
                    + reason[:1000]
                ),
                blocker_kind="result_contract",
                resume_status=self._resume_status_for_phase(state, phase) if retrying else None,
                result_contract_phase=phase,
                result_contract_failures=failures,
                result_contract_feedback=rejection_feedback(
                    phase,
                    state.task.task_id if state.task else "",
                    reason,
                    invocation_id=result.invocation_id,
                ),
            )
        )

    def _accept_result(
        self,
        role: str,
        state: WorkerState,
        result: AgentResult,
        *,
        phase: str,
        allow_issue_updates: bool,
    ) -> bool:
        self._result_context.accepted_role = ""
        previous_rejection = existing_rejection_reason(
            self.config,
            role,
            state,
            result,
            phase=phase,
            pending_result=read_pending_result(self.config, role),
        )
        if previous_rejection is not None:
            self._reject_result_contract(
                role, state, result, phase=phase, reason=previous_rejection
            )
            return False
        try:
            validate_phase_outcome(result, phase)
        except ValueError as exc:
            self._reject_result_contract(role, state, result, phase=phase, reason=str(exc))
            return False
        if result.needs_user and result.outcome != "blocked_user":
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_USER,
                    blocked_reason=result.blocker_reason or result.summary,
                    blocker_kind="human",
                    resume_status=state.status,
                )
            )
            return False
        if result.outcome == "blocked_user" and (
            not result.needs_user or not result.blocker_reason.strip()
        ):
            self._reject_result_contract(
                role, state, result, phase=phase, reason="invalid blocked_user agent result"
            )
            return False
        try:
            validate_agent_updates(
                self.root,
                role,
                result,
                allow_issue_updates=allow_issue_updates,
                task_id=state.task.task_id if state.task else "",
            )
            if (
                result.issue_updates or result.coordination_messages
            ) and not result.invocation_id.strip():
                raise ValueError("coordination publication requires invocation_id")
        except (OSError, UnicodeError) as exc:
            self._save_state_preserving_pending_result(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=f"coordination validation unavailable: {exc}",
                    blocker_kind="system",
                    resume_status=self._resume_status_for_phase(state, phase),
                )
            )
            return False
        except ValueError as exc:
            self._reject_result_contract(role, state, result, phase=phase, reason=str(exc))
            return False
        if result.outcome == "blocked_system" and not is_transient_runner_failure(
            result.blocker_reason or result.summary
        ):
            self._blocker_record(role, state, result, phase)
        try:
            apply_agent_updates(
                self.root,
                role,
                result,
                allow_issue_updates=allow_issue_updates,
                task_id=state.task.task_id if state.task else "",
            )
        except (ValueError, OSError) as exc:
            # A publication/transaction failure is not a model contract defect.
            # Keep the exact pending result for idempotent transaction recovery.
            self._save_state_preserving_pending_result(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=f"agent coordination publication failed: {exc}",
                    blocker_kind="system",
                    resume_status=self._resume_status_for_phase(state, phase),
                )
            )
            return False
        self._result_context.accepted_role = role
        return True

    def _done_claim(self, role: str, state: WorkerState, context: str) -> None:
        issues = active_owned_issues(
            self.root, role, deferred_issue_ids=self.config.deferred_issue_ids
        )
        for issue in issues:
            text = issue.read_text(encoding="utf-8", errors="replace")
            status = _issue_status(text)
            candidate = TaskItem(
                issue.stem,
                f"Resolve remaining owned issue {issue.stem}.\n\n{text[:6000]}",
            )

            # A reviewed task stays completed while its reviewed checkpoint is
            # still an ancestor of the current branch. Canonical issue files may
            # remain fixed-needs-retest while physical testing owns acceptance;
            # that status must not resurrect implementation work.
            if self._completed_assignment(role, candidate) is not None:
                continue

            if status == "fixed-needs-retest":
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_USER,
                        task=TaskItem(issue.stem, "Await canonical testing verification"),
                        planning_input_fingerprint="",
                        completion_preflight_fingerprint="",
                        completion_host_action="",
                        task_base_head=self._capture_task_base_head(role),
                        blocked_reason=f"{issue.stem} requires testing/retest before completion",
                        blocker_kind="retest",
                        resume_status=WorkerStatus.PLANNING,
                    )
                )
                return

            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=candidate,
                    planning_input_fingerprint="",
                    completion_preflight_fingerprint="",
                    completion_host_action="",
                    task_base_head=self._capture_task_base_head(role),
                    blocked_reason="",
                )
            )
            return
        final_task = TaskItem(
            f"FINAL-VERIFY-{role}",
            "Independently verify final workstream readiness, tests, CI, docs, and safety gates.",
        )
        try:
            with ControlPlaneLock(role_lease_lock_path(self.config, role)):
                head = self._capture_task_base_head(role)
                # Only a new planner claim retires old proof. Keep its durable
                # result until this real transition commits, so an interrupted
                # retirement resumes without another planner or new local gates.
                retire_final_preparation(self.config, role, final_task, head)
                state = replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=final_task,
                    task_base_head=head,
                    blocked_reason="",
                    blocker_kind="",
                    resume_status=None,
                )
                self.save_state(state)
                state = self.load_state(role)
        except CoordinationCheckError as exc:
            self._handle_invocation_error(
                role,
                state,
                "review",
                AgentInvocationError("supervisor", "incomplete_evidence", str(exc)),
                context,
            )
            return
        # Once this reserved task is saved, recovery may only validate or finish
        # its own preparation checkpoint, never retire it as a fresh claim.
        self._final_verify(role, state, context)

    def _completion_host_task(self, state: WorkerState, *, head: str = "") -> WorkerState:
        return replace(
            state,
            task=TaskItem(
                f"FINAL-VERIFY-{state.role}",
                "Independently verify final workstream readiness, tests, CI, docs, and safety gates.",
            ),
            task_base_head=head or state.task_base_head or self._capture_task_base_head(state.role),
        )

    def _completion_host_active(self) -> bool:
        with ControlPlaneLock(self.root / "control-plane.lock", timeout_seconds=2):
            current = AtomicJsonStore(self.root / "orchestrator-config.json").read()
            if not isinstance(current, dict) or not isinstance(current.get("observe_only"), bool):
                raise ValueError("completion host action has no valid current control policy")
            return not self.config.observe_only and current["observe_only"] is False

    def _publish_completion(
        self, role: str, acceptance: tuple[str, ...], *, resume_denied: bool = False
    ):
        from .github_publication import publish_reviewed_draft

        return publish_reviewed_draft(
            self.config, role, unresolved_acceptance=acceptance, resume_denied=resume_denied
        )

    def _continue_host_publication(self, state: WorkerState, gate: CompletionGate) -> None:
        # Commit the accepted model result before a host action; remote operations
        # never run with an unresolved result journal or a global controller lock.
        state = self._completion_host_task(state, head=gate.head)
        resume_denied = (
            state.completion_host_action == "publication"
            and state.status is WorkerStatus.IMPLEMENTING
            and not state.blocker_kind
            and state.resume_status is None
        )
        # Consume an explicit resume durably before retrying a definitive denial.
        # A crash after another denial must never replay the same permission.
        self.save_state(
            replace(
                state,
                status=WorkerStatus.BLOCKED_SYSTEM,
                blocked_reason="Host draft publication is pending; final acceptance remains required.",
                blocker_kind="host_publication",
                resume_status=None,
                completion_host_action="publication",
            )
        )
        state = self.load_state(state.role)
        self._settle_completion_result(state.role, state)
        if not self._completion_host_active():
            return
        result = self._publish_completion(
            state.role, gate.acceptance_reasons, resume_denied=resume_denied
        )
        if result.status == "review_required":
            from .github_publication import PublicationResult, _Stop
            from .publication_review import designate_next_review

            try:
                task, base = designate_next_review(self.config, state.role)
            except _Stop as exc:
                result = PublicationResult(exc.status, str(exc))
            except (OSError, ValueError, RuntimeError) as exc:
                result = PublicationResult(
                    "system_error", f"Publication review preparation blocked: {exc}"
                )
            else:
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.IMPLEMENTING,
                        task=task,
                        task_base_head=base,
                        completion_host_action="",
                        blocked_reason="",
                        blocker_kind="",
                        resume_status=None,
                    )
                )
                return
        if result.status in {"published", "already_published"}:
            refreshed = completion_preflight(self.config, state.role)
            if refreshed.publication_reasons and not refreshed.system_error:
                self.save_state(
                    replace(
                        state,
                        blocked_reason="Host publication completed but current PR observation is pending.",
                    )
                )
                return
            self._block_final_gate(replace(state, completion_host_action=""), refreshed)
            return
        kinds = {
            "waiting": ("host_publication", WorkerStatus.BLOCKED_SYSTEM),
            "auth_required": ("github_auth", WorkerStatus.BLOCKED_USER),
            "system_error": ("host_publication_system", WorkerStatus.BLOCKED_SYSTEM),
        }
        kind, status = kinds.get(
            result.status, ("host_publication_system", WorkerStatus.BLOCKED_SYSTEM)
        )
        self.save_state(
            replace(
                state,
                status=status,
                blocker_kind=kind,
                blocked_reason=result.reason or f"Host publication status: {result.status}",
                resume_status=(
                    WorkerStatus.IMPLEMENTING if status is WorkerStatus.BLOCKED_USER else None
                ),
            )
        )

    def _settle_completion_result(self, role: str, state: WorkerState) -> None:
        # Reuse consumed-completion validation without granting a new model phase.
        held = replace(
            state,
            status=WorkerStatus.BLOCKED_USER,
            blocker_kind="acceptance",
            task=None,
            resume_status=WorkerStatus.PLANNING,
        )
        head = self._capture_task_base_head(role)
        pending = read_pending_result(self.config, role)
        if pending and pending.get("invocation_id") == state.last_result_invocation_id:
            validate_consumed_completion_result(self.config, role, held, pending, head)
            self._recover_result(role, held, phase=pending["phase"], task_id=pending["task_id"])
        if read_pending_result(self.config, role) is not None:
            raise ValueError("host completion wait has an unresolved result")
        if read_consumed_completion_input(self.config, role, held, head) is None:
            raise ValueError("host completion wait has no consumed input provenance")

    def _claim_completion_queue(self, role: str, state: WorkerState) -> bool:
        with ControlPlaneLock(self.root / "control-plane.lock", timeout_seconds=2):
            queue = self._queue_store(role).read(default=[])
            if not any(not self._task_is_deferred(str(item.get("task_id", ""))) for item in queue):
                return False
            if not self._completion_host_active():
                return False
            if self.load_state(role) != state:
                raise ValueError("completion wait changed before queued work selection")
            # Publication journals remain untouched and must reconcile before a
            # later host publication. They do not freeze authorized local work.
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IDLE,
                    task=None,
                    task_base_head="",
                    blocked_reason="",
                    blocker_kind="",
                    resume_status=None,
                    completion_host_action="",
                    completion_preflight_fingerprint="",
                    planning_input_fingerprint="",
                    idle_context_fingerprint="",
                )
            )
            return self._dequeue(role) is not None

    def _reconcile_completion_host(self, role: str, state: WorkerState) -> None:
        self._settle_completion_result(role, state)
        if not self._completion_host_active():
            return
        if self._claim_completion_queue(role, state):
            return
        gate = completion_preflight(self.config, role)
        if state.completion_host_action == "publication" and not gate.system_error:
            # Reconcile the publisher's own journal even if the PR is now visible.
            self._continue_host_publication(state, gate)
        else:
            self._block_final_gate(state, gate)

    def _block_final_gate(self, state: WorkerState, gate: CompletionGate) -> None:
        reason = gate.system_error or "; ".join(gate.reasons)
        if gate.system_error or gate.blocker_kind == "incomplete_evidence":
            if gate.observation_retryable and gate.blocker_kind != "incomplete_evidence":
                observed = self._completion_host_task(state, head=gate.head)
                self.save_state(
                    replace(
                        observed,
                        status=WorkerStatus.BLOCKED_SYSTEM,
                        blocked_reason=reason,
                        resume_status=None,
                        blocker_kind=(
                            "host_publication_system"
                            if state.completion_host_action == "publication"
                            else "completion_observation_wait"
                        ),
                        completion_host_action=state.completion_host_action or "observe",
                    )
                )
                self._settle_completion_result(state.role, self.load_state(state.role))
                return
            host_wait = gate.blocker_kind != "incomplete_evidence" and (
                state.completion_host_action == "publication"
                or state.blocker_kind in {"host_publication", "host_publication_system"}
            )
            self._save_state_preserving_pending_result(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=reason,
                    blocker_kind=(
                        "host_publication_system"
                        if host_wait
                        else "incomplete_evidence" if not gate.system_error else "validation"
                    ),
                    resume_status=None,
                    completion_host_action="publication" if host_wait else "",
                )
            )
            return
        observed = gate.observation_fingerprint or completion_observation_fingerprint(
            self.config, state.role, gate.head or self._capture_task_base_head(state.role)
        )
        state = replace(state, completion_preflight_fingerprint=observed)
        if gate.blocker_kind and gate.blocker_kind not in {"acceptance"}:
            if gate.blocker_kind == "github_auth":
                state = self._completion_host_task(state, head=gate.head)
                state = replace(
                    state, completion_host_action=state.completion_host_action or "observe"
                )
            self.save_state(
                replace(
                    state,
                    status=(
                        WorkerStatus.BLOCKED_USER
                        if gate.needs_user
                        else WorkerStatus.BLOCKED_SYSTEM
                    ),
                    task=state.task if state.completion_host_action else None,
                    blocked_reason=reason,
                    blocker_kind=gate.blocker_kind,
                    resume_status=(
                        (
                            WorkerStatus.IMPLEMENTING
                            if state.completion_host_action
                            else WorkerStatus.PLANNING
                        )
                        if gate.needs_user
                        else None
                    ),
                )
            )
            return
        if gate.local_remediation:
            names = {
                "missing_ci_workflow": ("ci-workflow", "Establish the role-owned CI workflow."),
                "ci_checks": ("ci-checks", "Repair the observed required CI check failures."),
                "ci_blocking_checks": (
                    "ci-blocking-checks",
                    "Diagnose the actual additional completion-blocking CI failures below. "
                    "These are distinct from the manifest's required named checks. "
                    "Determine a scoped source remedy or report the genuine external/user blocker. "
                    "Do not waive or disable a check merely to obtain a pass.",
                ),
                "local_validation": (
                    "local-validation",
                    "Repair the actual canonical release-gate failure.",
                ),
            }
            key = gate.local_remediation[0]
            if key not in names:
                raise ValueError("unknown local completion remediation obligation")
            suffix, instruction = names[key]
            if key != "missing_ci_workflow":
                identity = gate.remediation_fingerprint
                if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
                    raise ValueError("completion failure has no bound material obligation identity")
                suffix += "-" + identity
            manifest = COMPLETION_MANIFESTS[state.role]
            commands = "\n".join(
                f"- {item.name}: {list(item.command)} (cwd={item.cwd})"
                for item in manifest.local_gates
            )
            candidate = TaskItem(
                f"FINAL-{state.role}-{suffix}",
                instruction
                + "\nImplement only source/configuration/tests within this leased role repository. "
                "Follow the repository instructions, including any additional required CI verification. "
                "Do not push, open a pull request, alter credentials, or confirm physical, documentation "
                "or cross-repository acceptance. The trusted host owns draft publication and canonical "
                "validation; all final human requirements remain unsatisfied until separately confirmed."
                + "\nRequired CI check names: "
                + ", ".join(manifest.required_checks)
                + "\nCanonical local gate contract:\n"
                + commands
                + "\nObserved completion requirements (human requirements are context, not this task):\n"
                + "\n".join("- " + item for item in gate.reasons),
            )
            if self._completed_assignment(state.role, candidate) is not None:
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_SYSTEM,
                        task=None,
                        blocked_reason=(
                            "Reviewed completion obligation remains unsatisfied: "
                            + candidate.task_id
                            + ". Preserve its accepted history; require a new explicit regression task. "
                            + reason
                        ),
                        blocker_kind="completion_remediation",
                        resume_status=None,
                        completion_host_action="",
                    )
                )
                return
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=candidate,
                    task_base_head=gate.head or self._capture_task_base_head(state.role),
                    blocked_reason="",
                    blocker_kind="",
                    resume_status=None,
                    planning_input_fingerprint="",
                    completion_preflight_fingerprint="",
                    completion_host_action="",
                )
            )
            return
        if gate.publication_reasons:
            self._continue_host_publication(state, gate)
            return
        if gate.waiting_for_ci:
            state = self._completion_host_task(state, head=gate.head)
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=reason,
                    blocker_kind="completion_ci_wait",
                    resume_status=None,
                    completion_host_action="observe",
                )
            )
            return
        if gate.ready:
            self.save_state(
                replace(
                    self._completion_host_task(state, head=gate.head),
                    completion_host_action="",
                    status=WorkerStatus.IMPLEMENTING,
                    blocked_reason="",
                    blocker_kind="",
                    resume_status=None,
                )
            )
            return
        if gate.needs_user:
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_USER,
                    task=None,
                    blocked_reason=reason,
                    blocker_kind=gate.blocker_kind or "human",
                    resume_status=WorkerStatus.PLANNING,
                    completion_host_action="",
                )
            )
            return
        self._save_state_preserving_pending_result(
            replace(
                state,
                status=WorkerStatus.BLOCKED_SYSTEM,
                blocked_reason=reason,
                blocker_kind="completion_observation",
                resume_status=None,
            )
        )

    def _require_final_evidence(self, role: str, state: WorkerState, context: str) -> None:
        assert state.task is not None
        bundle = build_review_evidence(
            self.config, role, state, state.task, context, "final-verification-preflight"
        )
        if bundle.truncated:
            raise EvidenceError(
                "review-critical evidence truncated: " + ", ".join(bundle.truncation_reasons)
            )

    def _publication_remediation(self, state: WorkerState, feedback: str, identity: str) -> None:
        head = self._capture_task_base_head(state.role)
        self.save_state(
            replace(
                state,
                status=WorkerStatus.IMPLEMENTING,
                task=TaskItem(
                    f"PUBLICATION-REMEDIATE-{state.role}-{head[:12]}-{identity}",
                    "Correct only the actual publication review or canonical gate findings below. "
                    "Use normal implementation and validation. Do not push, create a PR, or claim "
                    "physical/final acceptance. A fresh exhaustive publication review remains required.\n\n"
                    + feedback,
                    feedback=feedback,
                ),
                task_base_head=head,
                completion_host_action="",
                blocked_reason="",
                blocker_kind="",
                resume_status=None,
                review_failures=state.review_failures + 1,
            )
        )

    def _publication_verify(self, role: str, state: WorkerState) -> None:
        from .publication_review import prepare_part, validate_review_task

        assert state.task is not None
        context = ""
        remediation = None
        try:
            with ControlPlaneLock(role_lease_lock_path(self.config, role)):
                head = self._capture_task_base_head(role)
                validate_review_task(self.config, role, state.task, state.task_base_head, head)
                checkpoint = publication_preparation_checkpoint(
                    self.config, role, state.task, state.task_base_head, head
                )
                if state.status is WorkerStatus.IMPLEMENTING:
                    if checkpoint == "new":
                        begin_checks(
                            self.config,
                            role,
                            state.task,
                            state.task_base_head,
                            head,
                            recovered=True,
                        )
                        checkpoint = "open"
                    if checkpoint == "open":
                        report = run_iteration_validation(
                            self.config,
                            role,
                            state.task.task_id,
                            base_head=state.task_base_head,
                            head=head,
                        )
                        if report.system_error:
                            raise CoordinationCheckError(report.system_error)
                        if not report.passed:
                            remediation = report.feedback
                        else:
                            checkpoint = publication_preparation_checkpoint(
                                self.config, role, state.task, state.task_base_head, head
                            )
                    if checkpoint == "validated":
                        finish_checks(self.config, role, state.task, state.task_base_head, head)
                        checkpoint = publication_preparation_checkpoint(
                            self.config, role, state.task, state.task_base_head, head
                        )
                if remediation is None:
                    if checkpoint != "complete":
                        raise CoordinationCheckError(
                            "publication review lacks complete canonical preparation"
                        )
                    if read_pending_result(self.config, role) is None:
                        context = self._worker_context(role, state)
                    prepare_part(self.config, role, state, context)
                    if state.status is WorkerStatus.IMPLEMENTING:
                        state = replace(state, status=WorkerStatus.REVIEWING)
                        self.save_state(state)
        except (CoordinationCheckError, EvidenceError, OSError, ValueError) as exc:
            self._handle_invocation_error(
                role,
                state,
                "review",
                AgentInvocationError("supervisor", "incomplete_evidence", str(exc)),
                context,
            )
            return
        # Result consumption can already have committed a new state before journal
        # cleanup fails. Let run_role reload that state, never rewrite this old one.
        if remediation is not None:
            self._publication_remediation(state, remediation, "validation")
            return
        self._review(role, state, context)

    def _final_verify(self, role: str, state: WorkerState, context: str | None) -> None:
        assert state.task is not None and state.task.task_id == f"FINAL-VERIFY-{role}"
        if state.status is WorkerStatus.IMPLEMENTING:
            try:
                with ControlPlaneLock(role_lease_lock_path(self.config, role)):
                    checkpoint = final_preparation_checkpoint(
                        self.config, role, state.task, state.task_base_head
                    )
                    if checkpoint == "new":
                        begin_checks(
                            self.config,
                            role,
                            state.task,
                            state.task_base_head,
                            state.task_base_head,
                            recovered=True,
                        )
                        checkpoint = "open"
                    if checkpoint == "open":
                        gate = prepare_final_review(self.config, role, head=state.task_base_head)
                        if not gate.ready:
                            self._block_final_gate(state, gate)
                            return
                        checkpoint = final_preparation_checkpoint(
                            self.config, role, state.task, state.task_base_head
                        )
                    if checkpoint == "validated":
                        finish_checks(
                            self.config,
                            role,
                            state.task,
                            state.task_base_head,
                            state.task_base_head,
                        )
                        checkpoint = final_preparation_checkpoint(
                            self.config, role, state.task, state.task_base_head
                        )
                    if checkpoint != "complete":
                        raise CoordinationCheckError("final preparation has no complete checkpoint")
                    state = replace(state, status=WorkerStatus.REVIEWING)
                    self.save_state(state)
            except CoordinationCheckError as exc:
                self._handle_invocation_error(
                    role,
                    state,
                    "review",
                    AgentInvocationError("supervisor", "incomplete_evidence", str(exc)),
                    context or "",
                )
                return
        try:
            # Recovery must verify the same complete evidence before applying a
            # durable reviewer result, even when no fresh model call is needed.
            self._require_final_evidence(role, state, context or "")
            if context is None:
                context = self._worker_context(role, state)
                self._require_final_evidence(role, state, context)
            result = self._invoke_or_recover(
                role,
                state,
                phase="review",
                task_id=state.task.task_id,
                context=context,
                invoke=lambda: self.invoker.review(role, state, state.task, context, SOL_MODEL),
            )
        except EvidenceError as exc:
            self._handle_invocation_error(
                role,
                state,
                "review",
                AgentInvocationError("supervisor", "incomplete_evidence", str(exc)),
                context or "",
            )
            return
        except AgentInvocationError as exc:
            self._handle_invocation_error(role, state, "review", exc, context or "")
            return
        if not self._accept_result(role, state, result, phase="review", allow_issue_updates=False):
            return
        if result.outcome == "pass":
            with ControlPlaneLock(role_lease_lock_path(self.config, role)):
                gate = recheck_final_completion(self.config, role, head=state.task_base_head)
                if not gate.ready:
                    self._block_final_gate(state, gate)
                    return
                validate_handoff(self.config, role)
                try:
                    self._require_final_evidence(role, state, context)
                except EvidenceError as exc:
                    self._handle_invocation_error(
                        role,
                        state,
                        "review",
                        AgentInvocationError("supervisor", "incomplete_evidence", str(exc)),
                        context,
                    )
                    return
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.DONE,
                        blocked_reason="",
                        blocker_kind="",
                        resume_status=None,
                    )
                )
            return
        if result.outcome == "changes_required":
            prompt = "\n\n".join(part for part in (result.summary, result.next_action) if part)
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=TaskItem(f"FINAL-{role}", prompt),
                    task_base_head=self._capture_task_base_head(role),
                )
            )
            return
        self._save_block_or_failure(state, result)

    def _plan(self, role: str, state: WorkerState, context: str) -> None:
        completion_context = self._completion_context(role)
        planning_context = context + completion_context
        if isinstance(context, SuppliedContext):
            planning_context = SuppliedContext(
                planning_context, context.critical_text + completion_context
            )
        planning_fingerprint = hashlib.sha256(planning_context.encode("utf-8")).hexdigest()
        # A rejected completed-task assignment is not new work. Replan only
        # when coordination context changes or an explicit queue item arrives.
        if (
            state.status is WorkerStatus.IDLE
            and state.idle_context_fingerprint == planning_fingerprint
        ):
            return
        try:
            result = self._invoke_or_recover(
                role,
                state,
                phase="plan",
                task_id="",
                context=planning_context,
                invoke=lambda: self.invoker.lead(
                    role, replace(state, status=WorkerStatus.PLANNING), planning_context
                ),
            )
        except AgentInvocationError as exc:
            self._handle_invocation_error(role, state, "plan", exc, context)
            return
        baseline = ""
        if result.outcome == "done":
            baseline = plan_input_fingerprint(
                role,
                getattr(self._result_context, "input_provenance", None),
                self._capture_task_base_head(role),
            )
        if not self._accept_result(role, state, result, phase="plan", allow_issue_updates=False):
            return
        if result.outcome == "assign":
            if not result.task_id or not result.task_prompt:
                raise ValueError("lead assign result requires task_id and task_prompt")
            candidate = TaskItem(result.task_id, result.task_prompt)
            completed = self._completed_assignment(role, candidate)
            if completed is not None:
                self.alerts.emit(
                    role=role,
                    kind="duplicate-completed-task",
                    message=(
                        f"Planner assignment {result.task_id} matches reviewed completed work "
                        f"at {completed.get('completed_head', '')}; assignment rejected."
                    ),
                )
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.IDLE,
                        task=None,
                        task_base_head="",
                        blocked_reason="",
                        blocker_kind="",
                        idle_context_fingerprint=planning_fingerprint,
                    )
                )
                return
            if self._task_is_deferred(result.task_id):
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_SYSTEM,
                        task=None,
                        blocked_reason=(
                            f"planner assigned deferred task prefix/id: {result.task_id}"
                        ),
                        blocker_kind="system",
                        resume_status=WorkerStatus.PLANNING,
                    )
                )
                return
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=TaskItem(result.task_id, result.task_prompt),
                    task_base_head=self._capture_task_base_head(role),
                    blocked_reason="",
                    idle_context_fingerprint="",
                    planning_input_fingerprint="",
                    completion_preflight_fingerprint="",
                    completion_host_action="",
                )
            )
            return
        if result.outcome == "done":
            state = replace(state, planning_input_fingerprint=baseline)
            # Keep the original admission/recovered-journal baseline. A reply
            # arriving during the call must not be swallowed by a new baseline.
            fresh_context = self._worker_context(role, state)
            changed = bool(baseline) and baseline != self._input_fingerprint(
                role, state, "plan", fresh_context
            )
            with ControlPlaneLock(self.root / "control-plane.lock"):
                queued = list(self._queue_store(role).read(default=[]))
            if changed or any(
                not self._task_is_deferred(TaskItem(**item).task_id) for item in queued
            ):
                # Explicit or external work may arrive while a plan runs or recovers.
                # Consume this valid decision without declaring the campaign done.
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.IDLE,
                        blocked_reason="",
                        blocker_kind="",
                        resume_status=None,
                        idle_context_fingerprint="",
                    )
                )
                return
            self._done_claim(role, state, context)
            return
        self._save_block_or_failure(state, result)

    def recover_postprocessing_failure(self, role: str) -> bool:
        """Consume only a proven host rejection; never invoke a model or publish its messages."""
        with ControlPlaneLock(role_lease_lock_path(self.config, role)):
            pending = read_pending_result(self.config, role)
            if not pending or "postprocessing_failure" not in pending:
                return False
            state = self.load_state(role)
            # A journal must not override unrelated human or hard system gates.
            allowed = state.status is WorkerStatus.IMPLEMENTING or (
                state.status is WorkerStatus.BLOCKED_SYSTEM
                and (
                    state.blocker_kind == "postprocessing_evidence"
                    or (
                        state.blocker_kind == "system"
                        and state.resume_status is WorkerStatus.IMPLEMENTING
                    )
                    or (
                        state.blocker_kind == "coordination_evidence"
                        and state.blocked_reason
                        == f"initial coordination evidence unavailable: unresolved active-agents writer activity for {role}"
                    )
                )
            )
            if not allowed:
                return True
            try:
                manifest, record, directory = retire_prepublication_marker(
                    self.config, role, state, pending
                )
                invocation_id = record["invocation_id"]
                if state.last_result_invocation_id == invocation_id:
                    if (
                        state.status is WorkerStatus.BLOCKED_SYSTEM
                        and state.blocker_kind == "postprocessing_evidence"
                    ):
                        self._save_state_preserving_pending_result(
                            replace(
                                state,
                                status=WorkerStatus.IMPLEMENTING,
                                blocked_reason="",
                                blocker_kind="",
                                resume_status=None,
                            )
                        )
                    clear_pending_result(self.config, role, invocation_id=invocation_id)
                    return True
                assert state.task is not None
                feedback = failure_feedback(self.config, state.task, manifest, record, directory)
                result = validate_agent_result(pending["result"])
                self._set_result_context(role, result)
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.IMPLEMENTING,
                        task=replace(state.task, feedback=feedback),
                        iteration=state.iteration + 1,
                        implementation_failures=state.implementation_failures + 1,
                        blocked_reason="",
                        blocker_kind="",
                        resume_status=None,
                    )
                )
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                # Reload in case the state commit succeeded but journal cleanup
                # failed. Its invocation ledger prevents a second counter update.
                current = self.load_state(role)
                if current.last_result_invocation_id == pending.get("invocation_id"):
                    self.alerts.emit(role=role, kind="postprocessing_cleanup", message=str(exc))
                    self._save_state_preserving_pending_result(current)
                    return True
                self._save_state_preserving_pending_result(
                    replace(
                        current,
                        status=WorkerStatus.BLOCKED_SYSTEM,
                        blocked_reason=f"postprocessing failure evidence unavailable: {exc}",
                        blocker_kind="postprocessing_evidence",
                        resume_status=None,
                    )
                )
            return True

    def _has_current_green_validation(self, role: str, state: WorkerState) -> bool:
        if state.task is None or not state.task_base_head:
            return False
        head = self._capture_task_base_head(role)
        try:
            load_validation_receipt(
                self.config,
                role,
                state.task.task_id,
                base_head=state.task_base_head,
                head=head,
            )
        except (FileNotFoundError, ValueError):
            return False
        return True

    def _implement(self, role: str, state: WorkerState, context: str) -> None:
        if self.recover_postprocessing_failure(role):
            return
        assert state.task is not None
        model = select_implementer_model(state, self.config)
        recovering_cloud = (self.root / "cloud-sessions" / f"{role}.json").exists()
        try:
            if not recovering_cloud:
                begin_checks(
                    self.config,
                    role,
                    state.task,
                    state.task_base_head,
                    self._capture_task_base_head(role),
                    recovered=read_pending_result(self.config, role) is not None,
                )
        except CoordinationCheckError as exc:
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=f"initial coordination evidence unavailable: {exc}",
                    blocker_kind="coordination_evidence",
                    resume_status=None,
                )
            )
            return
        try:
            result = self._invoke_or_recover(
                role,
                state,
                phase="implement",
                task_id=state.task.task_id,
                context=context,
                invoke=lambda: (
                    self.invoker.poll_existing_cloud(role, state.task, context)
                    if recovering_cloud
                    else self.invoker.implement(role, state, state.task, context, model)
                ),
            )
        except AgentInvocationError as exc:
            self._handle_invocation_error(role, state, "implement", exc, context)
            return
        if self.recover_postprocessing_failure(role):
            return
        if result.outcome == "ready_for_review":
            validation_head = self._capture_task_base_head(role)
            if recovering_cloud:
                try:
                    begin_checks(
                        self.config,
                        role,
                        state.task,
                        state.task_base_head,
                        validation_head,
                        recovered=True,
                    )
                except CoordinationCheckError as exc:
                    self._save_state_preserving_pending_result(
                        replace(
                            state,
                            status=WorkerStatus.BLOCKED_SYSTEM,
                            blocked_reason=f"post-adoption coordination evidence unavailable: {exc}",
                            blocker_kind="coordination_evidence",
                            resume_status=None,
                        )
                    )
                    return
            report = run_iteration_validation(
                self.config,
                role,
                state.task.task_id,
                base_head=state.task_base_head,
                head=validation_head,
                coordination_only=_is_coordination_only_task(state.task),
            )
            if report.system_error:
                self._save_state_preserving_pending_result(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_SYSTEM,
                        blocked_reason=report.system_error,
                        blocker_kind="system",
                        resume_status=WorkerStatus.IMPLEMENTING,
                    )
                )
                return
            if not report.passed:
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.IMPLEMENTING,
                        task=replace(state.task, feedback=report.feedback),
                        iteration=state.iteration + 1,
                        blocked_reason="",
                        blocker_kind="",
                        resume_status=None,
                    )
                )
                return
            if not self._accept_result(
                role, state, result, phase="implement", allow_issue_updates=False
            ):
                return
            try:
                if self._capture_task_base_head(role) != validation_head:
                    raise CoordinationCheckError("revision changed after validation")
                finish_checks(
                    self.config,
                    role,
                    state.task,
                    state.task_base_head,
                    validation_head,
                )
            except CoordinationCheckError as exc:
                self._save_state_preserving_pending_result(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_SYSTEM,
                        blocked_reason=f"final coordination evidence unavailable: {exc}",
                        blocker_kind="coordination_evidence",
                        resume_status=None,
                    )
                )
                return
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.REVIEWING,
                    task=replace(state.task, feedback=""),
                    iteration=state.iteration + 1,
                    implementation_failures=0,
                    blocked_reason="",
                    blocker_kind="",
                )
            )
            return
        if not self._accept_result(
            role, state, result, phase="implement", allow_issue_updates=False
        ):
            return
        if result.outcome == "continue":
            self.save_state(
                replace(state, status=WorkerStatus.IMPLEMENTING, iteration=state.iteration + 1)
            )
            return
        if result.outcome == "failed":
            failed_state = replace(
                state,
                status=WorkerStatus.IMPLEMENTING,
                implementation_failures=state.implementation_failures + 1,
                blocked_reason=result.summary,
            )
            if failed_state.implementation_failures >= self.config.astra_adjudication_after:
                self._adjudicate(role, failed_state, context)
            else:
                self.save_state(failed_state)
            return
        self._save_block_or_failure(state, result)

    def _review(self, role: str, state: WorkerState, context: str) -> None:
        assert state.task is not None
        from .publication_review import is_publication_review, record_review_result

        publication = is_publication_review(state.task)
        # An active delegation suspends ChatGPT receipts even when this task
        # requires a protected Codex gate. Never substitute Fable for that gate.
        fable_policy_active = temporary_fable_policy_active(self.config, role)
        using_fable = not publication and temporary_fable_review_active(
            self.config, role, state.task, self._last_provider(role)
        )
        # Ineligible checkpoints retain their mandatory Codex review route.
        # During Fable delegation, ChatGPT fallback receipts remain suspended.
        using_fallback = (
            not fable_policy_active
            and fallback_active(self.config)
            and self._last_provider(role) in {"claude", "claude_cloud"}
            and not any(
                marker in state.task.task_id.casefold()
                for marker in (
                    "final-verify",
                    "security",
                    "privacy",
                    "auth",
                    "credential",
                    "permission",
                    "policy",
                    "release",
                    "deploy",
                    "audit",
                    "approval",
                    "identity",
                    "secret",
                )
            )
            and "codex" not in state.task.prompt.casefold()
        )
        if using_fallback:
            try:
                result = load_hourly_review(
                    self.config,
                    role,
                    state,
                    head=self._capture_task_base_head(role),
                    implementation_provider=self._last_provider(role),
                )
            except (FileNotFoundError, ValueError) as exc:
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_SYSTEM,
                        blocked_reason=f"invalid independent fallback review: {exc}",
                        blocker_kind="review_fallback_invalid",
                        resume_status=WorkerStatus.REVIEWING,
                    )
                )
                return
            if result is None:
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_SYSTEM,
                        blocked_reason="Awaiting revision-bound hourly ChatGPT review receipt",
                        blocker_kind="review_fallback_wait",
                        resume_status=WorkerStatus.REVIEWING,
                    )
                )
                return
        else:
            model = (
                FABLE_REVIEW_MODEL
                if using_fable
                else (
                    SOL_MODEL
                    if publication or self._last_provider(role) == "codex"
                    else select_reviewer_model(state, self.config)
                )
            )
            try:
                result = self._invoke_or_recover(
                    role,
                    state,
                    phase="review",
                    task_id=state.task.task_id,
                    context=context,
                    invoke=lambda: self.invoker.review(role, state, state.task, context, model),
                )
            except AgentInvocationError as exc:
                self._handle_invocation_error(role, state, "review", exc, context)
                return
        if publication:
            record_review_result(self.config, role, state, result, persist=False)
        if not self._accept_result(
            role,
            state,
            result,
            phase="review",
            allow_issue_updates=result.outcome == "pass",
        ):
            return
        if publication:
            record_review_result(self.config, role, state, result)
        if result.outcome == "pass":
            self._record_completed_task(
                role,
                state.task,
                source="chatgpt_hourly_review" if using_fallback else "independent_review",
                invocation_id=result.invocation_id,
            )
            if result.issue_updates and not _updates_are_retest_handoff(result.issue_updates):
                issue_ids = ", ".join(update.issue_id for update in result.issue_updates)
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_USER,
                        blocked_reason=f"Awaiting external verification for {issue_ids}",
                        blocker_kind="retest",
                        resume_status=WorkerStatus.IDLE,
                    )
                )
                return
            self.save_state(
                WorkerState(
                    role=role,
                    status=WorkerStatus.IDLE,
                    claude_session_id=state.claude_session_id,
                    codex_session_id=state.codex_session_id,
                    idle_context_fingerprint="",
                )
            )
            return
        if result.outcome == "changes_required":
            feedback = "\n\n".join(part for part in (result.summary, result.next_action) if part)
            if publication:
                self._publication_remediation(state, feedback, result.invocation_id)
                return
            failures = state.review_failures + 1
            failed_state = replace(
                state,
                status=WorkerStatus.IMPLEMENTING,
                task=replace(state.task, feedback=feedback),
                review_failures=failures,
                blocked_reason="",
                blocker_kind="",
            )
            if (
                _is_coordination_only_task(state.task)
                and failures >= self.config.review_escalation_after
                and self._has_current_green_validation(role, state)
            ):
                # The review result is durably journaled. Commit its state transition
                # first so save_state records/applies that invocation and clears the
                # pending review receipt before the adjudication phase begins.
                self.save_state(failed_state)
                self._adjudicate(role, self.load_state(role), context)
            else:
                self.save_state(failed_state)
            return
        if result.outcome == "failed":
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.REVIEWING,
                    review_failures=state.review_failures + 1,
                    blocked_reason=result.summary,
                )
            )
            return
        self._save_block_or_failure(state, result)

    def _adjudicate(self, role: str, state: WorkerState, context: str) -> None:
        assert state.task is not None
        adjudication_context = context
        if _is_coordination_only_task(state.task) and self._has_current_green_validation(
            role, state
        ):
            try:
                evidence = build_review_evidence(
                    self.config,
                    role,
                    state,
                    state.task,
                    context,
                    "supervisor-adjudication-evidence",
                )
            except EvidenceError as exc:
                self._handle_invocation_error(
                    role,
                    state,
                    "adjudicate",
                    AgentInvocationError("supervisor", "incomplete_evidence", str(exc)),
                    context,
                )
                return
            if evidence.truncated:
                self._handle_invocation_error(
                    role,
                    state,
                    "adjudicate",
                    AgentInvocationError(
                        "supervisor",
                        "incomplete_evidence",
                        "Adjudication evidence is incomplete: "
                        + ", ".join(evidence.truncation_reasons),
                    ),
                    context,
                )
                return
            adjudication_context = (
                "Authoritative revision-bound supervisor evidence follows. "
                "The nested evidence-bundle invocation identifier is not the lead invocation binding.\n\n"
                + evidence.text
            )
            adjudication_context = SuppliedContext(adjudication_context, adjudication_context)
        try:
            result = self._invoke_or_recover(
                role,
                state,
                phase="adjudicate",
                task_id=state.task.task_id,
                context=adjudication_context,
                invoke=lambda: self.invoker.lead(
                    role, state, adjudication_context, task=state.task
                ),
            )
        except AgentInvocationError as exc:
            self._handle_invocation_error(role, state, "adjudicate", exc, context)
            return
        if not self._accept_result(
            role, state, result, phase="adjudicate", allow_issue_updates=False
        ):
            return
        if result.outcome == "assign":
            if not result.task_id or not result.task_prompt:
                raise ValueError("Astra adjudication requires task_id and task_prompt")
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=TaskItem(result.task_id, result.task_prompt),
                    task_base_head=self._capture_task_base_head(role),
                    implementation_failures=0,
                    review_failures=0,
                    blocked_reason="",
                )
            )
            return
        if result.outcome == "done":
            if (
                state.task is not None
                and _is_coordination_only_task(state.task)
                and self._has_current_green_validation(role, state)
            ):
                self._record_completed_task(
                    role,
                    state.task,
                    source="coordination_adjudication",
                    invocation_id=result.invocation_id,
                )
                self.save_state(
                    WorkerState(
                        role=role,
                        status=WorkerStatus.IDLE,
                        claude_session_id=state.claude_session_id,
                        codex_session_id=state.codex_session_id,
                        idle_context_fingerprint="",
                    )
                )
                return
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=(
                        "Astra cannot close an active product-code task before independent review"
                    ),
                )
            )
            return
        self._save_block_or_failure(state, result)

    @staticmethod
    def _resume_status_for_phase(state: WorkerState, phase: str) -> WorkerStatus:
        if phase == "implement":
            return WorkerStatus.IMPLEMENTING
        if phase == "review":
            return WorkerStatus.REVIEWING
        if phase in {"lead", "plan"}:
            return WorkerStatus.PLANNING
        if phase == "adjudicate":
            if state.status in {WorkerStatus.IMPLEMENTING, WorkerStatus.REVIEWING}:
                return state.status
            return state.resume_status or state.status
        return state.status

    def _save_transient_runner_failure(self, state: WorkerState, reason: str) -> None:
        if state.status is WorkerStatus.REVIEWING:
            failures = state.review_failures + 1
            retry_limit = max(4, self.config.review_escalation_after)
            updates = {"review_failures": failures}
        else:
            failures = state.implementation_failures + 1
            retry_limit = max(4, self.config.implementation_escalation_after)
            updates = {"implementation_failures": failures}
        retrying = failures < retry_limit
        if retrying:
            message = f"transient agent runtime failure; automatic retry {failures}/{retry_limit}: {reason}"
        else:
            message = f"agent runtime recovery exhausted after {failures} attempts: {reason}"
        self.save_state(
            replace(
                state,
                status=WorkerStatus.BLOCKED_SYSTEM,
                blocked_reason=message,
                blocker_kind="system",
                resume_status=state.status if retrying else None,
                **updates,
            )
        )

    def _handle_invocation_error(
        self,
        role: str,
        state: WorkerState,
        phase: str,
        exc: AgentInvocationError,
        context: str,
    ) -> None:
        resume_status = self._resume_status_for_phase(state, phase)
        if exc.kind == "paused":
            return
        if exc.kind == "incomplete_evidence":
            self._save_state_preserving_pending_result(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=exc.detail,
                    blocker_kind="incomplete_evidence",
                    resume_status=None,
                )
            )
            return
        if is_transient_runner_failure(exc.detail):
            recover_codex_windows_terminal_runner()
            self._save_transient_runner_failure(state, exc.detail)
            return
        if exc.provider == "openai":
            if exc.kind == "usage_limit":
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_SYSTEM,
                        blocked_reason=(
                            "invalid OpenAI provider classification: usage_limit; " + exc.detail
                        ),
                        blocker_kind="system",
                        resume_status=resume_status,
                    )
                )
                return
            if exc.kind in {"auth", "billing", "budget"}:
                blocker_kind = "auth" if exc.kind == "auth" else "human"
                self.alerts.emit(
                    role=role,
                    kind=f"openai_{exc.kind}",
                    message=f"{role} needs OpenAI {exc.kind} intervention: {exc.detail}",
                )
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_USER,
                        blocked_reason=f"OpenAI {exc.kind}: {exc.detail}",
                        blocker_kind=blocker_kind,
                        resume_status=resume_status,
                    )
                )
                return
            if exc.kind in {
                "rate_limit",
                "timeout",
                "transient",
                "invalid_output",
                "failed",
            }:
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_SYSTEM,
                        blocked_reason=f"OpenAI {exc.kind}: {exc.detail}",
                        blocker_kind="system",
                        resume_status=resume_status,
                    )
                )
                return
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=(
                        f"unsupported OpenAI failure classification {exc.kind!r}: {exc.detail}"
                    ),
                    blocker_kind="system",
                    resume_status=resume_status,
                )
            )
            return
        if exc.kind == "usage_limit":
            if not is_cli_usage_provider(exc.provider):
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_SYSTEM,
                        blocked_reason=(
                            f"invalid usage-limit provider {exc.provider!r}: {exc.detail}"
                        ),
                        blocker_kind="system",
                        resume_status=resume_status,
                    )
                )
                return
            budget = UsageBudget(
                self.config.banked_resets_remaining,
                self.config.reserve_last_reset,
            )
            decision = handle_usage_limit(
                budget,
                self.alerts,
                role=role,
                provider=exc.provider,
                reason=exc.detail,
                claude_available=False,
            )
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_USER if decision.blocked_user else state.status,
                    blocked_reason=(
                        f"{exc.provider} usage limit: {decision.action}; "
                        f"{decision.budget.banked_resets_remaining} banked resets recorded. {exc.detail}"
                    ),
                    blocker_kind="usage_limit",
                    resume_status=resume_status,
                )
            )
            return
        if exc.kind == "auth":
            self.alerts.emit(
                role=role,
                kind="auth",
                message=f"{role} needs account/login intervention for {exc.provider}: {exc.detail}",
            )
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_USER,
                    blocked_reason=exc.detail,
                    blocker_kind="auth",
                    resume_status=resume_status,
                )
            )
            return
        if phase == "implement":
            failed = replace(
                state,
                status=WorkerStatus.IMPLEMENTING,
                implementation_failures=state.implementation_failures + 1,
                blocked_reason=exc.detail,
            )
            if failed.implementation_failures >= self.config.astra_adjudication_after:
                self._adjudicate(role, failed, context)
            else:
                self.save_state(failed)
            return
        if phase == "review":
            failed = replace(
                state,
                status=WorkerStatus.REVIEWING,
                review_failures=state.review_failures + 1,
                blocked_reason=exc.detail,
            )
            if failed.review_failures >= self.config.astra_adjudication_after:
                self._adjudicate(role, failed, context)
            else:
                self.save_state(failed)
            return
        self.save_state(
            replace(
                state,
                status=WorkerStatus.BLOCKED_SYSTEM,
                blocked_reason=exc.detail,
                blocker_kind="system",
                resume_status=resume_status,
            )
        )

    def _save_block_or_failure(self, state: WorkerState, result: AgentResult) -> None:
        reason = result.blocker_reason or result.summary
        if is_transient_runner_failure(reason):
            recover_codex_windows_terminal_runner()
            self._save_transient_runner_failure(state, reason)
            return
        if result.outcome == "blocked_system" and not result.needs_user:
            phase = getattr(self._result_context, "phase", "")
            if not phase:
                phase = "review" if state.status is WorkerStatus.REVIEWING else "implement"
            record = self._blocker_record(state.role, state, result, phase)
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=reason,
                    blocker_kind="model_dependency",
                    resume_status=self._resume_status_for_phase(state, phase),
                    model_blocker_phase=phase,
                    model_blocker_feedback=blocker_feedback(result, phase),
                    model_blocker_fingerprint=(record.get("input_provenance") or {}).get(
                        "fingerprint", ""
                    ),
                )
            )
            return
        if result.outcome == "blocked_user" or result.needs_user:
            status = WorkerStatus.BLOCKED_USER
        else:
            status = WorkerStatus.BLOCKED_SYSTEM
        self.save_state(
            replace(
                state,
                status=status,
                blocked_reason=reason,
                blocker_kind="human" if status is WorkerStatus.BLOCKED_USER else "system",
                resume_status=state.status,
            )
        )
