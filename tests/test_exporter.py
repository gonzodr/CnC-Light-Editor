import pytest

from cnc_light_editor.effect_importer import parse_effect_data
from cnc_light_editor.exporter import export_arduino_header
from cnc_light_editor.model import Layer, Project, Shape


def test_export_pads_playfield_map_to_68_leds_and_writes_v4_metadata(tmp_path):
    shape = Shape("rectangle", "all", width=1.0, height=1.0, color=(1, 2, 3))
    project = Project(
        "Launch flash", duration_ms=100, fps=10, effect_id=6, frame_ms=50,
        loops=3, loop_frames=1, layers=[Layer("one", shapes=[shape])],
    )
    path = export_arduino_header(project, [(0.5, 0.5)] * 59, tmp_path / "effect_data.h")
    text = path.read_text(encoding="utf-8")
    effect = parse_effect_data(text)[0]

    assert "const uint8_t fx_launch_flash[] PROGMEM" in text
    assert '{ 6, "Launch flash", fx_launch_flash, 2, 50, 3, 1 }' in text
    assert effect.effect_id == 6
    assert effect.frame_ms == 50
    assert effect.loops == 3
    assert effect.loop_frames == 1
    assert len(effect.frames) == 2
    assert len(effect.frames[0]) == 68
    assert effect.frames[0][0] == (1, 2, 3)
    assert effect.frames[0][-9:] == [(0, 0, 0)] * 9


def test_identical_frames_are_kept_for_fixed_frame_ms_firmware(tmp_path):
    shape = Shape("rectangle", "all", width=1.0, height=1.0, color=(4, 5, 6))
    project = Project(duration_ms=200, frame_ms=50, layers=[Layer("one", shapes=[shape])])

    path = export_arduino_header(project, [(0.5, 0.5)], tmp_path / "effect_data.h")
    effect = parse_effect_data(path.read_text(encoding="utf-8"))[0]

    assert len(effect.frames) == 4
    assert len(set(tuple(frame) for frame in effect.frames)) == 1


def test_null_slot_exports_black_rgb(tmp_path):
    shape = Shape("rectangle", "all", width=1.0, height=1.0, color=(10, 20, 30))
    project = Project(duration_ms=100, frame_ms=50, layers=[Layer("one", shapes=[shape])])
    slots = [(0.5, 0.5)] * 59
    slots[7] = None
    path = export_arduino_header(project, slots, tmp_path / "effect_data.h")
    frame = parse_effect_data(path.read_text(encoding="utf-8"))[0].frames[0]

    assert frame[6] == (10, 20, 30)
    assert frame[7] == (0, 0, 0)
    assert frame[8] == (10, 20, 30)


def test_export_rejects_loop_boundary_past_stored_frames(tmp_path):
    project = Project(duration_ms=100, frame_ms=50, loop_frames=3)

    with pytest.raises(ValueError, match="loopFrames"):
        export_arduino_header(project, [], tmp_path / "effect_data.h")
