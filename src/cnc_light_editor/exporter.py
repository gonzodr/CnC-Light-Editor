from __future__ import annotations

import math
from pathlib import Path
import re
import unicodedata

from .engine import render_leds_with_mask
from .effect_importer import ImportedEffect
from .model import Project
from .project_bundle import embedded_project_lines

FIRMWARE_LED_COUNT = 68
BYTES_PER_FRAME = FIRMWARE_LED_COUNT * 3
EFFECT_BANK_CAPACITY = 150 * 1024
TRANSPARENT_SENTINEL = (255, 0, 255)

# The Mega's USB-serial link (ATmega16U2 -> ATmega2560 UART) is sensitive to
# specific byte patterns: one particular 256-byte page of raw effect data
# reliably killed the avrdude transfer at 28%, on every host, while the same
# page XOR-masked went up fine. Storing the fx_* tables masked scrambles those
# patterns; the firmware XORs them back when reading (see FX_DATA_MASK in
# d_light_effects.ino). Both sides must use the same value.
FX_DATA_MASK = 0x5A
FX_DATA_MASK_DEFINE = "FX_DATA_MASK_APPLIED"


def sample_project_frames(
    project: Project,
    led_points: list[tuple[float, float] | None],
) -> list[tuple[int, ...]]:
    """Bake the project into fixed-duration, frame-major V4 RGB data."""
    frame_ms = max(1, int(project.frame_ms or 1))
    frame_count = max(1, math.ceil(project.duration_ms / frame_ms))
    slots = list(led_points[:FIRMWARE_LED_COUNT])
    slots.extend([None] * (FIRMWARE_LED_COUNT - len(slots)))
    active_points = [point for point in slots if point is not None]

    falloff_output = [(0, 0, 0) for _point in slots]
    falloff_starts = [[0, 0, 0] for _point in slots]
    falloff_targets = [[0, 0, 0] for _point in slots]
    falloff_elapsed = [[0, 0, 0] for _point in slots]
    falloff_active = [[False, False, False] for _point in slots]

    frames: list[tuple[int, ...]] = []
    for frame_index in range(frame_count):
        time_ms = frame_index * frame_ms
        rendered, painted = render_leds_with_mask(project, active_points, time_ms)
        active_cells = iter(zip(rendered, painted))
        raw_cells: list[tuple[tuple[int, int, int], bool]] = []
        for point in slots:
            raw_cells.append(((0, 0, 0), False) if point is None else next(active_cells))

        falloff_ms = project.falloff_duration_at(time_ms)
        processed: list[tuple[tuple[int, int, int], bool]] = []
        for slot_index, (raw, is_painted) in enumerate(raw_cells):
            if slots[slot_index] is None or falloff_ms <= 0:
                output = raw
                falloff_output[slot_index] = raw
                falloff_starts[slot_index][:] = raw
                falloff_targets[slot_index][:] = raw
                falloff_elapsed[slot_index][:] = [0, 0, 0]
                falloff_active[slot_index][:] = [False, False, False]
            else:
                previous = falloff_output[slot_index]
                output_channels: list[int] = []
                for channel in range(3):
                    current = raw[channel]
                    if current >= previous[channel]:
                        value = current
                        falloff_active[slot_index][channel] = False
                        falloff_elapsed[slot_index][channel] = 0
                    else:
                        if not falloff_active[slot_index][channel]:
                            falloff_starts[slot_index][channel] = previous[channel]
                            falloff_targets[slot_index][channel] = current
                            falloff_elapsed[slot_index][channel] = 0
                            falloff_active[slot_index][channel] = True
                        else:
                            # A source that keeps dimming should not restart the
                            # tail every frame.  Move its target down while the
                            # original fade clock keeps running.
                            falloff_targets[slot_index][channel] = min(
                                falloff_targets[slot_index][channel], current,
                            )
                        falloff_elapsed[slot_index][channel] += frame_ms
                        amount = min(
                            1.0,
                            falloff_elapsed[slot_index][channel] / max(1, falloff_ms),
                        )
                        start = falloff_starts[slot_index][channel]
                        target = falloff_targets[slot_index][channel]
                        value = max(current, round(start + (target - start) * amount))
                        if amount >= 1.0 or value <= current:
                            falloff_active[slot_index][channel] = False
                    output_channels.append(max(0, min(255, value)))
                output = tuple(output_channels)
                falloff_output[slot_index] = output
            processed.append((output, is_painted or any(output)))

        transparent_canvas = project.canvas_transparency_at(time_ms)
        colors = []
        for point, (color, is_painted) in zip(slots, processed):
            if point is None:
                colors.append(TRANSPARENT_SENTINEL if project.overlay else (0, 0, 0))
                continue
            if transparent_canvas and not is_painted:
                colors.append(TRANSPARENT_SENTINEL)
            elif project.overlay and color == TRANSPARENT_SENTINEL:
                colors.append((254, 0, 255))
            else:
                colors.append(color)
        frames.append(tuple(channel for color in colors for channel in color))
    return frames


