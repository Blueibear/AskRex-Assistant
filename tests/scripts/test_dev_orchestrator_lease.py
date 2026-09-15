from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.dev_orchestrator.cli import initialize_runtime
from scripts.dev_orchestrator.handoff import (
    HandoffRequired,
    accept_handoff,
    advance_handoff,
    clear_pending_result,
    read_pending_result,
    request_handoff,
    validate_handoff,
    validate_worker_checkout,
    verify_invocation_commits,
    worker_ack_handoff,
)
from scripts.dev_orchestrator.types import OrchestratorConfig
from tests.scripts.test_dev_orchestrator_safety import _git_repo


def _config(tmp_path: Path) -> OrchestratorConfig:
    root = tmp_path / "coord"
    root.mkdir()
    backend = tmp_path / "backend"
    _git_repo(backend)
    mobile = tmp_path / "mobile"
    _git_repo(mobile)
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    return replace(initialize_runtime(root, backend, mobile, frozen), observe_only=False)


def test_worker_checkout_requires_linked_nonprotected_branch_and_expected_origin(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    backend = config.backend_root
    assert backend is not None
    snapshot = validate_worker_checkout(config, "backend")
    assert snapshot.branch.startswith("worker/")
    assert snapshot.base_branch == "master"
    assert snapshot.upstream.startswith("origin/")

    subprocess.run(["git", "checkout", "--detach", "-q"], cwd=backend, check=True)
    with pytest.raises(HandoffRequired, match="detached"):
        validate_worker_checkout(config, "backend")


def test_worker_checkout_rejects_primary_or_protected_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "backend"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/Blueibear/AskRex-Assistant.git"],
        cwd=repo,
        check=True,
    )
    config = OrchestratorConfig(
        tmp_path / "coord", repo, tmp_path / "mobile", tmp_path / "frozen", observe_only=False
    )
    with pytest.raises(HandoffRequired, match="protected|linked worktree"):
        validate_worker_checkout(config, "backend")


def test_handoff_nonce_is_cli_option_safe(tmp_path: Path, monkeypatch) -> None:
    import json

    from scripts.dev_orchestrator import handoff

    config = _config(tmp_path)
    monkeypatch.setattr(handoff.secrets, "token_urlsafe", lambda _size: "-unsafe-option-value")
    request = handoff.request_handoff(config, "backend", worker_session="browser-a")
    nonce = json.loads(request.read_text(encoding="utf-8"))["nonce"]
    assert nonce
    assert not nonce.startswith("-")
    assert all(char in "0123456789abcdef" for char in nonce)


def test_handoff_requires_matching_worker_nonce_and_creates_supervisor_lease(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    request = request_handoff(config, "backend", worker_session="backend-browser-session")
    payload = json.loads(request.read_text(encoding="utf-8"))
    nonce = payload["nonce"]

    with pytest.raises(HandoffRequired, match="nonce"):
        worker_ack_handoff(
            config,
            "backend",
            nonce="wrong",
            source="trusted-operator",
            worker_session="backend-browser-session",
        )

    worker_ack_handoff(
        config,
        "backend",
        nonce=nonce,
        source="trusted-operator",
        worker_session="backend-browser-session",
    )
    lease = accept_handoff(config, "backend", nonce=nonce, source="trusted-operator")
    record = json.loads(lease.read_text(encoding="utf-8"))
    assert record["owner"] == "supervisor"
    assert record["handoff_nonce"] == nonce
    validate_handoff(config, "backend")


def test_invocation_commits_require_unique_orchestrator_trailer(tmp_path: Path) -> None:
    config = _config(tmp_path)
    repo = config.backend_root
    assert repo is not None
    pre = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "worker.txt").write_text("ok\n", encoding="utf-8")
    subprocess.run(["git", "add", "worker.txt"], cwd=repo, check=True)
    invocation = "inv-123"
    subprocess.run(
        ["git", "commit", "-qm", f"worker change\n\nAskRex-Orchestrator-Invocation: {invocation}"],
        cwd=repo,
        check=True,
    )
    post = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    verify_invocation_commits(repo, pre, post, invocation)


