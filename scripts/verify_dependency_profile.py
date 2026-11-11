"""Verify an installed AskRex ML or managed Voice profile without downloading models.

Run this only after resolving one of the pinned dependency profiles into an
isolated Python 3.11 environment.  It deliberately checks the distributions
that are *actually installed*, then exercises imports and small in-memory
operations across the torch companion packages.  The managed Electron Voice
profile also checks its packaged voice imports. It does not claim CUDA hardware
acceptance, audio-device acceptance, or model-inference acceptance.
"""

from __future__ import annotations

import argparse
from importlib import metadata

from packaging.specifiers import SpecifierSet
from packaging.version import Version

PROFILE_VERSIONS = {
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


def _base_version(version: str) -> str:
    return version.split("+", 1)[0]


def _matches_version(package: str, actual: str, expected: str) -> bool:
    if package == "transformers":
        return Version(actual) in SpecifierSet(expected)
    return _base_version(actual) == expected


def verify(profile: str) -> dict[str, str]:
    """Fail closed unless the resolved profile imports and operates together."""
    expected = PROFILE_VERSIONS[profile]
    actual = {package: metadata.version(package) for package in expected}
    mismatches = {
        package: (actual[package], version)
        for package, version in expected.items()
        if not _matches_version(package, actual[package], version)
    }
    if mismatches:
        raise RuntimeError(
            f"resolved dependency versions do not match {profile}: {mismatches}"
        )

    import torch
    import torchaudio
    import torchvision

    if profile != "electron_voice":
        from TTS.api import TTS
        from TTS.tts.configs.xtts_config import XttsAudioConfig, XttsConfig
        from TTS.tts.layers.xtts.stream_generator import NewGenerationMixin

        if not all(
            isinstance(item, type)
            for item in (TTS, XttsConfig, XttsAudioConfig, NewGenerationMixin)
        ):
            raise RuntimeError("Coqui XTTS import smoke failed")

    if _base_version(torch.__version__) != expected["torch"]:
        raise RuntimeError(
            f"torch runtime version differs from distribution: {torch.__version__}"
        )
    if _base_version(torchvision.__version__) != expected["torchvision"]:
        raise RuntimeError(
            f"torchvision runtime version differs from distribution: {torchvision.__version__}"
        )
    if _base_version(torchaudio.__version__) != expected["torchaudio"]:
        raise RuntimeError(
            f"torchaudio runtime version differs from distribution: {torchaudio.__version__}"
        )
    tensor = torch.tensor([1.0, 2.0])
    if tensor.sum().item() != 3.0:
        raise RuntimeError("torch tensor smoke failed")
    image = torch.zeros((3, 2, 2))
    resized = torchvision.transforms.functional.resize(image, [1, 1])
    if tuple(resized.shape) != (3, 1, 1):
        raise RuntimeError("torchvision transform smoke failed")
    waveform = torch.zeros((1, 16))
    resampled = torchaudio.functional.resample(waveform, 16_000, 8_000)
    if tuple(resampled.shape) != (1, 8):
        raise RuntimeError("torchaudio resample smoke failed")
    if profile == "electron_voice":
        import imageio_ffmpeg
        import onnxruntime
        import openwakeword
        import scipy
        import sklearn
        import sounddevice
        import soundfile
        import whisper

        voice_modules = (
            imageio_ffmpeg,
            onnxruntime,
            openwakeword,
            scipy,
            sklearn,
            sounddevice,
            soundfile,
            whisper,
        )
        if any(module is None for module in voice_modules):
            raise RuntimeError("managed Electron Voice import smoke failed")
    return actual


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILE_VERSIONS), required=True)
    args = parser.parse_args()
    versions = verify(args.profile)
    print(f"dependency-profile-ok profile={args.profile} versions={versions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
