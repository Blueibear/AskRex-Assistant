import subprocess
from pathlib import Path

import pytest

from scripts.dev_orchestrator import validation
from scripts.dev_orchestrator.types import OrchestratorConfig


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        pytest.param(b"captured progress\n", None, "captured progress\n", id="stdout-bytes"),
        pytest.param(None, b"captured error\n", "captured error\n", id="stderr-bytes"),
        pytest.param(b"progress\n", b"error\n", "progress\nerror\n", id="both-bytes"),
        pytest.param("progress\n", "error\n", "progress\nerror\n", id="text-streams"),
        pytest.param(None, None, "", id="silent-timeout"),
        pytest.param(b"progress \xff", None, "progress \ufffd", id="undecodable-bytes"),
    ],
)
def test_validation_timeout_returns_failure_without_retaining_success_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout, stderr, expected: str
) -> None:
    root = tmp_path / "coordination"
    repo = tmp_path / "backend"
    repo.mkdir()
    config = OrchestratorConfig(
        coordination_root=root,
        backend_root=repo,
        mobile_root=tmp_path / "mobile",
        metadata={
            "iteration_validation": {
                "backend": {
                    "gates": [
                        {
                            "name": "bounded fixture",
                            "command": ["bounded-fixture"],
                            "timeout_seconds": 1,
                        }
                    ]
                }
            }
        },
    )
    receipt = root / "validation" / "backend-timeout-fixture.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"previous_success":true}', encoding="utf-8")

    def timed_out(command, **kwargs):
        assert tuple(command) == ("bounded-fixture",)
        assert kwargs["text"] is True
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=stdout, stderr=stderr)

    monkeypatch.setattr(validation.subprocess, "run", timed_out)

    report = validation.run_iteration_validation(config, "backend", "timeout-fixture")

    assert report.passed is False
    assert report.system_error == ""
    assert report.feedback == "Deterministic validation timed out before review.\n" + expected
    assert report.receipt is None
    assert not receipt.exists()
