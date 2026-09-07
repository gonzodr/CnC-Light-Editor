import random

import pytest

from cnc_light_editor.frame_codec import pack_frames, unpack_frames
from cnc_light_editor.effect_importer import ImportedEffect, load_effect_data
from cnc_light_editor.exporter import export_effect_bank


def test_roundtrip_raw_rle_transparency_and_black(tmp_path):
    rng = random.Random(42)
    frames = [[(255, 0, 255)] * 68, [(0, 0, 0)] * 68,
              [tuple(rng.randrange(256) for _ in range(3)) for _ in range(68)],
              [(0, 0, 0), (255, 0, 255)] * 34]
    packed = pack_frames(frames)
    assert unpack_frames(packed, 4) == frames
    offsets = [int.from_bytes(packed[i:i+2], 'little') for i in range(0, 8, 2)]
    assert [packed[i] for i in offsets] == [1, 1, 0, 0]
    effect = ImportedEffect('Test', frames, effect_id=3, loops=3, loop_frames=3, intro_frames=1, overlay=True)
    path = export_effect_bank([effect], tmp_path / 'bank.h', compressed=True)
    restored = load_effect_data(path)[0]
    assert restored.frames == frames
    for t in range(0, 700, 10):
        assert restored.colors_at(t) == effect.colors_at(t)


def test_reject_corrupt_run():
    packed = bytearray(pack_frames([[(0, 0, 0)] * 68]))
    packed[3] = 69
    with pytest.raises(ValueError):
        unpack_frames(packed, 1)
