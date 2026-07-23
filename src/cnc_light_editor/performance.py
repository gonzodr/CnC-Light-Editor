from __future__ import annotations

import os
from pathlib import Path


PI_MODEL_PATH = Path("/proc/device-tree/model")


def raspberry_pi_model(path: str | Path = PI_MODEL_PATH) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").rstrip("\x00\n ")
    except (OSError, UnicodeError):
        return ""


def recommended_preview_fps(
    *, override: str | int | None = None, model: str | None = None,
) -> int:
    value = override if override is not None else os.environ.get("CNC_LIGHT_EDITOR_FPS")
    if value not in (None, ""):
        try:
            return max(15, min(120, int(value)))
        except (TypeError, ValueError):
            pass
    detected = raspberry_pi_model() if model is None else model
    if "Raspberry Pi 3" in detected:
        # Leave enough idle time for SDL/KMSDRM on the single fast core used by
        # the editor.  This affects UI preview only, never the exported effect FPS.
        return 20
    if detected.startswith("Raspberry Pi"):
        return 40
    return 60

