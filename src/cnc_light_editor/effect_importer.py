from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .model import Color


EFFECT_LEDS = 68
CHANNELS_PER_LED = 3
BYTES_PER_FRAME = EFFECT_LEDS * CHANNELS_PER_LED
DEFAULT_FRAME_MS = 50
LEGACY_FRAME_MS = 60
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
    effect_id: int = 0
    symbol: str = ""
    loops: int = 1
    loop_frames: int = 0

    @property
    def normalized_loop_frames(self) -> int:
        if not self.frames:
            return 0
        return len(self.frames) if self.loop_frames == 0 else min(len(self.frames), self.loop_frames)

    @property
    def stored_duration_ms(self) -> int:
        return max(self.frame_ms, len(self.frames) * self.frame_ms)

    @property
    def duration_ms(self) -> int:
        if not self.frames:
            return self.frame_ms
        loop_frames = self.normalized_loop_frames
        steps = loop_frames * max(1, self.loops) + len(self.frames) - loop_frames
        return max(self.frame_ms, steps * self.frame_ms)

    @property
    def flash_bytes(self) -> int:
        return len(self.frames) * BYTES_PER_FRAME

    @property
    def actual_fps(self) -> float:
        return 1000.0 / self.frame_ms

    def colors_at(self, time_ms: int) -> list[Color]:
        if not self.frames:
            return [BLACK] * EFFECT_LEDS
        loop_frames = self.normalized_loop_frames
        cycle_steps = loop_frames * max(1, self.loops)
        total_steps = cycle_steps + len(self.frames) - loop_frames
        step = max(0, min(total_steps - 1, time_ms // self.frame_ms))
        frame_index = step % loop_frames if step < cycle_steps else loop_frames + step - cycle_steps
        return self.frames[frame_index]


def load_effect_data(path: str | Path) -> list[ImportedEffect]:
    return parse_effect_data(Path(path).read_text(encoding="utf-8"))


def parse_effect_data(source: str) -> list[ImportedEffect]:
    clean = _strip_comments(source)
    if re.search(r"\bbakedEffects\s*\[", clean):
        return _parse_v4_effects(clean)
    return _parse_legacy_effects(clean)


def _parse_v4_effects(source: str) -> list[ImportedEffect]:
    arrays: dict[str, list[int]] = {}
    array_pattern = re.compile(
        r"(?:static\s+)?const\s+uint8_t\s+(fx_[A-Za-z_]\w*)\s*\[\s*\]\s+"
        r"PROGMEM\s*=\s*\{(.*?)\}\s*;",
        re.DOTALL,
    )
    for match in array_pattern.finditer(source):
        symbol, body = match.groups()
        values = [_parse_integer(value) for value in re.findall(r"0[xX][0-9A-Fa-f]+|\d+", body)]
        if any(not 0 <= value <= 255 for value in values):
            raise ValueError(f"{symbol}: RGB values must be between 0 and 255")
        arrays[symbol] = values

    table_match = re.search(
        r"const\s+EffectDef\s+bakedEffects\s*\[\s*\]\s*(?:PROGMEM\s*)?=\s*\{(.*?)\}\s*;",
        source,
        re.DOTALL,
    )
    if not table_match:
        raise ValueError("No bakedEffects[] table found in the selected V4 header")

    row_pattern = re.compile(
        r"\{\s*(\d+)\s*,\s*\"((?:\\.|[^\"\\])*)\"\s*,\s*"
        r"(fx_[A-Za-z_]\w*)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\}"
    )
    effects: list[ImportedEffect] = []
    seen_ids: set[int] = set()
    for match in row_pattern.finditer(table_match.group(1)):
        raw_id, raw_name, symbol, raw_frames, raw_frame_ms, raw_loops, raw_loop_frames = match.groups()
        effect_id = int(raw_id)
        frame_count = int(raw_frames)
        frame_ms = int(raw_frame_ms)
        loops = int(raw_loops) or 1
        loop_frames = int(raw_loop_frames)
        name = json.loads(f'"{raw_name}"')

        if effect_id in seen_ids:
            raise ValueError(f"Duplicate effect ID {effect_id} in bakedEffects[]")
        seen_ids.add(effect_id)
        if not 1 <= effect_id <= 255:
            raise ValueError(f"{name}: effect ID must be between 1 and 255")
        if frame_count < 1:
            raise ValueError(f"{name}: frames must be at least 1")
        if not 1 <= frame_ms <= 65535:
            raise ValueError(f"{name}: frameMs must be between 1 and 65535")
        if not 1 <= loops <= 255:
            raise ValueError(f"{name}: loops must be between 1 and 255")
        if not 0 <= loop_frames <= frame_count:
            raise ValueError(f"{name}: loopFrames must be between 0 and frames ({frame_count})")
        if symbol not in arrays:
            raise ValueError(f"{name}: referenced data array {symbol} was not found")

        values = arrays[symbol]
        expected = frame_count * BYTES_PER_FRAME
        if len(values) != expected:
            raise ValueError(f"{name}: {len(values)} data bytes found, expected {expected} ({frame_count} * 204)")
        frames: list[list[Color]] = []
        for frame_offset in range(0, expected, BYTES_PER_FRAME):
            chunk = values[frame_offset:frame_offset + BYTES_PER_FRAME]
            frames.append([tuple(chunk[index:index + 3]) for index in range(0, BYTES_PER_FRAME, 3)])
        effects.append(ImportedEffect(name, frames, frame_ms, effect_id, symbol, loops, loop_frames))

    if not effects:
        raise ValueError("No valid effect rows found in bakedEffects[]")
    return effects


def _parse_legacy_effects(source: str) -> list[ImportedEffect]:
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
            effect_id = int(name.removeprefix("EffectID"))
            effects.append(ImportedEffect(
                name, _firmware_frames(name, rows), frame_ms=LEGACY_FRAME_MS, effect_id=effect_id,
            ))
    if not effects:
        raise ValueError("No V4 bakedEffects table or legacy EffectID arrays found in the selected header")
    return effects


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\r\n]*", "", source)


def _parse_integer(value: str) -> int:
    return int(value, 0)


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