def export_arduino_header(
    project: Project,
    led_points: list[tuple[float, float] | None],
    path: str | Path,
) -> Path:
    effect = project_to_imported_effect(project, led_points)
    return export_effect_bank([effect], path, max_bytes=None)


def project_to_imported_effect(
    project: Project,
    led_points: list[tuple[float, float] | None],
) -> ImportedEffect:
    frames = sample_project_frames(project, led_points)
    _validate_metadata(project, len(frames))
    colors = [
        [tuple(frame[index:index + 3]) for index in range(0, len(frame), 3)]
        for frame in frames
    ]
    return ImportedEffect(
        name=project.name,
        frames=colors,
        frame_ms=int(project.frame_ms or 1),
        effect_id=project.effect_id,
        symbol=f"fx_{_identifier(project.name)}",
        loops=project.loops,
        loop_frames=len(frames) if project.loop_frames == 0 else project.loop_frames,
        overlay=project.overlay,
        project_data=project.to_dict(),
        intro_frames=project.normalized_intro_frames,
    )


def export_effect_bank(
    effects: list[ImportedEffect],
    path: str | Path,
    max_bytes: int | None = EFFECT_BANK_CAPACITY,
) -> Path:
    _validate_effect_bank(effects, max_bytes)
    symbols = _unique_symbols(effects)
    total_bytes = sum(effect.flash_bytes for effect in effects)
    capacity_note = (
        f" of {max_bytes} bytes ({max_bytes / 1024:.1f} KiB bank)"
        if max_bytes is not None else ""
    )

    lines = [
        "#pragma once",
        "// Generated by CnC Light Editor for the V4 baked-frame firmware.",
        "// Frame-major RGB: 68 LEDs * 3 channels = 204 bytes per stored frame.",
        "// OVERLAY sentinel: (255,0,255) means transparent; painted FF00FF is exported as FE00FF.",
        "// NULL LED-map slots use the transparent sentinel in OVERLAY mode (black in FULL mode).",
        "// Embedded CnC Light projects are compressed Base64 comments and consume no firmware flash.",
        f"// Effect bank: {len(effects)} effects, {total_bytes} bytes{capacity_note}.",
        "// Opacity, easing and fades are pre-composited into the RGB values.",
        f"// fx_* data is stored XOR 0x{FX_DATA_MASK:02X}: raw effect bytes could contain patterns that",
        "// break the avrdude upload over the Mega's USB-serial link. The firmware decodes",
        "// on read (FX_DATA_MASK in d_light_effects.ino) - both sides must match.",
        "#include <Arduino.h>",
        "#include <avr/pgmspace.h>",
        "",
        "// Keep Arduino core pin tables below 64 KiB; this bank is read with far access.",
        '#define FX_DATA_PROGMEM __attribute__((section(".text.fxdata"), used))',
        "",
        f"#define {FX_DATA_MASK_DEFINE} 0x{FX_DATA_MASK:02X}",
        "",
        "struct EffectDef {",
        "  uint8_t id;",
        "  const char* name;",
        "  uint16_t frames;",
        "  uint16_t frameMs;",
        "  uint8_t loops;",
        "  uint16_t loopFrames;",
        "  uint8_t overlay;",
        "  uint16_t introFrames;",
        "};",
    ]
    for effect, symbol in zip(effects, symbols):
        mode = (
            "OVERLAY/CANVAS (FF00FF sentinel is transparent; black stays opaque)"
            if effect.overlay else "FULL (black stays black)"
        )
        lines.extend([
            "",
            f"// {effect.name}: {len(effect.frames)} frames, {effect.flash_bytes} bytes, "
            f"{effect.actual_fps:.3f} FPS; {mode}.",
            f"const uint8_t {symbol}[] FX_DATA_PROGMEM = {{",
        ])
        for frame_index, colors in enumerate(effect.frames):
            frame = [channel for color in colors for channel in color]
            lines.append(f"  // frame {frame_index}")
            lines.append(
                "  " + ", ".join(str(value ^ FX_DATA_MASK) for value in frame) + ","
            )
        lines.append("};")
        if effect.project_data is not None:
            lines.extend(embedded_project_lines(effect.effect_id, effect.project_data))
    lines.extend([
        "",
        "// Generated far-flash lookup: d_light_effects.ino calls this by effect ID.",
        "static inline uint_farptr_t bakedEffectFarAddress(uint8_t id) {",
        "  switch (id) {",
    ])
    for effect, symbol in zip(effects, symbols):
        lines.append(f"    case {effect.effect_id}: return pgm_get_far_address({symbol});")
    lines.extend([
        "    default: return 0;",
        "  }",
        "}",
        "",
        "const EffectDef bakedEffects[] = {",
        "  // ID, name, frames, frameMs, loops, loopFrames, overlay, introFrames",
    ])
    for effect, symbol in zip(effects, symbols):
        escaped_name = effect.name.replace("\\", "\\\\").replace('"', '\\"')
        loop_frames = effect.normalized_loop_frames
        intro_frames = effect.normalized_intro_frames
        lines.append(
            f'  {{ {effect.effect_id}, "{escaped_name}", {len(effect.frames)}, '
            f"{effect.frame_ms}, {effect.loops}, {loop_frames}, {int(bool(effect.overlay))}, "
            f"{intro_frames} }},"
        )
    lines.extend([
        "};",
        "const uint8_t bakedEffectCount = sizeof(bakedEffects) / sizeof(bakedEffects[0]);",
        "",
    ])

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(destination)
    return destination


