from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil

from cnc_light_editor.effect_bank import resolve_effects
from cnc_light_editor.effect_importer import load_effect_data
from cnc_light_editor.exporter import EFFECT_BANK_CAPACITY, export_effect_bank, project_to_imported_effect
from cnc_light_editor.frame_codec import pack_frames
from cnc_light_editor.ledmap import LedMap
from cnc_light_editor.model import Project


def main() -> None:
    parser = argparse.ArgumentParser(description="Install one editable project into an effect bank")
    parser.add_argument("project", type=Path, nargs="+")
    parser.add_argument("header", type=Path)
    parser.add_argument("--led-map", type=Path, default=Path("data/led_map.json"))
    args = parser.parse_args()

    source = args.header.read_text(encoding="utf-8")
    compressed = bool(re.search(r"#define\s+FX_FRAME_CODEC\s+1\b", source))
    existing = load_effect_data(args.header)
    led_map = LedMap.load(args.led_map)
    errors = led_map.validate()
    if errors:
        raise SystemExit("LED map: " + "; ".join(errors))
    installed = [
        project_to_imported_effect(Project.load(path), led_map.export_slots())
        for path in args.project
    ]
    combined = existing
    for effect in installed:
        combined = resolve_effects(combined, effect)

    backup = args.header.with_name(args.header.name + ".bak")
    shutil.copy2(args.header, backup)
    export_effect_bank(
        combined,
        args.header,
        max_bytes=EFFECT_BANK_CAPACITY,
        compressed=compressed,
    )
    restored = load_effect_data(args.header)
    restored_by_id = {effect.effect_id: effect for effect in restored}
    if any(
        effect.effect_id not in restored_by_id
        or restored_by_id[effect.effect_id].frames != effect.frames
        or restored_by_id[effect.effect_id].project_data is None
        for effect in installed
    ):
        shutil.copy2(backup, args.header)
        raise SystemExit("Verification failed; restored the previous bank")

    stored_bytes = (
        sum(len(pack_frames(effect.frames)) for effect in restored)
        if compressed else sum(effect.flash_bytes for effect in restored)
    )
    print(
        f'Installed {len(installed)} project effect(s) into {args.header} '
        f'({len(restored)} effects, {stored_bytes} stored bytes, '
        f'{"RLE" if compressed else "raw RGB"})'
    )
    print(f"Backup: {backup}")


if __name__ == "__main__":
    main()
