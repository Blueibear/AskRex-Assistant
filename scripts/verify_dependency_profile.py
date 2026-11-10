"""Verify an installed AskRex ML dependency profile without downloading models.

Run this only after resolving one of the pinned dependency profiles into an
isolated Python 3.11 environment.  It deliberately checks the distributions
that are *actually installed*, then exercises imports and small in-memory
operations across the torch companion packages.  It does not claim CUDA
hardware acceptance or model-inference acceptance.
"""

from __future__ import annotations

import argparse
from importlib import metadata


PROFILE_VERSIONS = {
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


def _base_version(version: str) -> str:
    return version.split("+", 1)[0]


def verify(profile: str) -> dict[str, str]:
    """Fail closed unless the resolved profile imports and operates together."""
    expected = PROFILE_VERSIONS[profile]
    actual = {package: metadata.version(package) for package in expected}
    mismatches = {
        package: (actual[package], version)
        for package, version in expected.items()
        if _base_version(actual[package]) != version
    }
    if mismatches:
        raise RuntimeError(
            f"resolved dependency versions do not match {profile}: {mismatches}"
        )

    import torch
    import torchaudio
    import torchvision

    from rex.compat import ensure_transformers_compatibility

    ensure_transformers_compatibility()
    from transformers import BeamSearchScorer

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
    if not isinstance(BeamSearchScorer, type):
        raise RuntimeError("transformers compatibility shim did not expose BeamSearchScorer")

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