def _validate_effect_bank(effects: list[ImportedEffect], max_bytes: int | None) -> None:
    if not effects:
        raise ValueError("Effect bank cannot be empty")
    seen_ids: set[int] = set()
    for effect in effects:
        if not 1 <= effect.effect_id <= 255:
            raise ValueError(f"{effect.name}: effect ID must be between 1 and 255")
        if effect.effect_id in seen_ids:
            raise ValueError(f"Duplicate effect ID {effect.effect_id}")
        seen_ids.add(effect.effect_id)
        if not effect.name.strip():
            raise ValueError("Effect name cannot be empty")
        if not effect.frames:
            raise ValueError(f"{effect.name}: frames must be at least 1")
        if not 1 <= effect.frame_ms <= 65535:
            raise ValueError(f"{effect.name}: frameMs must be between 1 and 65535")
        if not 1 <= effect.loops <= 255:
            raise ValueError(f"{effect.name}: loops must be between 1 and 255")
        if not 0 <= effect.loop_frames <= len(effect.frames):
            raise ValueError(f"{effect.name}: loopFrames must be between 0 and frames")
        loop_end = len(effect.frames) if effect.loop_frames == 0 else effect.loop_frames
        if not 0 <= effect.intro_frames <= loop_end:
            raise ValueError(f"{effect.name}: introFrames must be between 0 and the loop end ({loop_end})")
        for frame in effect.frames:
            if len(frame) != FIRMWARE_LED_COUNT:
                raise ValueError(f"{effect.name}: every frame must contain exactly 68 LEDs")
            if any(len(color) != 3 or any(not 0 <= channel <= 255 for channel in color) for color in frame):
                raise ValueError(f"{effect.name}: RGB values must be between 0 and 255")
    total_bytes = sum(effect.flash_bytes for effect in effects)
    if max_bytes is not None and total_bytes > max_bytes:
        raise ValueError(
            f"Effect bank uses {total_bytes} bytes, exceeding the {max_bytes} byte limit"
        )


def _unique_symbols(effects: list[ImportedEffect]) -> list[str]:
    symbols: list[str] = []
    used: set[str] = set()
    for effect in effects:
        base = (
            effect.symbol
            if re.fullmatch(r"fx_[A-Za-z_]\w*", effect.symbol or "")
            else f"fx_{_identifier(effect.name)}"
        )
        symbol = base
        suffix = 2
        while symbol in used:
            symbol = f"{base}_{suffix}"
            suffix += 1
        used.add(symbol)
        symbols.append(symbol)
    return symbols


def _validate_metadata(project: Project, frames: int) -> None:
    if not 1 <= project.effect_id <= 255:
        raise ValueError("Effect ID must be between 1 and 255")
    if not project.name.strip():
        raise ValueError("Effect name cannot be empty")
    if not 1 <= int(project.frame_ms or 0) <= 65535:
        raise ValueError("frameMs must be between 1 and 65535")
    if not 1 <= project.loops <= 255:
        raise ValueError("loops must be between 1 and 255")
    if not 0 <= project.loop_frames <= frames:
        raise ValueError(f"loopFrames must be between 0 and {frames}")
    loop_end = frames if project.loop_frames == 0 else project.loop_frames
    if not 0 <= project.intro_frames <= loop_end:
        raise ValueError(f"introFrames must be between 0 and the loop end ({loop_end})")


def _identifier(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    identifier = re.sub(r"[^A-Za-z0-9_]+", "_", ascii_value.strip()).strip("_").lower()
    if not identifier:
        identifier = "effect"
    if identifier[0].isdigit():
        identifier = "effect_" + identifier
    return identifier
