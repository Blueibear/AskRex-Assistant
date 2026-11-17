from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from scripts.dev_orchestrator.coordination_checks import (
    CoordinationCheckError,
    begin_checks,
    finish_checks,
    read_check_summary,
)
from scripts.dev_orchestrator.types import OrchestratorConfig, TaskItem

BASE = "1" * 40
INITIAL = "2" * 40
HEAD = "3" * 40


def _config(tmp_path: Path) -> OrchestratorConfig:
    root = tmp_path / "coordination"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "check-coordination.ps1").write_text(
        "param([string]$Role)\nWrite-Output $Role\n", encoding="utf-8"
    )
    for name in ("backend", "mobile", "frozen"):
        (tmp_path / name).mkdir()
    return OrchestratorConfig(
        coordination_root=root,
        backend_root=tmp_path / "backend",
        mobile_root=tmp_path / "mobile",
        frozen_worktree=tmp_path / "frozen",
        observe_only=False,
    )


def test_check_receipt_is_revision_bound_and_has_verified_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    task = TaskItem("B-CHECK-1", "run checks")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, returncode=0, stdout=b"CHECK-OK\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    begin_checks(config, "backend", task, BASE, INITIAL)
    before = read_check_summary(config, "backend", task, BASE, HEAD)
    assert "initial: exit=0" in before
    assert "final: missing, not verified" in before
    finish_checks(config, "backend", task, BASE, HEAD)
    receipt = read_check_summary(config, "backend", task, BASE, HEAD)
    assert "initial: exit=0" in receipt
    assert "final: exit=0" in receipt
    assert INITIAL in receipt
    assert HEAD in receipt
    assert "do not establish the absence" in receipt
    assert len(calls) == 2
    assert all(call[0][-2:] == ["-Role", "backend"] for call in calls)
    assert all(call[1]["timeout"] == 120 for call in calls)

    files = list((config.coordination_root / "evidence" / "supervisor-checks").glob("*.stdout.txt"))
    assert len(files) == 2
    assert (
        hashlib.sha256(files[0].read_bytes()).hexdigest()
        == hashlib.sha256(b"CHECK-OK\n").hexdigest()
    )


def test_new_attempt_invalidates_previous_final(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    task = TaskItem("B-CHECK-2", "run checks")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, b"ok", b""),
    )
    begin_checks(config, "backend", task, BASE, INITIAL)
    finish_checks(config, "backend", task, BASE, HEAD)
    begin_checks(config, "backend", task, BASE, HEAD)
    assert "final: missing, not verified" in read_check_summary(config, "backend", task, BASE, HEAD)


def test_tampered_evidence_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    task = TaskItem("B-CHECK-3", "run checks")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, b"ok", b""),
    )
    begin_checks(config, "backend", task, BASE, INITIAL)
    finish_checks(config, "backend", task, BASE, HEAD)
    first = next((config.coordination_root / "evidence" / "supervisor-checks").glob("*.stdout.txt"))
    first.write_bytes(b"tampered")
    with pytest.raises(CoordinationCheckError, match="hash mismatch"):
        read_check_summary(config, "backend", task, BASE, HEAD)


def test_failed_check_never_produces_success_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    task = TaskItem("B-CHECK-4", "run checks")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 2, b"bad", b"diagnostic"),
    )
    with pytest.raises(CoordinationCheckError, match="exit code 2"):
        begin_checks(config, "backend", task, BASE, INITIAL)
    assert "No revision-bound" in read_check_summary(config, "backend", task, BASE, HEAD)


def test_strict_mode_blocks_missing_or_incomplete_receipts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    task = TaskItem("B-STRICT", "review")
    with pytest.raises(CoordinationCheckError, match="missing"):
        read_check_summary(config, "backend", task, BASE, HEAD, strict=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, b"ok", b""),
    )
    begin_checks(config, "backend", task, BASE, INITIAL)
    with pytest.raises(CoordinationCheckError, match="final"):
        read_check_summary(config, "backend", task, BASE, HEAD, strict=True)
    finish_checks(config, "backend", task, BASE, HEAD)
    assert "final: exit=0" in read_check_summary(config, "backend", task, BASE, HEAD, strict=True)


def test_no_script_keeps_fake_supervisor_tests_portable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (config.coordination_root / "scripts" / "check-coordination.ps1").unlink()
    task = TaskItem("B-NOSCRIPT", "run checks")
    begin_checks(config, "backend", task, BASE, INITIAL)
    finish_checks(config, "backend", task, BASE, HEAD)
    assert "No revision-bound" in read_check_summary(config, "backend", task, BASE, HEAD)


def test_rejects_path_like_task_id(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(CoordinationCheckError, match="identity"):
        read_check_summary(config, "backend", TaskItem("../bad", "bad"), BASE, HEAD)
