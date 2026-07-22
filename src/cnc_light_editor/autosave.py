from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


AUTOSAVE_FORMAT = "cnc-light-autosave"
AUTOSAVE_VERSION = 1


@dataclass(frozen=True)
class RecoveryRecord:
    project_data: dict[str, Any]
    saved_at: str
    source_path: Path | None
    autosave_path: Path


class AutosaveManager:
    def __init__(self, directory: str | Path, slots: int = 3):
        self.directory = Path(directory)
        self.slots = max(1, int(slots))

    def write(self, project_data: dict[str, Any], source_path: str | Path | None) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        for index in range(self.slots - 1, 0, -1):
            previous = self._slot(index - 1)
            destination = self._slot(index)
            if previous.exists():
                previous.replace(destination)
        payload = {
            "format": AUTOSAVE_FORMAT,
            "version": AUTOSAVE_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "source_path": str(Path(source_path).resolve()) if source_path else None,
            "project": project_data,
        }
        destination = self._slot(0)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(destination)
        return destination

    def latest(self) -> RecoveryRecord | None:
        for path in (self._slot(index) for index in range(self.slots)):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if (
                    raw.get("format") != AUTOSAVE_FORMAT
                    or raw.get("version") != AUTOSAVE_VERSION
                    or not isinstance(raw.get("project"), dict)
                ):
                    continue
                source = raw.get("source_path")
                return RecoveryRecord(
                    project_data=raw["project"],
                    saved_at=str(raw.get("saved_at", "Unknown time")),
                    source_path=Path(source) if source else None,
                    autosave_path=path,
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return None

    def clear(self) -> None:
        for index in range(self.slots):
            path = self._slot(index)
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _slot(self, index: int) -> Path:
        return self.directory / f"recovery-{index}.cnclight.autosave.json"
