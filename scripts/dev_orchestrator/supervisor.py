from __future__ import annotations

import hashlib
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from .alerts import AlertSink
from .completed_tasks import CompletedTaskLedger
from .completion import CompletionGate, prepare_final_review, recheck_final_completion
from .coordination import (
    _issue_status,
    active_owned_issues,
    apply_agent_updates,
    build_coordination_context,
    recover_coordination_transactions,
)
from .coordination_checks import CoordinationCheckError, begin_checks, finish_checks
from .evidence import EvidenceError, build_review_evidence
from .handoff import (
    clear_pending_result,
    read_pending_result,
    role_lease_lock_path,
    validate_handoff,
)
from .lifecycle import ControlPlaneLock
from .provider_routing import (
    FABLE_REVIEW_MODEL,
    temporary_fable_policy_active,
    temporary_fable_review_active,
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


class AgentInvoker(Protocol):
    def implement(self, role, state, task, context, model) -> AgentResult: ...

    def poll_existing_cloud(self, role, task, context) -> AgentResult: ...

    def review(self, role, state, task, context, model) -> AgentResult: ...

    def lead(self, role, state, context, task=None) -> AgentResult: ...


def _state_from_dict(role: str, data: dict | None) -> WorkerState:
    if not data:
        return WorkerState(role=role)
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

    def save_state(self, state: WorkerState) -> None:
        context_role = getattr(self._result_context, "role", "")
        invocation_id = getattr(self._result_context, "invocation_id", "")
        if invocation_id and context_role == state.role:
            state = replace(state, last_result_invocation_id=invocation_id)
        with ControlPlaneLock(self.root / "control-plane.lock"):
            self._state_store(state.role).write(state.to_dict())
        if invocation_id and context_role == state.role:
            clear_pending_result(self.config, state.role, invocation_id=invocation_id)
            self._result_context.role = ""
            self._result_context.invocation_id = ""

    def _save_state_preserving_pending_result(self, state: WorkerState) -> None:
        self._result_context.role = ""
        self._result_context.invocation_id = ""
        with ControlPlaneLock(self.root / "control-plane.lock"):
            self._state_store(state.role).write(state.to_dict())

    def enqueue(self, role: str, task: TaskItem) -> None:
        with ControlPlaneLock(self.root / "control-plane.lock"):
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
        with ControlPlaneLock(self.root / "control-plane.lock"):
            store = self._queue_store(role)
            queue = list(store.read(default=[]))
            for index, payload in enumerate(queue):
                task = TaskItem(**payload)
                if self._task_is_deferred(task.task_id):
                    continue
                queue.pop(index)
                store.write(queue)
                return task
            return None

    def _set_result_context(self, role: str, result: AgentResult) -> None:
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

    def _invoke_or_recover(
        self, role: str, state: WorkerState, *, phase: str, task_id: str, invoke
    ) -> AgentResult:
        result = self._recover_result(role, state, phase=phase, task_id=task_id)
        if result is None:
            current = AtomicJsonStore(self.root / "orchestrator-config.json").read(default=None)
            if self.config.observe_only or (
                current is not None
                and (not isinstance(current, dict) or current.get("observe_only") is not False)
            ):
                raise AgentInvocationError("supervisor", "paused", "new model phase paused")
            result = invoke()
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
        with ControlPlaneLock(self.root / "control-plane.lock"):
            state = self.load_state(role)
            if state.status is WorkerStatus.BLOCKED_USER or (
                state.status is WorkerStatus.BLOCKED_SYSTEM and state.resume_status is None
            ):
                self._save_state_preserving_pending_result(state)
                self.alerts.emit(role=role, kind="workstream_failure", message=message)
                return
            resume_status = (
                state.resume_status if state.status is WorkerStatus.BLOCKED_SYSTEM else state.status
            )
            self._save_state_preserving_pending_result(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=message,
                    blocker_kind="system",
                    resume_status=resume_status,
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
        state = self.load_state(role)
        if state.status is WorkerStatus.BLOCKED_SYSTEM:
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
            queued = self._dequeue(role)
            if queued is not None:
                state = replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=queued,
                    task_base_head=self._capture_task_base_head(role),
                    blocked_reason="",
                    blocker_kind="",
                    resume_status=None,
                    idle_context_fingerprint="",
                )
                self.save_state(state)
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

        task_context_text = ""
        if state.task is not None:
            task_context_text = f"{state.task.task_id}\n{state.task.prompt}\n{state.task.feedback}"
        context = build_coordination_context(
            self.root,
            role,
            deferred_issue_ids=self.config.deferred_issue_ids,
            deferred_task_prefixes=self.config.deferred_task_prefixes,
            task_text=task_context_text,
        )
        if state.task is None:
            queued = self._dequeue(role)
            if queued is not None:
                state = replace(
                    state,
                    task=queued,
                    task_base_head=self._capture_task_base_head(role),
                    status=WorkerStatus.IMPLEMENTING,
                    idle_context_fingerprint="",
                )
                context = build_coordination_context(
                    self.root,
                    role,
                    deferred_issue_ids=self.config.deferred_issue_ids,
                    deferred_task_prefixes=self.config.deferred_task_prefixes,
                    task_text=f"{queued.task_id}\n{queued.prompt}\n{queued.feedback}",
                )
            else:
                # IDLE is a transient scheduling state in an active campaign.
                # After a reviewed task clears, immediately ask the planner for
                # the next bounded task. Only DONE or a real blocker parks work.
                self._plan(role, state, context)
                return

        if state.task.task_id == f"FINAL-VERIFY-{role}":
            self._final_verify(role, state, context)
            return
        if state.status is WorkerStatus.REVIEWING:
            self._review(role, state, context)
            return
        self._implement(role, state, context)

    def _accept_result(
        self,
        role: str,
        state: WorkerState,
        result: AgentResult,
        *,
        phase: str,
        allow_issue_updates: bool,
    ) -> bool:
        try:
            validate_phase_outcome(result, phase)
        except ValueError as exc:
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=f"invalid {phase} agent result: {exc}",
                    blocker_kind="system",
                    resume_status=state.status,
                )
            )
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
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason="invalid blocked_user agent result",
                    blocker_kind="system",
                    resume_status=state.status,
                )
            )
            return False
        try:
            apply_agent_updates(
                self.root,
                role,
                result,
                allow_issue_updates=allow_issue_updates,
                task_id=state.task.task_id if state.task else "",
            )
        except (ValueError, OSError) as exc:
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=f"agent result rejected by supervisor safety gate: {exc}",
                    blocker_kind="system",
                    resume_status=state.status,
                )
            )
            return False
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
                    task_base_head=self._capture_task_base_head(role),
                    blocked_reason="",
                )
            )
            return
        final_task = TaskItem(
            f"FINAL-VERIFY-{role}",
            "Independently verify final workstream readiness, tests, CI, docs, and safety gates.",
        )
        state = replace(
            state,
            status=WorkerStatus.IMPLEMENTING,
            task=final_task,
            task_base_head=self._capture_task_base_head(role),
            blocked_reason="",
            blocker_kind="",
            resume_status=None,
        )
        # This reserved task prepares deterministic final evidence, never model
        # implementation. Persist it before preparation so restart preserves phase.
        self.save_state(state)
        self._final_verify(role, state, context)

    def _block_final_gate(self, state: WorkerState, gate: CompletionGate) -> None:
        reason = gate.system_error or "; ".join(gate.reasons)
        if gate.system_error or gate.blocker_kind == "incomplete_evidence":
            self._save_state_preserving_pending_result(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=reason,
                    blocker_kind="incomplete_evidence" if not gate.system_error else "validation",
                    resume_status=None,
                )
            )
        elif gate.needs_user:
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_USER,
                    task=None,
                    blocked_reason=reason,
                    blocker_kind=gate.blocker_kind or "human",
                    resume_status=WorkerStatus.PLANNING,
                )
            )
        else:
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=TaskItem(
                        f"FINAL-{state.role}",
                        "Satisfy deterministic final completion gates: " + reason,
                    ),
                    task_base_head=self._capture_task_base_head(state.role),
                    blocked_reason="",
                    blocker_kind="",
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

    def _final_verify(self, role: str, state: WorkerState, context: str) -> None:
        assert state.task is not None and state.task.task_id == f"FINAL-VERIFY-{role}"
        if state.status is WorkerStatus.IMPLEMENTING:
            try:
                begin_checks(
                    self.config,
                    role,
                    state.task,
                    state.task_base_head,
                    self._capture_task_base_head(role),
                    recovered=True,
                )
                gate = prepare_final_review(self.config, role, head=state.task_base_head)
                if not gate.ready:
                    self._block_final_gate(state, gate)
                    return
                finish_checks(
                    self.config, role, state.task, state.task_base_head, state.task_base_head
                )
            except CoordinationCheckError as exc:
                self._handle_invocation_error(
                    role,
                    state,
                    "review",
                    AgentInvocationError("supervisor", "incomplete_evidence", str(exc)),
                    context,
                )
                return
            state = replace(state, status=WorkerStatus.REVIEWING)
            self.save_state(state)
        try:
            # Recovery must verify the same complete evidence before applying a
            # durable reviewer result, even when no fresh model call is needed.
            self._require_final_evidence(role, state, context)
            result = self._invoke_or_recover(
                role,
                state,
                phase="review",
                task_id=state.task.task_id,
                invoke=lambda: self.invoker.review(role, state, state.task, context, SOL_MODEL),
            )
        except EvidenceError as exc:
            self._handle_invocation_error(
                role,
                state,
                "review",
                AgentInvocationError("supervisor", "incomplete_evidence", str(exc)),
                context,
            )
            return
        except AgentInvocationError as exc:
            self._handle_invocation_error(role, state, "review", exc, context)
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
        planning_context = context + self._completion_context(role)
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
                invoke=lambda: self.invoker.lead(
                    role, replace(state, status=WorkerStatus.PLANNING), planning_context
                ),
            )
        except AgentInvocationError as exc:
            self._handle_invocation_error(role, state, "plan", exc, context)
            return
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
                )
            )
            return
        if result.outcome == "done":
            self.save_state(state)
            self._done_claim(role, self.load_state(role), context)
            return
        self._save_block_or_failure(state, result)

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
                invoke=lambda: (
                    self.invoker.poll_existing_cloud(role, state.task, context)
                    if recovering_cloud
                    else self.invoker.implement(role, state, state.task, context, model)
                ),
            )
        except AgentInvocationError as exc:
            self._handle_invocation_error(role, state, "implement", exc, context)
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
        # An active delegation suspends ChatGPT receipts even when this task
        # requires a protected Codex gate. Never substitute Fable for that gate.
        fable_policy_active = temporary_fable_policy_active(self.config, role)
        using_fable = temporary_fable_review_active(
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
                    if self._last_provider(role) == "codex"
                    else select_reviewer_model(state, self.config)
                )
            )
            try:
                result = self._invoke_or_recover(
                    role,
                    state,
                    phase="review",
                    task_id=state.task.task_id,
                    invoke=lambda: self.invoker.review(role, state, state.task, context, model),
                )
            except AgentInvocationError as exc:
                self._handle_invocation_error(role, state, "review", exc, context)
                return
        if not self._accept_result(
            role,
            state,
            result,
            phase="review",
            allow_issue_updates=result.outcome == "pass",
        ):
            return
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
        try:
            result = self._invoke_or_recover(
                role,
                state,
                phase="adjudicate",
                task_id=state.task.task_id,
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
