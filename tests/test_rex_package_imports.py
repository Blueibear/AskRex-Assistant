from __future__ import annotations

import subprocess
import sys


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )


def test_import_rex_does_not_initialize_runtime_config() -> None:
    result = _run("import rex, sys; print('rex.config' in sys.modules)")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_legacy_package_root_export_remains_available() -> None:
    result = _run("from rex import settings; print(type(settings).__name__)")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_package_submodule_import_remains_available() -> None:
    result = _run("from rex import calendar_service; print(calendar_service.__name__)")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "rex.calendar_service"
