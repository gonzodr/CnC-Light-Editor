"""Opt-in test export: python -m cnc_light_editor.compress_bank INPUT OUTPUT."""
import argparse

from .effect_importer import load_effect_data
from .exporter import export_effect_bank
from .frame_codec import pack_frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    effects = load_effect_data(args.source)
    export_effect_bank(effects, args.destination, compressed=True)
    restored = load_effect_data(args.destination)
    if restored != effects:
        raise RuntimeError("Round-trip verification failed")
    raw = sum(e.flash_bytes for e in effects)
    packed = sum(len(pack_frames(e.frames)) for e in effects)
    print(f"Verified {len(effects)} effects, {sum(len(e.frames) for e in effects)} frames: {raw} -> {packed} bytes")


if __name__ == "__main__":
    main()
