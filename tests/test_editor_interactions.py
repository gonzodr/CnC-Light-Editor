import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from cnc_light_editor.app import Editor
from cnc_light_editor.effect_importer import EFFECT_LEDS, ImportedEffect
from cnc_light_editor.model import Project


def make_editor() -> Editor:
    pygame.init()
    editor = Editor(pygame.display.set_mode((1280, 900)))
    editor.project = Project("interaction test")
    editor.selected = None
    editor.undo_stack.clear()
    editor.redo_stack.clear()
    return editor


def test_drag_created_shape_can_be_undone_and_redone():
    editor = make_editor()
    editor._create_shape("rectangle", (0.25, 0.4))

    assert len(editor.project.layers[0].shapes) == 1
    assert editor.selected is editor.project.layers[0].shapes[0]

    editor._undo()
    assert editor.project.layers[0].shapes == []

    editor._redo()
    assert len(editor.project.layers[0].shapes) == 1


def test_selection_handles_resize_and_rotation():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    canvas, _, _ = editor.layout()
    state = dict(editor.selected.state_at(0))
    editor.drag_shape_state = state

    editor.drag_mode = "resize_se"
    editor._transform_selection((canvas.centerx + 80, canvas.centery + 100), canvas)
    resized = editor.selected.state_at(0)
    assert resized["width"] > state["width"]
    assert resized["height"] > state["height"]

    editor.drag_shape_state = dict(resized)
    editor.drag_mode = "rotate"
    editor._transform_selection((canvas.centerx + 100, canvas.centery), canvas)
    assert editor.selected.state_at(0)["rotation"] == 90


def test_keyframe_drag_snaps_to_frame_boundary():
    editor = make_editor()
    editor._create_shape("line", (0.5, 0.5))
    editor.selected.add_keyframe("x", 1000, 0.6)
    _, _, timeline = editor.layout()
    editor.drag_key_time = 1000
    track = editor._timeline_track(timeline)

    editor._move_keyframe(track.x + track.width // 2, timeline)

    moved = editor.selected.keyframes["x"][0].time_ms
    frame_ms = round(1000 / editor.project.fps)
    assert moved % frame_ms == 0


def test_selected_keyframe_delete_keeps_shape_and_undo_restores_diamond():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.selected.add_keyframe("x", 0, 0.5)
    editor.selected.add_keyframe("x", 1000, 0.7)
    editor.selected_keyframes = {(editor.selected.id, "x", 1000)}

    editor._delete_selected_keyframes()
    assert len(editor.project.layers[0].shapes) == 1
    assert [frame.time_ms for frame in editor.selected.keyframes["x"]] == [0]

    editor._undo()
    assert editor.selected is not None
    assert [frame.time_ms for frame in editor.selected.keyframes["x"]] == [0, 1000]


def test_context_action_sets_easing_on_selected_keyframe():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.selected.add_keyframe("x", 1000, 0.7)
    editor.selected_keyframes = {(editor.selected.id, "x", 1000)}

    editor._apply_keyframe_context("ease_out")

    assert editor.selected.keyframes["x"][0].easing == "ease_out"


def test_timeline_zoom_and_duration_are_independent():
    editor = make_editor()
    _, _, timeline = editor.layout()
    track = editor._timeline_track(timeline)
    anchor_x = track.centerx
    anchor_before = editor._timeline_x_to_time(anchor_x, timeline)

    editor._zoom_timeline(3, anchor_x, timeline)
    anchor_after = editor._timeline_x_to_time(anchor_x, timeline)
    assert abs(anchor_after - anchor_before) <= 1
    assert editor.timeline_zoom > 1

    slider = editor._duration_slider_rect(timeline)
    editor._set_duration_from_x(slider.x + round(slider.width * 1.5 / 14.5), slider)
    assert 1900 <= editor.project.duration_ms <= 2100


def test_keyframe_context_menu_can_render():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.selected.add_keyframe("x", 1000, 0.7)
    editor.selected_keyframes = {(editor.selected.id, "x", 1000)}
    editor.context_menu_pos = (500, 400)

    editor.draw()

    assert editor._context_action_at(editor._context_menu_items()[0][0].center) == "linear"


def test_mouse_keyframe_click_does_not_require_event_mod_attribute():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.selected.add_keyframe("x", 1000, 0.7)
    _, _, timeline = editor.layout()
    key_x = round(editor._time_to_timeline_x(1000, timeline))
    key_y = editor._timeline_track(timeline).y + 15
    click = pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": (key_x, key_y)}
    )

    editor.handle_event(click)

    assert (editor.selected.id, "x", 1000) in editor.selected_keyframes


def test_led_id_can_be_typed_in_calibration_panel():
    editor = make_editor()
    editor.calibration = True
    previous_index = editor._current_led().firmware_index
    previous_owner = next(led for led in editor.led_map.leds if led.firmware_index == 58)
    editor._action("edit_led_id")
    editor._handle_led_id_input(
        pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_5, "unicode": "5"})
    )
    editor._handle_led_id_input(
        pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_8, "unicode": "8"})
    )
    editor._handle_led_id_input(
        pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN, "unicode": "\r"})
    )

    assert editor.selected_led_id == 0
    assert editor._current_led().firmware_index == 58
    assert previous_owner.firmware_index == previous_index
    assert editor.led_id_input == "58"
    assert editor.led_id_editing is False


