"""Regression checks for the supported ML dependency release policy."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _requirements(name: str) -> set[str]:
    return {
        line.strip()
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def test_cpu_and_electron_voice_use_the_same_published_torch_set() -> None:
    cpu = _requirements("requirements-cpu.txt")
    electron = _requirements("requirements-electron-voice.txt")

    assert {"torch==2.7.1", "torchvision==0.22.1", "torchaudio==2.7.1"} <= cpu
    assert "torch==2.7.1" in electron


def test_cuda_124_profile_uses_one_matching_torch_release_set() -> None:
    gpu = _requirements("requirements-gpu-cu124.txt")

    assert {
        "torch==2.6.0+cu124",
        "torchvision==0.21.0+cu124",
        "torchaudio==2.6.0+cu124",
    } <= gpu
    assert '-e ".[audio]"' in gpu
    assert '-e ".[ml,audio]"' not in gpu


def test_project_and_profile_transformers_policy_matches_pipfile_lock() -> None:
    requirements = _requirements("requirements-cpu.txt")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pipfile = tomllib.loads((ROOT / "Pipfile").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "Pipfile.lock").read_text(encoding="utf-8"))

    assert "transformers>=5.5.0,<6.0" in requirements
    assert pipfile["packages"]["transformers"] == "==5.5.0"
    assert lock["default"]["transformers"]["version"] == "==5.5.0"
    for extra in ("ml", "full"):
        assert "transformers>=5.5.0,<6.0" in project["project"]["optional-dependencies"][extra]


def test_ci_does_not_ignore_the_retired_transformers_vulnerabilities() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "CVE-2026-1839" not in ci
    assert "PYSEC-2025-217" not in ci