def test_invocation_commit_without_trailer_is_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)
    repo = config.backend_root
    assert repo is not None
    pre = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "browser.txt").write_text("race\n", encoding="utf-8")
    subprocess.run(["git", "add", "browser.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "browser commit"], cwd=repo, check=True)
    post = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    with pytest.raises(HandoffRequired, match="invocation trailer"):
        verify_invocation_commits(repo, pre, post, "inv-123")


def test_validate_handoff_rejects_changed_head_after_lease(tmp_path: Path) -> None:
    config = _config(tmp_path)
    repo = config.backend_root
    assert repo is not None
    request = request_handoff(config, "backend", worker_session="backend-browser-session")
    nonce = json.loads(request.read_text(encoding="utf-8"))["nonce"]
    worker_ack_handoff(
        config,
        "backend",
        nonce=nonce,
        source="trusted-operator",
        worker_session="backend-browser-session",
    )
    accept_handoff(config, "backend", nonce=nonce, source="trusted-operator")
    (repo / "race.txt").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "race.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "unexpected"], cwd=repo, check=True)
    with pytest.raises(HandoffRequired, match="HEAD changed"):
        validate_handoff(config, "backend")


def test_worker_checkout_rejects_task_branch_tracking_protected_upstream(tmp_path: Path) -> None:
    config = _config(tmp_path)
    backend = config.backend_root
    assert backend is not None
    subprocess.run(
        ["git", "branch", "--set-upstream-to", "origin/master"],
        cwd=backend,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    with pytest.raises(HandoffRequired, match="matching task branch"):
        validate_worker_checkout(config, "backend")


def test_mobile_worker_checkout_rejects_task_branch_tracking_main(tmp_path: Path) -> None:
    config = _config(tmp_path)
    mobile = config.mobile_root
    assert mobile is not None
    subprocess.run(
        ["git", "branch", "--set-upstream-to", "origin/main"],
        cwd=mobile,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    with pytest.raises(HandoffRequired, match="matching task branch"):
        validate_worker_checkout(config, "mobile")


def test_new_handoff_request_invalidates_prior_acknowledgment(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first = request_handoff(config, "backend", worker_session="backend-browser-session")
    nonce_a = json.loads(first.read_text(encoding="utf-8"))["nonce"]
    worker_ack_handoff(
        config,
        "backend",
        nonce=nonce_a,
        source="trusted-operator",
        worker_session="backend-browser-session",
    )

    second = request_handoff(config, "backend", worker_session="backend-browser-session")
    nonce_b = json.loads(second.read_text(encoding="utf-8"))["nonce"]
    assert nonce_b != nonce_a
    with pytest.raises(HandoffRequired, match="current supervisor request"):
        accept_handoff(config, "backend", nonce=nonce_a, source="trusted-operator")

    worker_ack_handoff(
        config,
        "backend",
        nonce=nonce_b,
        source="trusted-operator",
        worker_session="backend-browser-session",
    )
    lease = accept_handoff(config, "backend", nonce=nonce_b, source="trusted-operator")
    assert lease.is_file()
    assert not second.exists()
    assert not (config.coordination_root / "handoff" / "backend.ack.json").exists()


def test_handoff_binds_expected_browser_worker_session(tmp_path: Path) -> None:
    config = _config(tmp_path)
    request = request_handoff(config, "backend", worker_session="backend-browser-session-1")
    payload = json.loads(request.read_text(encoding="utf-8"))
    nonce = payload["nonce"]
    assert payload["worker_session"] == "backend-browser-session-1"

    with pytest.raises(HandoffRequired, match="worker session"):
        worker_ack_handoff(
            config,
            "backend",
            nonce=nonce,
            source="trusted-operator",
            worker_session="wrong-session",
        )

    worker_ack_handoff(
        config,
        "backend",
        nonce=nonce,
        source="trusted-operator",
        worker_session="backend-browser-session-1",
    )
    lease = accept_handoff(config, "backend", nonce=nonce, source="trusted-operator")
    record = json.loads(lease.read_text(encoding="utf-8"))
    assert record["worker_session"] == "backend-browser-session-1"


def test_worker_checkout_rejects_spoofed_github_host_and_prefixed_path(tmp_path: Path) -> None:
    config = _config(tmp_path)
    backend = config.backend_root
    assert backend is not None

    subprocess.run(
        [
            "git",
            "remote",
            "set-url",
            "origin",
            "https://attacker.example/github.com/blueibear/askrex-assistant.git",
        ],
        cwd=backend,
        check=True,
    )
    with pytest.raises(HandoffRequired, match="repository mismatch"):
        validate_worker_checkout(config, "backend")

    subprocess.run(
        [
            "git",
            "remote",
            "set-url",
            "origin",
            "https://github.com/prefix/blueibear/askrex-assistant.git",
        ],
        cwd=backend,
        check=True,
    )
    with pytest.raises(HandoffRequired, match="repository mismatch"):
        validate_worker_checkout(config, "backend")


def test_new_request_revokes_previously_accepted_lease(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first = request_handoff(config, "backend", worker_session="session-a")
    nonce_a = json.loads(first.read_text(encoding="utf-8"))["nonce"]
    worker_ack_handoff(
        config, "backend", nonce=nonce_a, source="trusted-operator", worker_session="session-a"
    )
    lease = accept_handoff(config, "backend", nonce=nonce_a, source="trusted-operator")
    assert lease.exists()
    validate_handoff(config, "backend")

    second = request_handoff(config, "backend", worker_session="session-b")
    assert not lease.exists()
    assert second.exists()
    with pytest.raises(HandoffRequired, match="pending|missing"):
        validate_handoff(config, "backend")


def test_handoff_transitions_wait_for_control_plane_lock(tmp_path: Path) -> None:
    import time
    from concurrent.futures import ThreadPoolExecutor

    from scripts.dev_orchestrator.handoff import request_handoff, role_lease_lock_path
    from scripts.dev_orchestrator.lifecycle import ControlPlaneLock

    root = tmp_path / "coord"
    root.mkdir()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(root, backend, mobile, frozen)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with ControlPlaneLock(role_lease_lock_path(config, "backend")):
            future = pool.submit(request_handoff, config, "backend", worker_session="browser-A")
            time.sleep(0.05)
            assert future.done() is False
        path = future.result(timeout=2)
    assert path.is_file()


def test_accept_handoff_cannot_erase_newer_request(tmp_path: Path, monkeypatch) -> None:
    import threading
    import time

    from scripts.dev_orchestrator import handoff
    from scripts.dev_orchestrator.handoff import accept_handoff, request_handoff, worker_ack_handoff
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    root = tmp_path / "coord"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    root.mkdir()
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(root, backend, mobile, frozen)
    request = request_handoff(config, "backend", worker_session="browser-a")
    nonce = str(AtomicJsonStore(request).read()["nonce"])
    worker_ack_handoff(config, "backend", nonce=nonce, source="test", worker_session="browser-a")

    entered = threading.Event()
    release = threading.Event()
    original = handoff.validate_worker_checkout

    def slow_validate(cfg, role):
        if threading.current_thread().name == "accept-thread":
            entered.set()
            release.wait(timeout=5)
        return original(cfg, role)

    monkeypatch.setattr(handoff, "validate_worker_checkout", slow_validate)
    t1 = threading.Thread(
        name="accept-thread",
        target=lambda: accept_handoff(config, "backend", nonce=nonce, source="test"),
    )
    t1.start()
    assert entered.wait(timeout=5)
    t2 = threading.Thread(
        target=lambda: request_handoff(config, "backend", worker_session="browser-b")
    )
    t2.start()
    time.sleep(0.1)
    assert t2.is_alive(), "new request must wait for accept-handoff control-plane lock"
    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not (root / "handoff" / "backend.json").exists()
    newest = AtomicJsonStore(root / "handoff" / "backend.request.json").read()
    assert newest["worker_session"] == "browser-b"


def test_advance_handoff_persists_pending_result_with_lease(tmp_path: Path) -> None:
    config = _config(tmp_path)
    request = request_handoff(config, "backend", worker_session="backend-browser-session")
    nonce = json.loads(request.read_text(encoding="utf-8"))["nonce"]
    worker_ack_handoff(
        config,
        "backend",
        nonce=nonce,
        source="trusted-operator",
        worker_session="backend-browser-session",
    )
    lease = accept_handoff(config, "backend", nonce=nonce, source="trusted-operator")
    repo = config.backend_root
    assert repo is not None
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    pending = {
        "phase": "review",
        "role": "backend",
        "task_id": "B-1",
        "invocation_id": "inv-pending-1",
        "pre_head": head,
        "post_head": head,
        "result": {"outcome": "pass"},
    }
    advance_handoff(
        config,
        "backend",
        pre_head=head,
        post_head=head,
        invocation_id="inv-pending-1",
        provider="codex",
        pending_result=pending,
    )

    assert read_pending_result(config, "backend") == pending
    record = json.loads(lease.read_text(encoding="utf-8"))
    assert record["head"] == head
    assert record["last_invocation_id"] == "inv-pending-1"
    assert record["pending_result"] == pending

    clear_pending_result(config, "backend", invocation_id="inv-pending-1")
    assert read_pending_result(config, "backend") is None


def _accept_test_lease(config: OrchestratorConfig, role: str) -> None:
    request = request_handoff(config, role, worker_session=f"{role}-openai-test")
    nonce = json.loads(request.read_text(encoding="utf-8"))["nonce"]
    worker_ack_handoff(
        config,
        role,
        nonce=nonce,
        source="test",
        worker_session=f"{role}-openai-test",
    )
    accept_handoff(config, role, nonce=nonce, source="test")


def test_openai_handoff_accepts_read_only_pending_result(tmp_path: Path) -> None:
    config = _config(tmp_path)
    repo = config.backend_root
    assert repo is not None
    _accept_test_lease(config, "backend")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    pending = {
        "invocation_id": "openai-1",
        "role": "backend",
        "phase": "review",
        "task_id": "B-1",
        "result": {"outcome": "pass"},
    }
    advance_handoff(
        config,
        "backend",
        pre_head=head,
        post_head=head,
        invocation_id="openai-1",
        provider="openai",
        pending_result=pending,
    )
    assert read_pending_result(config, "backend") == pending


def test_openai_handoff_rejects_repository_delta(tmp_path: Path) -> None:
    config = _config(tmp_path)
    repo = config.backend_root
    assert repo is not None
    _accept_test_lease(config, "backend")
    pre = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "smuggled.txt").write_text("delta\n", encoding="utf-8")
    subprocess.run(["git", "add", "smuggled.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "smuggled delta"], cwd=repo, check=True)
    post = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    with pytest.raises(HandoffRequired, match="read-only OpenAI"):
        advance_handoff(
            config,
            "backend",
            pre_head=pre,
            post_head=post,
            invocation_id="openai-2",
            provider="openai",
        )
