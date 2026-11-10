"""Source-level regression coverage for the executable profile verifier."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_verifier():
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_dependency_profile.py"
    spec = importlib.util.spec_from_file_location("verify_dependency_profile", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_profile_verifier_has_exact_supported_release_sets() -> None:
    verifier = _load_verifier()

    assert verifier.PROFILE_VERSIONS == {
        "cpu": {
            "torch": "2.7.1",
            "torchvision": "0.22.1",
            "torchaudio": "2.7.1",
            "transformers": "5.5.0",
        },
        "cuda124": {
            "torch": "2.6.0",
            "torchvision": "0.21.0",
            "torchaudio": "2.6.0",
            "transformers": "5.5.0",
        },
    }


def test_profile_verifier_accepts_local_version_suffixes() -> None:
    verifier = _load_verifier()

    assert verifier._base_version("2.6.0+cu124") == "2.6.0"
