"""Regression coverage for the executable profile verifier."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


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
            "transformers": ">=5.5.0,<6.0",
            "coqui-tts": "0.28.0",
        },
        "cuda124": {
            "torch": "2.6.0",
            "torchvision": "0.21.0",
            "torchaudio": "2.6.0",
            "transformers": ">=5.5.0,<6.0",
            "coqui-tts": "0.28.0",
        },
        "electron_voice": {
            "torch": "2.7.1",
            "torchvision": "0.22.1",
            "torchaudio": "2.7.1",
            "openai-whisper": "20250625",
            "openwakeword": "0.6.0",
            "scipy": "1.15.3",
            "onnxruntime": "1.22.0",
            "scikit-learn": "1.6.1",
        },
    }


def test_profile_verifier_accepts_local_version_suffixes() -> None:
    verifier = _load_verifier()

    assert verifier._base_version("2.6.0+cu124") == "2.6.0"


def test_profile_verifier_enforces_transformers_security_range() -> None:
    verifier = _load_verifier()

    assert verifier._matches_version("transformers", "5.22.2", ">=5.5.0,<6.0")
    assert not verifier._matches_version("transformers", "5.4.9", ">=5.5.0,<6.0")
    assert not verifier._matches_version("transformers", "6.0.0", ">=5.5.0,<6.0")


def test_electron_voice_profile_runs_its_runtime_only_import_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _load_verifier()

    class FakeTensor:
        def __init__(self, shape: tuple[int, ...] = ()) -> None:
            self.shape = shape

        def sum(self) -> FakeTensor:
            return self

        def item(self) -> float:
            return 3.0

    torch = ModuleType("torch")
    torch.__version__ = "2.7.1"

    def tensor(_: object) -> FakeTensor:
        return FakeTensor()

    def zeros(shape: tuple[int, ...]) -> FakeTensor:
        return FakeTensor(tuple(shape))

    torch.tensor = tensor
    torch.zeros = zeros

    torchvision = ModuleType("torchvision")
    torchvision.__version__ = "0.22.1"
    torchvision.transforms = ModuleType("torchvision.transforms")
    torchvision.transforms.functional = ModuleType("torchvision.transforms.functional")

    def resize(_: FakeTensor, shape: list[int]) -> FakeTensor:
        return FakeTensor((3, *shape))

    torchvision.transforms.functional.resize = resize

    torchaudio = ModuleType("torchaudio")
    torchaudio.__version__ = "2.7.1"
    torchaudio.functional = ModuleType("torchaudio.functional")

    def resample(_: FakeTensor, __: int, ___: int) -> FakeTensor:
        return FakeTensor((1, 8))

    torchaudio.functional.resample = resample

    modules = {
        "torch": torch,
        "torchvision": torchvision,
        "torchaudio": torchaudio,
    }
    for name in (
        "imageio_ffmpeg",
        "openwakeword",
        "sounddevice",
        "soundfile",
        "whisper",
        "onnxruntime",
        "scipy",
        "sklearn",
    ):
        modules[name] = ModuleType(name)
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def version(package: str) -> str:
        return verifier.PROFILE_VERSIONS["electron_voice"][package]

    monkeypatch.setattr(verifier.metadata, "version", version)

    assert verifier.verify("electron_voice") == verifier.PROFILE_VERSIONS["electron_voice"]
