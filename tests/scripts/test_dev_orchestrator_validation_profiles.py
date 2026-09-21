from pathlib import Path

from scripts.dev_orchestrator.types import OrchestratorConfig
from scripts.dev_orchestrator.validation import (
    _coordination_only_gates,
    _has_task_validation_override,
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
