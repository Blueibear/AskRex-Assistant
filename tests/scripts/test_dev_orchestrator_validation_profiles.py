from pathlib import Path

from scripts.dev_orchestrator.types import OrchestratorConfig
from scripts.dev_orchestrator.validation import (
    _coordination_only_gates,
    _has_task_validation_override,
    _scoped_backend_gates_from_diff,
)


def _config(tmp_path: Path) -> OrchestratorConfig:
    root = tmp_path / "coordination"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "check-coordination.ps1").write_text(
        "Write-Output 'ok'\n", encoding="utf-8"
    )
    return OrchestratorConfig(
        coordination_root=root,
        backend_root=tmp_path / "backend",
        mobile_root=tmp_path / "mobile",
        metadata={
            "iteration_validation": {
                "backend": {
                    "overrides": [
                        {
                            "task_prefix": "special-closeout",
                            "gates": [
                                {
                                    "name": "special",
                                    "command": ["py", "-3.11", "-c", "pass"],
                                }
                            ],
                        }
                    ]
                }
            }
        },
    )


def test_coordination_only_profile_never_runs_default_pytest(tmp_path: Path) -> None:
    config = _config(tmp_path)

    gates = _coordination_only_gates(
        config,
        "backend",
        base_head="abc123",
        head="abc123",
    )

    commands = [tuple(part.lower() for part in gate.command) for gate in gates]
    assert [gate.name for gate in gates] == [
        "coordination recheck",
        "accepted checkpoint unchanged",
        "coordination diff check",
    ]
    assert not any(
        len(command) >= 4 and command[2:4] == ("-m", "pytest")
        for command in commands
    )
    assert any(
        any("check-coordination.ps1" in part for part in command)
        for command in commands
    )


def test_explicit_task_validation_override_wins_for_coordination_task(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert _has_task_validation_override(config, "backend", "special-closeout-v2") is True
    assert _has_task_validation_override(config, "backend", "ordinary-closeout-v2") is False


def _git(repo: Path, *args: str) -> str:
    import subprocess
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_git_repo(repo: Path) -> str:
    import subprocess
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=repo, check=True)
    (repo / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_module.py").write_text(
        "from module import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def test_scoped_backend_profile_uses_changed_python_regressions(tmp_path: Path) -> None:
    import subprocess
    config = _config(tmp_path)
    config.metadata["iteration_validation"]["backend"]["scoped_default_from_diff"] = True
    repo = config.backend_root
    assert repo is not None
    base = _init_git_repo(repo)
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "tests" / "test_module.py").write_text(
        "from module import VALUE\n\ndef test_value():\n    assert VALUE == 2\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "change"], cwd=repo, check=True)
    head = _git(repo, "rev-parse", "HEAD")

    gates = _scoped_backend_gates_from_diff(
        config,
        "backend",
        base_head=base,
        head=head,
    )

    assert [gate.name for gate in gates] == [
        "scoped changed regression tests",
        "scoped changed-file ruff",
        "scoped changed-source syntax",
        "scoped task diff check",
    ]
    assert "tests/test_module.py" in gates[0].command
    assert "module.py" in gates[1].command


def test_scoped_backend_profile_falls_back_for_non_python_change(tmp_path: Path) -> None:
    import subprocess
    config = _config(tmp_path)
    config.metadata["iteration_validation"]["backend"]["scoped_default_from_diff"] = True
    repo = config.backend_root
    assert repo is not None
    base = _init_git_repo(repo)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "packaging"], cwd=repo, check=True)
    head = _git(repo, "rev-parse", "HEAD")

    assert _scoped_backend_gates_from_diff(
        config,
        "backend",
        base_head=base,
        head=head,
    ) == ()
