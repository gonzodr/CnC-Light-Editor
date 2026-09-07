from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .model import Color
from .frame_codec import unpack_frames
from .project_bundle import extract_embedded_projects


EFFECT_LEDS = 68
CHANNELS_PER_LED = 3
BYTES_PER_FRAME = EFFECT_LEDS * CHANNELS_PER_LED
DEFAULT_FRAME_MS = 50
LEGACY_FRAME_MS = 60
# Headers written by the exporter store the fx_* tables XOR-masked (see
# FX_DATA_MASK in exporter.py) and announce it with this #define. Headers
# without it are read as-is, so pre-mask files still import unchanged.
FX_DATA_MASK_DEFINE = "FX_DATA_MASK_APPLIED"
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
    overlay: bool = False
    project_data: dict | None = None
    intro_frames: int = 0

    @property
    def normalized_loop_frames(self) -> int:
        if not self.frames:
            return 0
        return len(self.frames) if self.loop_frames == 0 else min(len(self.frames), self.loop_frames)

    @property
    def normalized_intro_frames(self) -> int:
        return max(0, min(self.intro_frames, self.normalized_loop_frames))

    @property
    def stored_duration_ms(self) -> int:
        return max(self.frame_ms, len(self.frames) * self.frame_ms)

    @property
    def duration_ms(self) -> int:
        if not self.frames:
            return self.frame_ms
        intro_frames = self.normalized_intro_frames
        loop_end = self.normalized_loop_frames
        loop_len = loop_end - intro_frames
        steps = intro_frames + loop_len * max(1, self.loops) + len(self.frames) - loop_end
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
        intro_frames = self.normalized_intro_frames
        loop_end = self.normalized_loop_frames
        loop_len = loop_end - intro_frames
        cycle_steps = loop_len * max(1, self.loops)
        total_steps = intro_frames + cycle_steps + len(self.frames) - loop_end
        step = max(0, min(total_steps - 1, time_ms // self.frame_ms))
        if step < intro_frames:
            frame_index = step
        else:
            after_intro = step - intro_frames
            frame_index = (
                intro_frames + (after_intro % loop_len if loop_len else 0)
                if after_intro < cycle_steps
                else loop_end + after_intro - cycle_steps
            )
        return self.frames[frame_index]


def load_effect_data(path: str | Path) -> list[ImportedEffect]:
    return parse_effect_data(Path(path).read_text(encoding="utf-8"))


def parse_effect_data(source: str) -> list[ImportedEffect]:
    embedded_projects = extract_embedded_projects(source)
    clean = _strip_comments(source)
    if re.search(r"\bbakedEffects\s*\[", clean):
        return _parse_v4_effects(clean, embedded_projects)
    return _parse_legacy_effects(clean)


def _parse_v4_effects(
    source: str, embedded_projects: dict[int, dict] | None = None,
) -> list[ImportedEffect]:
    embedded_projects = embedded_projects or {}
    data_mask = _detect_data_mask(source)
    arrays: dict[str, list[int]] = {}
    array_pattern = re.compile(
        r"(?:static\s+)?const\s+uint8_t\s+(fx_[A-Za-z_]\w*)\s*\[\s*\]\s+"
        r"(?:PROGMEM|FX_DATA_PROGMEM)\s*=\s*\{(.*?)\}\s*;",
        re.DOTALL,
    )
    for match in array_pattern.finditer(source):
        symbol, body = match.groups()
        values = [_parse_integer(value) for value in re.findall(r"0[xX][0-9A-Fa-f]+|\d+", body)]
        if any(not 0 <= value <= 255 for value in values):
            raise ValueError(f"{symbol}: RGB values must be between 0 and 255")
        # Undo the exporter's XOR so everything downstream sees real RGB.
        arrays[symbol] = [value ^ data_mask for value in values] if data_mask else values

    table_match = re.search(
        r"const\s+EffectDef\s+bakedEffects\s*\[\s*\]\s*(?:PROGMEM\s*)?=\s*\{(.*?)\}\s*;",
        source,
        re.DOTALL,
    )
    if not table_match:
        raise ValueError("No bakedEffects[] table found in the selected V4 header")

    far_address_symbols = {
        int(raw_id): symbol
        for raw_id, symbol in re.findall(
            r"case\s+(\d+)\s*:\s*return\s+pgm_get_far_address\s*\(\s*"
            r"(fx_[A-Za-z_]\w*)\s*\)\s*;",
            source,
        )
    }
    array_symbols = list(arrays)
    row_pattern = re.compile(
        r"\{\s*(\d+)\s*,\s*\"((?:\\.|[^\"\\])*)\"\s*,\s*"
        r"(?:(fx_[A-Za-z_]\w*)\s*,\s*)?(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)"
        r"\s*(?:,\s*(\d+))?\s*(?:,\s*(\d+))?\s*\}"
    )
    effects: list[ImportedEffect] = []
    seen_ids: set[int] = set()
    for row_index, match in enumerate(row_pattern.finditer(table_match.group(1))):
        (
            raw_id, raw_name, row_symbol, raw_frames, raw_frame_ms, raw_loops,
            raw_loop_frames, raw_overlay, raw_intro_frames,
        ) = match.groups()
        effect_id = int(raw_id)
        symbol = row_symbol or far_address_symbols.get(effect_id)
        if symbol is None and row_index < len(array_symbols):
            # The first far-PROGMEM firmware revision removed the 16-bit data
            # pointer before the generated address switch lived in the header.
            # Those transitional files keep arrays and metadata rows in the
            # same order, so retain compatibility with them as well.
            symbol = array_symbols[row_index]
        frame_count = int(raw_frames)
        frame_ms = int(raw_frame_ms)
        loops = int(raw_loops) or 1
        loop_frames = int(raw_loop_frames)
        overlay = int(raw_overlay or 0)
        intro_frames = int(raw_intro_frames or 0)
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
        if overlay not in (0, 1):
            raise ValueError(f"{name}: overlay must be 0 (FULL) or 1 (CANVAS)")
        normalized_loop_end = frame_count if loop_frames == 0 else loop_frames
        if not 0 <= intro_frames <= normalized_loop_end:
            raise ValueError(f"{name}: introFrames must be between 0 and the loop end ({normalized_loop_end})")
        if symbol not in arrays:
            raise ValueError(f"{name}: referenced data array {symbol} was not found")

        values = arrays[symbol]
        codec = re.search(r"#define\s+FX_FRAME_CODEC\s+(\d+)", source)
        if codec:
            if int(codec.group(1)) != 1:
                raise ValueError("Unsupported frame codec")
            values = [c for frame in unpack_frames(values, frame_count) for rgb in frame for c in rgb]
        expected = frame_count * BYTES_PER_FRAME
        if len(values) != expected:
            raise ValueError(f"{name}: {len(values)} data bytes found, expected {expected} ({frame_count} * 204)")
        frames: list[list[Color]] = []
        for frame_offset in range(0, expected, BYTES_PER_FRAME):
            chunk = values[frame_offset:frame_offset + BYTES_PER_FRAME]
            frames.append([tuple(chunk[index:index + 3]) for index in range(0, BYTES_PER_FRAME, 3)])
        effects.append(ImportedEffect(
            name, frames, frame_ms, effect_id, symbol, loops, loop_frames, bool(overlay),
            embedded_projects.get(effect_id), intro_frames,
        ))

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


def _detect_data_mask(source: str) -> int:
    """Read the XOR mask the header was written with, or 0 if it carries none.

    Comments are already stripped by the caller, so a commented-out #define
    correctly reads as "not masked" instead of silently scrambling the import.
    """
    match = re.search(
        rf"#\s*define\s+{FX_DATA_MASK_DEFINE}\s+(0[xX][0-9A-Fa-f]+|\d+)", source
    )
    if not match:
        return 0
    mask = _parse_integer(match.group(1))
    if not 0 <= mask <= 255:
        raise ValueError(f"{FX_DATA_MASK_DEFINE} must be between 0 and 255, got {mask}")
    return mask


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
