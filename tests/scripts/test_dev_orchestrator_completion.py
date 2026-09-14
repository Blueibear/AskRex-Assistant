from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.dev_orchestrator import completion
from scripts.dev_orchestrator.completion import evaluate_completion, record_acceptance
from scripts.dev_orchestrator.handoff import acknowledge_handoff
from scripts.dev_orchestrator.types import OrchestratorConfig
from tests.scripts.test_dev_orchestrator_safety import _git_repo


def _config(tmp_path: Path, role: str = "backend") -> OrchestratorConfig:
    coord = tmp_path / "coord"
    coord.mkdir()
    (coord / "issues").mkdir()
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    _git_repo(backend)
    _git_repo(mobile)
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    config = OrchestratorConfig(coord, backend, mobile, frozen, observe_only=False)
    (coord / "handoff").mkdir(exist_ok=True)
    acknowledge_handoff(config, "backend", source="test")
    acknowledge_handoff(config, "mobile", source="test")
    return config


def _success_checks(role: str) -> list[dict[str, str]]:
    manifest = completion.COMPLETION_MANIFESTS[role]
    return [{"name": name, "conclusion": "SUCCESS"} for name in manifest.required_checks]


def _mock_pr(monkeypatch, config: OrchestratorConfig, role: str, **overrides) -> None:
    repo = config.backend_root if role == "backend" else config.mobile_root
    assert repo is not None
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=repo, text=True
    ).strip()
    payload = {
        "state": "OPEN",
        "headRefOid": head,
        "headRefName": branch,
        "baseRefName": completion.COMPLETION_MANIFESTS[role].base_branch,
        "statusCheckRollup": _success_checks(role),
    }
    payload.update(overrides)
    real_run = completion._run

    def fake_run(target: Path, *args: str):
        if args[:3] == ("gh", "pr", "view"):
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        return real_run(target, *args)

    monkeypatch.setattr(completion, "_run", fake_run)
    monkeypatch.setattr(completion, "_ci_workflow_present", lambda *_args: True)
    monkeypatch.setattr(completion, "_run_local_gates", lambda *_args: ())


def _accept(config: OrchestratorConfig, role: str) -> None:
    record_acceptance(
        config.coordination_root, role, "documentation", "docs match current behavior"
    )
    record_acceptance(config.coordination_root, role, "physical", "hardware path passed")
    record_acceptance(config.coordination_root, role, "cross_repo", "peer contract acknowledged")


def test_completion_requires_recorded_physical_and_cross_repo_acceptance(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    _mock_pr(monkeypatch, config, "backend")
    gate = evaluate_completion(config, "backend")
    assert gate.ready is False
    assert gate.needs_user is True
    assert any("acceptance" in reason.lower() for reason in gate.reasons)


def test_completion_requires_open_pr_expected_base_and_current_head(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    _accept(config, "backend")
    _mock_pr(monkeypatch, config, "backend", state="CLOSED", baseRefName="wrong")
    gate = evaluate_completion(config, "backend")
    assert gate.ready is False
    text = " ".join(gate.reasons).lower()
    assert "open" in text
    assert "base" in text


def test_required_ci_check_must_be_present_and_successful(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    _accept(config, "backend")
    checks = _success_checks("backend")
    checks[0]["conclusion"] = "SKIPPED"
    _mock_pr(monkeypatch, config, "backend", statusCheckRollup=checks)
    gate = evaluate_completion(config, "backend")
    assert gate.ready is False
    assert "required ci" in " ".join(gate.reasons).lower()


def test_local_gate_failure_blocks_completion(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    _accept(config, "backend")
    _mock_pr(monkeypatch, config, "backend")
    monkeypatch.setattr(completion, "_run_local_gates", lambda *_args: ("backend pytest failed",))
    gate = evaluate_completion(config, "backend")
    assert gate.ready is False
    assert "pytest failed" in " ".join(gate.reasons).lower()


def test_complete_manifest_can_pass_only_when_all_evidence_is_green(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    _accept(config, "backend")
    _mock_pr(monkeypatch, config, "backend")
    gate = evaluate_completion(config, "backend")
    assert gate.ready is True
    assert gate.needs_user is False
    assert gate.reasons == ()


def test_acceptance_is_invalidated_when_local_or_peer_revision_changes(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    _accept(config, "backend")
    backend = config.backend_root
    mobile = config.mobile_root
    assert backend is not None and mobile is not None

    (backend / "after.txt").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "after.txt"], cwd=backend, check=True)
    subprocess.run(["git", "commit", "-qm", "after acceptance"], cwd=backend, check=True)
    _mock_pr(monkeypatch, config, "backend")
    gate = evaluate_completion(config, "backend")
    assert gate.ready is False
    assert "lease is invalid" in " ".join(gate.reasons).lower()


def test_completion_fails_closed_when_git_metadata_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    repo = config.backend_root
    assert repo is not None
    real_run = completion._run

    def fake_run(target: Path, *args: str):
        if args == ("git", "rev-parse", "--git-dir"):
            return subprocess.CompletedProcess(args, 128, "", "not a repo")
        return real_run(target, *args)

    monkeypatch.setattr(completion, "_run", fake_run)
    gate = evaluate_completion(config, "backend")
    assert gate.ready is False
    assert "git metadata" in " ".join(gate.reasons).lower()


def test_cross_repo_acceptance_is_invalidated_when_peer_head_changes(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    _accept(config, "backend")
    mobile = config.mobile_root
    assert mobile is not None
    (mobile / "peer-after.txt").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "peer-after.txt"], cwd=mobile, check=True)
    subprocess.run(["git", "commit", "-qm", "peer changed"], cwd=mobile, check=True)
    _mock_pr(monkeypatch, config, "backend")
    gate = evaluate_completion(config, "backend")
    assert gate.ready is False
    assert "cross-repo" in " ".join(gate.reasons).lower()


def test_completion_revalidates_lease_and_exact_pr_head_branch(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    _accept(config, "backend")
    repo = config.backend_root
    assert repo is not None
    current = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=repo, text=True
    ).strip()
    _mock_pr(monkeypatch, config, "backend", headRefName="wrong-branch")
    gate = evaluate_completion(config, "backend")
    assert gate.ready is False
    assert "head branch" in " ".join(gate.reasons).lower()

    subprocess.run(["git", "branch", "-m", current + "-renamed"], cwd=repo, check=True)
    gate = evaluate_completion(config, "backend")
    assert gate.ready is False
    assert "lease is invalid" in " ".join(gate.reasons).lower()


def test_duplicate_required_ci_name_requires_every_matching_result_successful(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    _accept(config, "backend")
    checks = _success_checks("backend")
    required = completion.COMPLETION_MANIFESTS["backend"].required_checks[0]
    checks.insert(0, {"name": required, "conclusion": "SKIPPED"})
    _mock_pr(monkeypatch, config, "backend", statusCheckRollup=checks)
    gate = evaluate_completion(config, "backend")
    assert gate.ready is False
    assert required in " ".join(gate.reasons)
