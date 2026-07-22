import pytest

from cnc_light_editor.effect_importer import ImportedEffect, parse_effect_data
from cnc_light_editor.exporter import export_arduino_header, export_effect_bank
from cnc_light_editor.model import Layer, Project, Shape


def test_export_pads_playfield_map_to_68_leds_and_writes_v4_metadata(tmp_path):
    shape = Shape("rectangle", "all", width=1.0, height=1.0, color=(1, 2, 3))
    project = Project(
        "Launch flash", duration_ms=100, fps=10, effect_id=6, frame_ms=50,
        loops=3, loop_frames=1, overlay=True, layers=[Layer("one", shapes=[shape])],
    )
    path = export_arduino_header(project, [(0.5, 0.5)] * 59, tmp_path / "effect_data.h")
    text = path.read_text(encoding="utf-8")
    effect = parse_effect_data(text)[0]

    assert "const uint8_t fx_launch_flash[] PROGMEM" in text
    assert '{ 6, "Launch flash", fx_launch_flash, 2, 50, 3, 1, 1 }' in text
    assert effect.effect_id == 6
    assert effect.frame_ms == 50
    assert effect.loops == 3
    assert effect.loop_frames == 1
    assert effect.overlay is True
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


def test_export_defaults_to_full_canvas_mode(tmp_path):
    project = Project("full effect", duration_ms=50, frame_ms=50)

    path = export_arduino_header(project, [], tmp_path / "effect_data.h")
    text = path.read_text(encoding="utf-8")
    effect = parse_effect_data(text)[0]

    assert "FULL (black stays black)" in text
    assert effect.overlay is False


def test_effect_bank_exports_multiple_editable_effect_definitions(tmp_path):
    black_frame = [[(0, 0, 0)] * 68]
    effects = [
        ImportedEffect("Pulse", black_frame, effect_id=2),
        ImportedEffect("Pulse", black_frame * 2, frame_ms=40, effect_id=7, loops=3),
    ]

    path = export_effect_bank(effects, tmp_path / "effect_data.h")
    text = path.read_text(encoding="utf-8")
    loaded = parse_effect_data(text)

    assert [effect.effect_id for effect in loaded] == [2, 7]
    assert [effect.name for effect in loaded] == ["Pulse", "Pulse"]
    assert "const uint8_t fx_pulse[] PROGMEM" in text
    assert "const uint8_t fx_pulse_2[] PROGMEM" in text


def test_effect_bank_rejects_duplicate_ids_and_capacity_overflow(tmp_path):
    black_frame = [[(0, 0, 0)] * 68]
    duplicate_ids = [
        ImportedEffect("One", black_frame, effect_id=4),
        ImportedEffect("Two", black_frame, effect_id=4),
    ]
    with pytest.raises(ValueError, match="Duplicate effect ID 4"):
        export_effect_bank(duplicate_ids, tmp_path / "duplicate.h")

    too_large = [ImportedEffect("Large", black_frame * 3, effect_id=5)]
    with pytest.raises(ValueError, match="exceeding"):
        export_effect_bank(too_large, tmp_path / "large.h", max_bytes=500)


def test_overlay_canvas_layer_switches_empty_cells_between_black_and_sentinel(tmp_path):
    canvas = Layer("Canvas", is_canvas=True, canvas_enabled=False)
    canvas.add_keyframe("canvas_enabled", 0, False)
    canvas.add_keyframe("canvas_enabled", 50, True)
    project = Project(
        "dynamic canvas", duration_ms=100, frame_ms=50, overlay=True,
        layers=[Layer("art"), canvas],
    )

    path = export_arduino_header(project, [(0.5, 0.5)], tmp_path / "canvas.h")
    frames = parse_effect_data(path.read_text(encoding="utf-8"))[0].frames

    assert frames[0][0] == (0, 0, 0)
    assert frames[1][0] == (255, 0, 255)


def test_export_uses_first_future_keyframe_value_before_its_timestamp(tmp_path):
    shape = Shape(
        "rectangle", "future color", width=1.0, height=1.0, color=(0, 0, 0),
    )
    shape.add_keyframe("color", 50, (90, 120, 150))
    project = Project(
        "backward hold", duration_ms=100, frame_ms=50,
        layers=[Layer("art", shapes=[shape])],
    )

    path = export_arduino_header(project, [(0.5, 0.5)], tmp_path / "hold.h")
    frames = parse_effect_data(path.read_text(encoding="utf-8"))[0].frames

    assert frames[0][0] == (90, 120, 150)
    assert frames[1][0] == (90, 120, 150)


def test_overlay_export_preserves_opaque_black_and_avoids_real_magenta_collision(tmp_path):
    black = Shape("rectangle", "black", x=0.25, width=0.2, height=1.0, color=(0, 0, 0))
    magenta = Shape("rectangle", "magenta", x=0.75, width=0.2, height=1.0, color=(255, 0, 255))
    project = Project(
        "sentinel safety", duration_ms=50, frame_ms=50, overlay=True,
        layers=[Layer("paint", shapes=[black, magenta])],
    )

    path = export_arduino_header(
        project, [(0.25, 0.5), (0.5, 0.5), (0.75, 0.5), None],
        tmp_path / "sentinel.h",
    )
    frame = parse_effect_data(path.read_text(encoding="utf-8"))[0].frames[0]

    assert frame[0] == (0, 0, 0)
    assert frame[1] == (255, 0, 255)
    assert frame[2] == (254, 0, 255)
    assert frame[3] == (0, 0, 0)
