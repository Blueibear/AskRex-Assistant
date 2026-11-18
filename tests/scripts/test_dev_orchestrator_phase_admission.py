from __future__ import annotations

import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from scripts.dev_orchestrator import cli as cli_module
from scripts.dev_orchestrator import supervisor as supervisor_module
from scripts.dev_orchestrator.cli import initialize_runtime, set_observe_only
from scripts.dev_orchestrator.lifecycle import ControlPlaneLock
from scripts.dev_orchestrator.runner import AgentInvocationError
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import AgentResult, TaskItem, WorkerState, WorkerStatus


@pytest.fixture
def active_config(tmp_path: Path):
    root, backend, mobile, frozen = (
        tmp_path / name for name in ("coordination", "backend", "mobile", "frozen")
    )
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    initialize_runtime(root, backend, mobile, frozen)
    return set_observe_only(root, False)


def _result(role="backend", invocation_id=""):
    return AgentResult(
        "continue",
        "bounded work",
        "continue",
        role=role,
        task_id=role + "-task",
        invocation_id=invocation_id,
    )


def _invoke(supervisor, callback, role="backend"):
    task = TaskItem(role + "-task", "bounded work")
    state = WorkerState(role=role, status=WorkerStatus.IMPLEMENTING, task=task)
    return supervisor._invoke_or_recover(
        role, state, phase="implement", task_id=task.task_id, invoke=callback
    )


def _thread(target, name="phase-worker"):
    values = []

    def run():
        try:
            values.append(target())
        except BaseException as error:
            values.append(error)

    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    return thread, values


def test_pause_wins_final_admission_after_stale_active_snapshot(active_config, monkeypatch):
    supervisor = Supervisor(active_config, object())
    boundary = threading.Event()
    calls = []
    root = active_config.coordination_root
    # The worker already has an active config snapshot. Pause wins the final
    # shared admission lock before the worker can authorize a model callback.
    assert active_config.observe_only is False

    @contextmanager
    def observed_lock(path, **kwargs):
        if threading.current_thread().name == "pause-race-worker":
            boundary.set()
        with ControlPlaneLock(path, **kwargs):
            yield

    monkeypatch.setattr(supervisor_module, "ControlPlaneLock", observed_lock)
    policy_may_return = threading.Event()
    pause_committed = threading.Event()
    original_read = AtomicJsonStore.read

    def held_policy_read(store, default=None):
        value = original_read(store, default)
        if (
            threading.current_thread().name == "pause-race-worker"
            and store.path == root / "orchestrator-config.json"
        ):
            # On the old path this captures false before pause and returns it
            # only after pause commits. The fixed path cannot read until it
            # acquires admission ownership, and therefore observes true.
            boundary.set()
            assert policy_may_return.wait(5)
        return value

    monkeypatch.setattr(AtomicJsonStore, "read", held_policy_read)

    def model():
        calls.append(pause_committed.is_set())
        return _result()

    worker = None
    try:
        with ControlPlaneLock(root / "control-plane.lock"):
            worker, values = _thread(lambda: _invoke(supervisor, model), "pause-race-worker")
            assert boundary.wait(5), "worker never reached the admission boundary"
            set_observe_only(root, True)
            pause_committed.set()
            policy_may_return.set()
    finally:
        policy_may_return.set()
        if worker is not None:
            worker.join(5)
    assert not worker.is_alive()
    assert calls == []
    assert len(values) == 1 and isinstance(values[0], AgentInvocationError)
    assert values[0].kind == "paused"
    assert not (root / "phase-admissions" / "backend.json").exists()


def test_durable_admission_wins_pause_before_callback_starts(active_config, monkeypatch):
    supervisor = Supervisor(active_config, object())
    admitted = threading.Event()
    allow_callback = threading.Event()
    calls = []
    original = getattr(supervisor, "_admit_phase", None)
    assert callable(original), "model admission must have an explicit atomic boundary"

    def wait_after_admission(*args, **kwargs):
        original(*args, **kwargs)
        admitted.set()
        assert allow_callback.wait(5), "test did not release an already admitted phase"

    monkeypatch.setattr(supervisor, "_admit_phase", wait_after_admission)

    def model():
        calls.append("model")
        return _result()

    worker, values = _thread(lambda: _invoke(supervisor, model))
    try:
        assert admitted.wait(5)
        marker = AtomicJsonStore(
            active_config.coordination_root / "phase-admissions" / "backend.json"
        ).read()
        assert marker["role"] == "backend"
        assert marker["phase"] == "implement"
        assert marker["task_id"] == "backend-task"
        assert marker["admission_id"]
        assert calls == []
        set_observe_only(active_config.coordination_root, True)
    finally:
        allow_callback.set()
        worker.join(5)
    assert not worker.is_alive()
    assert values == [_result()]
    assert calls == ["model"]


