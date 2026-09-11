from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class AtomicJsonStore:
    """Small atomic JSON file store for durable supervisor state."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self, default: Any = None) -> Any:
        if not self.path.exists():
            return default
        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def write(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
