from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .model import Color


EFFECT_LEDS = 68
DEFAULT_FRAME_MS = 60
BLACK: Color = (0, 0, 0)
FASTLED_COLORS: dict[str, Color] = {
    "White": (255, 255, 255),
    "Yellow": (255, 255, 0),
    "Red": (255, 0, 0),
    "Orange": (255, 165, 0),
    "Green": (0, 128, 0),
    "IndianRed": (205, 92, 92),
    "SkyBlue": (135, 206, 235),
    "DeepPink": (255, 20, 147),
    "DarkViolet": (148, 0, 211),
    "Tomato": (255, 99, 71),
    "LightSeaGreen": (32, 178, 170),
    "FairyLight": (255, 228, 105),
}
BLAST_PALETTE = tuple(FASTLED_COLORS[name] for name in (
    "White", "Yellow", "Red", "Orange", "Green", "IndianRed",
    "SkyBlue", "DeepPink", "DarkViolet", "Tomato", "LightSeaGreen", "FairyLight",
))


@dataclass(frozen=True)
class ImportedEffect:
    name: str
    frames: list[list[Color]]
    frame_ms: int = DEFAULT_FRAME_MS

    @property
    def duration_ms(self) -> int:
        return max(self.frame_ms, len(self.frames) * self.frame_ms)

    def colors_at(self, time_ms: int) -> list[Color]:
        if not self.frames:
            return [BLACK] * EFFECT_LEDS
        index = max(0, min(len(self.frames) - 1, time_ms // self.frame_ms))
        return self.frames[index]


def load_effect_data(path: str | Path) -> list[ImportedEffect]:
    return parse_effect_data(Path(path).read_text(encoding="utf-8"))


def parse_effect_data(source: str) -> list[ImportedEffect]:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"//[^\r\n]*", "", source)
    pattern = re.compile(
        r"const\s+uint8_t\s+(EffectID\d+)\s*\[\s*\]\s+PROGMEM\s*=\s*\{(.*?)\}\s*;",
        re.DOTALL,
    )
    effects: list[ImportedEffect] = []
    for match in pattern.finditer(source):
        name, body = match.groups()
        values = [int(value) for value in re.findall(r"\b\d+\b", body)]
        if len(values) % EFFECT_LEDS:
            raise ValueError(f"{name}: {len(values)} values is not divisible by {EFFECT_LEDS}")
        rows = [values[offset:offset + EFFECT_LEDS] for offset in range(0, len(values), EFFECT_LEDS)]
        if rows:
            effects.append(ImportedEffect(name, _firmware_frames(name, rows)))
    if not effects:
        raise ValueError("No EffectID arrays found in the selected header")
    return effects


def _mask(row: list[int], color: Color) -> list[Color]:
    return [color if value else BLACK for value in row]


def _firmware_frames(name: str, rows: list[list[int]]) -> list[list[Color]]:
    if name == "EffectID2":
        frames: list[list[Color]] = []
        for color_name in ("Red", "Orange", "Yellow", "White"):
            for row in rows[:4]:
                frames.extend((_mask(row, FASTLED_COLORS[color_name]), [BLACK] * EFFECT_LEDS))
        return frames
    if name == "EffectID5":
        frames = []
        for color_name in ("White", "Red"):
            for row in rows[:4]:
                frames.extend((_mask(row, FASTLED_COLORS[color_name]), [BLACK] * EFFECT_LEDS))
        return frames
    if name == "EffectID3":
        return [[BLAST_PALETTE[value - 1] if 1 <= value <= 12 else BLACK for value in row] for row in rows]
    return [_mask(row, FASTLED_COLORS["White"]) for row in rows]
