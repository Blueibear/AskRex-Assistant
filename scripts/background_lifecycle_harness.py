"""Packaged-runtime harness for US-124 background lifecycle mechanics.

This script is copied into a temporary runtime root by the installed-artifact
PowerShell smoke and launched with the installed managed ``pythonw.exe -I``.
It imports and runs the real packaged ``RuntimeSupervisor`` while substituting
self-contained deterministic Core/Voice child fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _managed_site_packages() -> Path:
    return Path(sys.executable).resolve().parent / "Lib" / "site-packages"


def _assert_managed_import(module_file: str | None) -> None:
    if not module_file:
        raise RuntimeError("rex.background has no module file")
    module_path = Path(module_file).resolve()
    expected = _managed_site_packages().resolve()
    if expected != module_path and expected not in module_path.parents:
        raise RuntimeError(
            f"managed rex.background import escaped installed resources: {module_path}"
        )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: background_lifecycle_harness.py <runtime_root> <fake_child_script>",
            file=sys.stderr,
        )
        return 2

    import rex.background
    from rex.background.lock import AlreadyRunningError
    from rex.background.paths import BackgroundPaths
    from rex.background.supervisor import ComponentSpec, RuntimeSupervisor

    _assert_managed_import(rex.background.__file__)
    runtime_root = Path(argv[0]).expanduser().resolve()
    fake_child = Path(argv[1]).expanduser().resolve()
    if not fake_child.is_file():
        print("fake child script is missing", file=sys.stderr)
        return 2

    python = str(Path(sys.executable).resolve())
    root = str(runtime_root)
    child = str(fake_child)
    core = ComponentSpec(
        name="core",
        argv=(python, "-I", child, "core", root),
        required=True,
    )
    voice = ComponentSpec(
        name="voice_agent",
        argv=(python, "-I", child, "voice_agent", root),
        required=True,
    )
    runtime = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(runtime_root),
        core,
        voice,
    )
    try:
        runtime.run()
    except AlreadyRunningError:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
