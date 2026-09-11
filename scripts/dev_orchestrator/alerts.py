import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .storage import AtomicJsonStore


class AlertSink:
    def __init__(
        self,
        root: Path,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = root
        self.now = now or (lambda: datetime.now(UTC))

    def emit(self, *, role: str, kind: str, message: str) -> Path:
        digest = hashlib.sha256(f"{role}\0{kind}\0{message}".encode()).hexdigest()[:16]
        path = self.root / f"{kind}-{role}-{digest}.json"
        if path.is_file():
            return path
        payload = {
            "role": role,
            "kind": kind,
            "message": message,
            "status": "active",
            "created_at": self.now().isoformat(),
        }
        AtomicJsonStore(path).write(payload)
        return path
