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
    assert {"torch==2.7.1", "torchvision==0.22.1", "torchaudio==2.7.1"} <= electron


def test_cuda_124_profile_uses_one_matching_torch_release_set() -> None:
    gpu = _requirements("requirements-gpu-cu124.txt")

    assert {
        "torch==2.6.0+cu124",
        "torchvision==0.21.0+cu124",
        "torchaudio==2.6.0+cu124",
    } <= gpu
    assert '-e ".[audio]"' in gpu
    assert '-e ".[ml,audio]"' not in gpu
    assert "coqui-tts==0.28.0" in gpu
    assert "six>=1.17,<2" in gpu


def test_project_and_profile_transformers_policy_matches_pipfile_lock() -> None:
    cpu = _requirements("requirements-cpu.txt")
    gpu = _requirements("requirements-gpu-cu124.txt")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pipfile = tomllib.loads((ROOT / "Pipfile").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "Pipfile.lock").read_text(encoding="utf-8"))

    assert "transformers>=5.5.0,<6.0" in cpu
    assert "transformers>=5.5.0,<6.0" in gpu
    assert pipfile["packages"]["transformers"] == "==5.5.0"
    assert lock["default"]["transformers"]["version"] == "==5.5.0"
    for extra in ("ml", "full"):
        assert "transformers>=5.5.0,<6.0" in project["project"]["optional-dependencies"][
            extra
        ]
        assert "coqui-tts==0.28.0" in project["project"]["optional-dependencies"][extra]
        assert "six>=1.17,<2" in project["project"]["optional-dependencies"][extra]
        assert "TTS==0.22.0" not in project["project"]["optional-dependencies"][extra]


def test_supported_release_profiles_match_the_documented_compatibility_matrix() -> None:
    """Keep the release profiles and their compatibility evidence in lockstep.

    These are intentionally exact release sets, not broad ranges: a profile
    upgrade must update this regression and receive its own installation and
    runtime evidence.
    """
    cpu = _requirements("requirements-cpu.txt")
    gpu = _requirements("requirements-gpu-cu124.txt")
    electron = _requirements("requirements-electron-voice.txt")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    builder = (ROOT / "scripts" / "build_managed_python_runtime.ps1").read_text(
        encoding="utf-8"
    )
    guide = (ROOT / "docs" / "DEPENDENCIES.md").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "verify_dependency_profile.py").read_text(
        encoding="utf-8"
    )

    cpu_set = {"torch==2.7.1", "torchvision==0.22.1", "torchaudio==2.7.1"}
    cuda_124_set = {
        "torch==2.6.0+cu124",
        "torchvision==0.21.0+cu124",
        "torchaudio==2.6.0+cu124",
    }
    assert cpu_set <= cpu
    assert cuda_124_set <= gpu
    assert "torch==2.7.1" in electron
    assert (
        "pip install --no-cache-dir torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1"
        in dockerfile
    )
    assert "import torch, torchvision, torchaudio, whisper" in builder
    assert "torchvision.__version__.split('+', 1)[0] == '0.22.1'" in builder
    assert "torchaudio.__version__.split('+', 1)[0] == '2.7.1'" in builder
    for expected in (
        "CPU / Docker / `[ml]` extra | `2.7.1` | `0.22.1` | `2.7.1`",
        "CUDA 12.4 | `2.6.0+cu124` | `0.21.0+cu124` | `2.6.0+cu124`",
        "Transformers policy | `>=5.5.0,<6.0`",
    ):
        assert expected in guide
    for expected in (
        '"cpu": {',
        '"torch": "2.7.1"',
        '"torchvision": "0.22.1"',
        '"torchaudio": "2.7.1"',
        '"cuda124": {',
        '"torch": "2.6.0"',
        '"torchvision": "0.21.0"',
        '"torchaudio": "2.6.0"',
        '"transformers": ">=5.5.0,<6.0"',
        '"coqui-tts": "0.28.0"',
    ):
        assert expected in verifier


def test_ci_does_not_ignore_the_retired_transformers_vulnerabilities() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "CVE-2026-1839" not in ci
    assert "PYSEC-2025-217" not in ci