def test_peer_admissions_and_pause_do_not_wait_for_inflight_model(active_config):
    supervisor = Supervisor(active_config, object())
    started = {role: threading.Event() for role in ("backend", "mobile")}
    release = threading.Event()
    threads = []
    outputs = []
    paused = threading.Event()

    def model(role):
        started[role].set()
        assert release.wait(5), "test did not release the model"
        return _result(role)

    def pause():
        set_observe_only(active_config.coordination_root, True)
        paused.set()

    try:
        for role in ("backend", "mobile"):
            worker, values = _thread(
                lambda role=role: _invoke(supervisor, lambda: model(role), role)
            )
            threads.append(worker)
            outputs.append(values)
        assert started["backend"].wait(5)
        assert started["mobile"].wait(5), "backend model serialized mobile admission"
        pauser, pause_values = _thread(pause, "pauser")
        threads.append(pauser)
        assert paused.wait(5), "model invocation held the global pause lock"
        assert not release.is_set()
        with pytest.raises(AgentInvocationError) as failure:
            _invoke(supervisor, lambda: pytest.fail("paused phase dispatched"))
        assert failure.value.kind == "paused"
    finally:
        release.set()
        for worker in threads:
            worker.join(5)
    assert all(not worker.is_alive() for worker in threads)
    assert outputs == [[_result("backend")], [_result("mobile")]]
    assert pause_values == [None]


@pytest.mark.parametrize("policy", [None, "{", "[]", '{"observe_only":"false"}'])
def test_missing_or_malformed_live_policy_never_admits(active_config, policy):
    root = active_config.coordination_root
    path = root / "orchestrator-config.json"
    if policy is None:
        path.unlink()
    else:
        path.write_text(policy, encoding="utf-8")
    supervisor = Supervisor(active_config, object())
    with pytest.raises(AgentInvocationError) as failure:
        _invoke(supervisor, lambda: pytest.fail("unavailable policy authorized a model"))
    assert failure.value.kind == "paused"
    assert not (root / "phase-admissions" / "backend.json").exists()


def test_admission_write_failure_never_invokes(active_config, monkeypatch):
    supervisor = Supervisor(active_config, object())
    original = AtomicJsonStore.write

    def fail_admission(store, value):
        if store.path.parent.name == "phase-admissions":
            raise OSError("admission disk unavailable")
        return original(store, value)

    monkeypatch.setattr(AtomicJsonStore, "write", fail_admission)
    with pytest.raises(OSError, match="admission disk unavailable"):
        _invoke(supervisor, lambda: pytest.fail("undurable admission invoked a model"))


def test_crashed_phase_audit_is_neither_permission_nor_busy_lock(active_config):
    root = active_config.coordination_root
    marker = AtomicJsonStore(root / "phase-admissions" / "backend.json")
    supervisor = Supervisor(active_config, object())

    def interrupted():
        raise RuntimeError("simulated process interruption")

    with pytest.raises(RuntimeError, match="simulated process interruption"):
        _invoke(supervisor, interrupted)
    original = marker.read()
    assert original["admission_id"]
    set_observe_only(root, True)
    restarted = Supervisor(active_config, object())
    with pytest.raises(AgentInvocationError) as failure:
        _invoke(restarted, lambda: pytest.fail("old admission was reused"))
    assert failure.value.kind == "paused"
    assert marker.read() == original
    # Audit corruption also cannot become a stale busy lock on restart.
    marker.path.write_text("{", encoding="utf-8")
    set_observe_only(root, False)
    assert _invoke(restarted, _result) == _result()
    assert marker.read()["admission_id"] != original["admission_id"]


@pytest.mark.parametrize("missing_policy", [False, True])
def test_durable_result_recovers_without_new_admission(active_config, missing_policy):
    root = active_config.coordination_root
    invocation_id = "cd990f23-612d-42c1-8874-6a55efe9b75b"
    result = _result(invocation_id=invocation_id)
    journal = AtomicJsonStore(root / "handoff" / "backend.json")
    pending = {
        "role": "backend",
        "phase": "implement",
        "task_id": "backend-task",
        "invocation_id": invocation_id,
        "result": asdict(result),
    }
    journal.write({"owner": "supervisor", "pending_result": pending})
    pending_bytes = journal.path.read_bytes()
    set_observe_only(root, True)
    if missing_policy:
        (root / "orchestrator-config.json").unlink()
    supervisor = Supervisor(active_config, object())
    assert _invoke(supervisor, lambda: pytest.fail("durable result reinvoked")) == result
    assert journal.path.read_bytes() == pending_bytes
    assert not (root / "phase-admissions" / "backend.json").exists()
    state = WorkerState(
        role="backend",
        status=WorkerStatus.REVIEWING,
        task=TaskItem("backend-task", "bounded work"),
    )
    supervisor.save_state(state)
    assert "pending_result" not in journal.read()
    assert supervisor.load_state("backend") == replace(
        state, last_result_invocation_id=invocation_id
    )


