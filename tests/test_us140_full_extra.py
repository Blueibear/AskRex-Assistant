"""US-140: Consolidate optional extras into a single [full] install target.

Tests verify the pyproject.toml structure and install script references.
"""

import tomllib
from pathlib import Path
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _load_toml() -> dict[str, Any]:
    with open(PYPROJECT, "rb") as f:
        return cast(dict[str, Any], tomllib.load(f))


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    return _load_toml()


@pytest.fixture(scope="module")
def optional_deps(pyproject: dict[str, Any]) -> dict[str, list[str]]:
    project = cast(dict[str, Any], pyproject["project"])
    return cast(dict[str, list[str]], project["optional-dependencies"])


class TestFullExtraExists:
    def test_full_extra_defined(self, optional_deps: dict) -> None:
        assert "full" in optional_deps, "pyproject.toml must define a [full] extra"

    def test_full_extra_is_non_empty(self, optional_deps: dict) -> None:
        assert len(optional_deps["full"]) > 0, "[full] extra must not be empty"


class TestFullExtraCoversVoice:
    """[full] must cover all voice/ML capabilities."""

    def _packages(self, optional_deps: dict) -> list[str]:
        return [
            dep.split(">=")[0].split(">")[0].split("<")[0].split("==")[0].strip().lower()
            for dep in optional_deps["full"]
        ]

    def test_torch_in_full(self, optional_deps: dict) -> None:
        pkgs = self._packages(optional_deps)
        assert "torch" in pkgs

    def test_openai_whisper_in_full(self, optional_deps: dict) -> None:
        pkgs = self._packages(optional_deps)
        assert "openai-whisper" in pkgs

    def test_openwakeword_in_full(self, optional_deps: dict) -> None:
        pkgs = self._packages(optional_deps)
        assert "openwakeword" in pkgs

    def test_coqui_tts_in_full(self, optional_deps: dict) -> None:
        pkgs = self._packages(optional_deps)
        assert "coqui-tts" in pkgs

    def test_transformers_in_full(self, optional_deps: dict) -> None:
        pkgs = self._packages(optional_deps)
        assert "transformers" in pkgs

    def test_openai_in_full(self, optional_deps: dict) -> None:
        pkgs = self._packages(optional_deps)
        assert "openai" in pkgs


class TestFullExtraCoversAudio:
    """[full] must cover all audio I/O capabilities."""

    def _packages(self, optional_deps: dict) -> list[str]:
        return [
            dep.split(">=")[0].split(">")[0].split("<")[0].split("==")[0].strip().lower()
            for dep in optional_deps["full"]
        ]

    def test_numpy_in_full(self, optional_deps: dict) -> None:
        pkgs = self._packages(optional_deps)
        assert "numpy" in pkgs

    def test_sounddevice_in_full(self, optional_deps: dict) -> None:
        pkgs = self._packages(optional_deps)
        assert "sounddevice" in pkgs

    def test_soundfile_in_full(self, optional_deps: dict) -> None:
        pkgs = self._packages(optional_deps)
        assert "soundfile" in pkgs


class TestFullExtraCoversIntegrations:
    """[full] must cover integrations (SMS via twilio)."""

    def _packages(self, optional_deps: dict) -> list[str]:
        return [
            dep.split(">=")[0].split(">")[0].split("<")[0].split("==")[0].strip().lower()
            for dep in optional_deps["full"]
        ]

    def test_twilio_in_full(self, optional_deps: dict) -> None:
        pkgs = self._packages(optional_deps)
        assert "twilio" in pkgs


class TestExistingExtrasPreserved:
    """All existing fine-grained extras must remain available."""

    def test_audio_extra_preserved(self, optional_deps: dict) -> None:
        assert "audio" in optional_deps

    def test_ml_extra_preserved(self, optional_deps: dict) -> None:
        assert "ml" in optional_deps

    def test_dev_extra_preserved(self, optional_deps: dict) -> None:
        assert "dev" in optional_deps

    def test_sms_extra_preserved(self, optional_deps: dict) -> None:
        assert "sms" in optional_deps

    def test_voice_id_extra_preserved(self, optional_deps: dict) -> None:
        assert "voice-id" in optional_deps

    def test_devtools_extra_preserved(self, optional_deps: dict) -> None:
        assert "devtools" in optional_deps


class TestInstallScriptsUseFullExtra:
    """Install scripts must reference rex[full]."""

    def test_install_sh_uses_full(self) -> None:
        install_sh = REPO_ROOT / "install.sh"
        if not install_sh.exists():
            pytest.skip("install.sh not present")
        content = install_sh.read_text()
        assert "[full]" in content, "install.sh must reference [full] extra"

    def test_install_ps1_uses_full(self) -> None:
        install_ps1 = REPO_ROOT / "install.ps1"
        if not install_ps1.exists():
            pytest.skip("install.ps1 not present")
        content = install_ps1.read_text()
        assert "[full]" in content, "install.ps1 must reference [full] extra"
