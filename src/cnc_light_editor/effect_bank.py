from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .effect_importer import ImportedEffect


SORT_MODES = ("id", "name", "size", "duration")


@dataclass(frozen=True)
class BankChange:
    kind: str
    effect_id: int
    description: str


def resolve_effects(
    mapped: Iterable[ImportedEffect], current: ImportedEffect,
) -> list[ImportedEffect]:
    """Insert current into the bank, replacing the mapped effect with the same ID."""
    result: list[ImportedEffect] = []
    inserted = False
    for effect in mapped:
        if effect.effect_id == current.effect_id:
            if not inserted:
                result.append(current)
                inserted = True
            continue
        result.append(effect)
    if not inserted:
        result.append(current)
    return result


def sort_effects(
    effects: Iterable[ImportedEffect], mode: str,
) -> list[ImportedEffect]:
    values = list(effects)
    if mode == "name":
        return sorted(values, key=lambda effect: (effect.name.casefold(), effect.effect_id))
    if mode == "size":
        return sorted(values, key=lambda effect: (-effect.flash_bytes, effect.effect_id))
    if mode == "duration":
        return sorted(values, key=lambda effect: (-effect.duration_ms, effect.effect_id))
    return sorted(values, key=lambda effect: (effect.effect_id, effect.name.casefold()))


def effect_matches(effect: ImportedEffect, query: str) -> bool:
    normalized = query.strip().casefold()
    if not normalized:
        return True
    haystack = " ".join((
        str(effect.effect_id), effect.name, effect.symbol,
        f"{effect.flash_bytes}", f"{effect.duration_ms}",
    )).casefold()
    return all(token in haystack for token in normalized.split())


def total_playback_ms(effects: Iterable[ImportedEffect]) -> int:
    return sum(effect.duration_ms for effect in effects)


def describe_changes(
    original: Iterable[ImportedEffect], planned: Iterable[ImportedEffect],
) -> list[BankChange]:
    before = {effect.effect_id: effect for effect in original}
    after = {effect.effect_id: effect for effect in planned}
    changes: list[BankChange] = []
    for effect_id in sorted(before.keys() | after.keys()):
        old, new = before.get(effect_id), after.get(effect_id)
        if old is None and new is not None:
            changes.append(BankChange("ADD", effect_id, f'Add ID {effect_id}: "{new.name}"'))
            continue
        if new is None and old is not None:
            changes.append(BankChange("REMOVE", effect_id, f'Remove ID {effect_id}: "{old.name}"'))
            continue
        assert old is not None and new is not None
        data_changed = _payload_fingerprint(old) != _payload_fingerprint(new)
        if data_changed:
            changes.append(BankChange(
                "REPLACE", effect_id,
                f'Replace ID {effect_id}: "{old.name}" → "{new.name}"',
            ))
        elif old.name != new.name:
            changes.append(BankChange(
                "RENAME", effect_id,
                f'Rename ID {effect_id}: "{old.name}" → "{new.name}"',
            ))
    return changes


def _payload_fingerprint(effect: ImportedEffect) -> tuple:
    frames = tuple(tuple(frame) for frame in effect.frames)
    return (
        frames, effect.frame_ms, effect.loops, effect.loop_frames,
        effect.overlay, effect.project_data,
    )