def test_scheduler_pause_wins_dispatch_admission(active_config, monkeypatch):
    root = active_config.coordination_root
    boundary = threading.Event()
    submitted = []

    @contextmanager
    def observed_lock(path, **kwargs):
        if threading.current_thread().name == "scheduler-race":
            boundary.set()
        with ControlPlaneLock(path, **kwargs):
            yield

    class Pool:
        def __init__(self, **kwargs):
            assert kwargs["max_workers"] == 2

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def submit(self, _function, _root, role, **_kwargs):
            submitted.append(role)
            boundary.set()
            future = Future()
            future.set_result(None)
            return future

    monkeypatch.setattr(cli_module, "ControlPlaneLock", observed_lock)
    monkeypatch.setattr(cli_module, "ThreadPoolExecutor", Pool)
    with ControlPlaneLock(root / "control-plane.lock"):
        worker, values = _thread(
            lambda: cli_module.run_loop(active_config, max_cycles=1), "scheduler-race"
        )
        assert boundary.wait(5), "scheduler never reached its admission boundary"
        set_observe_only(root, True)
    worker.join(5)
    assert not worker.is_alive()
    assert values == [None]
    assert submitted == []


def test_queued_role_rechecks_pause_under_dispatch_lock(active_config, monkeypatch):
    root = active_config.coordination_root
    boundary = threading.Event()
    entered = []

    @contextmanager
    def observed_lock(path, **kwargs):
        if threading.current_thread().name == "role-race":
            boundary.set()
        with ControlPlaneLock(path, **kwargs):
            yield

    class Worker:
        def __init__(self, *_args):
            pass

        def run_role(self, role):
            entered.append(role)
            boundary.set()

    monkeypatch.setattr(cli_module, "ControlPlaneLock", observed_lock)
    monkeypatch.setattr(cli_module, "Supervisor", Worker)
    monkeypatch.setattr(cli_module, "validate_handoff", lambda *_args: None)
    with ControlPlaneLock(root / "control-plane.lock"):
        worker, values = _thread(
            lambda: cli_module._run_role_cycle(root, "backend", invoker=object()), "role-race"
        )
        assert boundary.wait(5), "queued worker never reached its admission boundary"
        set_observe_only(root, True)
    worker.join(5)
    assert not worker.is_alive()
    assert values == [None]
    assert entered == []


@contextmanager
def _held_by_another_process(path: Path):
    ready = path.with_suffix(".child-ready")
    command = (
        "import sys; from pathlib import Path; "
        "from scripts.dev_orchestrator.lifecycle import ControlPlaneLock; "
        "lock = ControlPlaneLock(Path(sys.argv[1])); lock.__enter__(); "
        "Path(sys.argv[2]).write_text('locked', encoding='utf-8'); "
        "sys.stdin.readline(); lock.__exit__(None, None, None)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", command, str(path), str(ready)],
        cwd=Path(__file__).resolve().parents[2],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "other process never acquired the real OS lock"
        yield
    finally:
        try:
            _stdout, stderr = child.communicate(input="\n", timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            _stdout, stderr = child.communicate(timeout=5)
            pytest.fail("test lock holder did not exit after release")
        assert child.returncode == 0, stderr


@pytest.mark.parametrize("operation", ["pause", "admit"])
def test_short_transaction_waits_for_real_process_contention(active_config, monkeypatch, operation):
    from scripts.dev_orchestrator import lifecycle

    root = active_config.coordination_root
    supervisor = Supervisor(active_config, object())
    contention = threading.Event()
    original = lifecycle.SupervisorLock.acquire

    def observed_acquire(lock):
        try:
            return original(lock)
        except RuntimeError as error:
            if isinstance(error.__cause__, BlockingIOError):
                contention.set()
            raise

    monkeypatch.setattr(lifecycle.SupervisorLock, "acquire", observed_acquire)

    def action():
        if operation == "pause":
            return set_observe_only(root, True)
        return _invoke(supervisor, _result)

    with _held_by_another_process(root / "control-plane.lock"):
        worker, values = _thread(action)
        assert contention.wait(5), "test did not exercise OS-level lock contention"
    worker.join(5)
    assert not worker.is_alive()
    assert len(values) == 1 and not isinstance(values[0], BaseException)
    if operation == "pause":
        assert values[0].observe_only is True
    else:
        assert values[0] == _result()


def test_process_wait_expiry_releases_thread_lock_and_handles(active_config):
    path = active_config.coordination_root / "control-plane.lock"
    with _held_by_another_process(path):
        lock = ControlPlaneLock(path, timeout_seconds=0.03)
        with pytest.raises(RuntimeError) as failure:
            with lock:
                pytest.fail("contended transaction acquired the lock")
        assert isinstance(failure.value.__cause__, BlockingIOError)

    def reacquire():
        with lock:
            return "acquired after expiry"

    worker, values = _thread(reacquire)
    worker.join(5)
    assert not worker.is_alive(), "failed acquisition leaked the thread lock"
    assert values == ["acquired after expiry"]


def test_default_role_lease_lock_still_rejects_immediately(active_config, monkeypatch):
    from scripts.dev_orchestrator import lifecycle

    path = active_config.coordination_root / "handoff" / "backend.lease.lock"
    with lifecycle.SupervisorLock(path):
        monkeypatch.setattr(
            lifecycle.time, "sleep", lambda *_args: pytest.fail("lease lock unexpectedly waited")
        )
        with pytest.raises(RuntimeError) as failure:
            with ControlPlaneLock(path):
                pytest.fail("competing lease owner was admitted")
        assert isinstance(failure.value.__cause__, BlockingIOError)