def test_ctrl_z_event_restores_deleted_keyframe():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.selected.add_keyframe("x", 0, 0.5)
    editor.selected.add_keyframe("x", 1000, 0.7)
    editor.selected_keyframes = {(editor.selected.id, "x", 1000)}
    editor.handle_event(
        pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_DELETE, "mod": 0, "unicode": ""})
    )
    editor.handle_event(
        pygame.event.Event(
            pygame.KEYDOWN, {"key": pygame.K_z, "mod": pygame.KMOD_CTRL, "unicode": "z"}
        )
    )

    assert editor.selected is not None
    assert [frame.time_ms for frame in editor.selected.keyframes["x"]] == [0, 1000]


def test_timeline_layer_selection_and_layer_delete():
    editor = make_editor()
    editor._action("layer")
    editor._create_shape("rectangle", (0.4, 0.4))
    editor.draw()
    _, _, timeline = editor.layout()
    second_row = (timeline.x + 50, editor._timeline_track(timeline).y + 47)
    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": second_row})
    )
    assert editor.active_layer == 1

    editor._action("select_layer:0")
    assert editor.active_layer == 0

    editor._action("select_layer:1")
    editor._action("delete_layer")
    assert len(editor.project.layers) == 1
    assert editor.active_layer == 0


def test_duration_can_be_entered_manually():
    editor = make_editor()
    editor._action("edit_duration")
    for key, character in ((pygame.K_2, "2"), (pygame.K_PERIOD, "."), (pygame.K_3, "3")):
        editor._handle_duration_input(
            pygame.event.Event(pygame.KEYDOWN, {"key": key, "unicode": character})
        )
    editor._handle_duration_input(
        pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN, "unicode": "\r"})
    )
    assert editor.project.duration_ms == 2300
    assert editor.duration_editing is False


def test_led_name_null_is_stored_on_selected_position():
    editor = make_editor()
    editor._action("edit_led_name")
    for character in "NULL":
        editor._handle_led_name_input(
            pygame.event.Event(
                pygame.KEYDOWN, {"key": ord(character.lower()), "unicode": character}
            )
        )
    editor._handle_led_name_input(
        pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN, "unicode": "\r"})
    )
    assert editor._current_led().name == "NULL"
    assert editor.led_map.export_slots()[editor._current_led().firmware_index] is None


def test_filled_shape_draws_color_not_only_selection_frame():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    canvas, _, _ = editor.layout()
    editor.screen.fill((20, 22, 28))
    editor._draw_shapes(canvas)
    center = editor._world_to_screen((0.5, 0.5), canvas)
    pixel = editor.screen.get_at(center)
    assert pixel.r > pixel.b
    assert pixel != pygame.Color(20, 22, 28, 255)


def test_led_can_be_moved_added_and_deleted_in_calibration():
    editor = make_editor()
    editor.calibration = True
    canvas, _, _ = editor.layout()
    original_count = len(editor.led_map.leds)
    editor._move_selected_led(canvas.center, canvas)
    moved = editor._current_led()
    assert moved.x == round(editor.led_map.width / 2)
    assert moved.y == round(editor.led_map.height / 2)

    editor._action("add_led")
    assert len(editor.led_map.leds) == original_count + 1
    assert editor._current_led().firmware_index == original_count

    editor._action("delete_led")
    assert len(editor.led_map.leds) == original_count
    assert editor.led_map.validate() == []


def test_imported_effect_drives_led_preview_and_duration(tmp_path):
    editor = make_editor()
    values = [0] * EFFECT_LEDS
    first = editor._current_led().firmware_index
    values[first] = 1
    path = tmp_path / "effect_data.h"
    path.write_text(
        "const uint8_t EffectID1[] PROGMEM = {" + ",".join(map(str, values)) + "};",
        encoding="utf-8",
    )

    editor.load_effect_data_file(path)

    assert editor._active_imported_effect().name == "EffectID1"
    assert editor._playback_duration() == 60
    assert editor._preview_led_colors()[0] == (255, 255, 255)
    assert editor.stencil is True


def test_stencil_glows_mix_overlapping_led_colors():
    editor = make_editor()
    canvas, _, _ = editor.layout()
    for led in editor.led_map.leds[:2]:
        led.x = editor.led_map.width / 2
        led.y = editor.led_map.height / 2
        led.name = "test"
    editor.led_points = editor.led_map.normalized_points()
    frame = [(0, 0, 0)] * EFFECT_LEDS
    frame[editor.led_map.leds[0].firmware_index] = (255, 0, 0)
    frame[editor.led_map.leds[1].firmware_index] = (0, 0, 255)
    editor.imported_effects = [ImportedEffect("mix", [frame])]
    editor.active_import_index = 0
    editor.screen.fill((0, 0, 0))

    editor._draw_leds(canvas, stencil_back=True)

    pixel = editor.screen.get_at(canvas.center)
    assert pixel.r > 100
    assert pixel.b > 100
    assert pixel.g == 0
