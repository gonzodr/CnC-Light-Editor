import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from cnc_light_editor.app import Editor, _parse_resolution
from cnc_light_editor.effect_importer import EFFECT_LEDS, ImportedEffect, parse_effect_data
from cnc_light_editor.exporter import (
    export_effect_bank,
    project_to_imported_effect,
    sample_project_frames,
)
from cnc_light_editor.model import Layer, Project, Shape


def make_editor() -> Editor:
    pygame.init()
    screen = pygame.display.get_surface()
    if screen is None or screen.get_size() != (1280, 900):
        screen = pygame.display.set_mode((1280, 900))
    editor = Editor(screen, check_recovery=False)
    editor.project = Project("interaction test")
    editor.selected = None
    editor.undo_stack.clear()
    editor.redo_stack.clear()
    return editor


def test_native_1280x1024_workspace_and_resolution_parser():
    assert _parse_resolution("1280x1024") == (1280, 1024)
    pygame.init()
    screen = pygame.display.set_mode((1280, 1024), pygame.RESIZABLE)
    editor = Editor(screen, check_recovery=False)
    editor.project = Project(layers=[Layer(f"Layer {index + 1}") for index in range(8)])

    canvas, panel, timeline = editor.layout()
    assert editor.screen.get_size() == (1280, 1024)
    assert canvas.top >= 54
    assert timeline.bottom == 1024
    assert panel.bottom == 1024
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert "rename_layer:7" in actions


def test_timeline_resize_drag_clamps_resets_and_persists(tmp_path):
    pygame.init()
    screen = pygame.display.set_mode((1280, 1024), pygame.RESIZABLE)
    settings_path = tmp_path / "editor_settings.json"
    editor = Editor(screen, settings_path=settings_path, check_recovery=False)
    editor.draw()
    _canvas, _panel, timeline = editor.layout()
    handle = next(rect for rect, action, _label in editor.buttons if action == "timeline_resize_handle")

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, button=1, pos=handle.center, clicks=1,
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEMOTION, pos=(handle.centerx, 680), rel=(0, -120), buttons=(1, 0, 0),
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, button=1, pos=(handle.centerx, 680),
    ))

    assert editor.timeline_height == 344
    restored = Editor(screen, settings_path=settings_path, check_recovery=False)
    assert restored.timeline_height == 344

    restored.draw()
    reset_handle = next(
        rect for rect, action, _label in restored.buttons if action == "timeline_resize_handle"
    )
    restored.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, button=1, pos=reset_handle.center, clicks=2,
    ))
    assert restored.timeline_height == 218

    restored.timeline_height = 9999
    assert restored.layout()[2].height == 520


def test_canvas_layer_is_unique_undoable_and_owns_a_timeline_channel():
    editor = make_editor()

    editor._action("canvas_layer")

    canvas_layer = editor.project.layers[-1]
    assert canvas_layer.is_canvas is True
    assert editor.project.overlay is True
    assert editor.active_layer == len(editor.project.layers) - 1
    assert canvas_layer.value_at("canvas_enabled", 0) is True
    assert (canvas_layer.id, "canvas_enabled", 0) in editor.selected_keyframes
    assert any(
        row.kind == "property"
        and row.target_id == canvas_layer.id
        and row.prop == "canvas_enabled"
        for row in editor._timeline_rows()
    )

    editor._action("canvas_layer")
    assert sum(layer.is_canvas for layer in editor.project.layers) == 1

    editor._undo()
    assert not any(layer.is_canvas for layer in editor.project.layers)


def test_canvas_timeline_toggle_creates_stepped_on_off_keyframe():
    editor = make_editor()
    editor._action("canvas_layer")
    canvas_layer = editor.project.layers[-1]
    editor.current_ms = 500

    editor._action(f"toggle_layer:{editor.active_layer}")

    assert canvas_layer.value_at("canvas_enabled", 499) is True
    assert canvas_layer.value_at("canvas_enabled", 500) is False
    assert editor.selected_keyframes == {(canvas_layer.id, "canvas_enabled", 500)}
    assert editor.project.canvas_transparency_at(499) is True
    assert editor.project.canvas_transparency_at(500) is False


def test_canvas_inspector_writes_explicit_enable_and_disable_keyframes():
    editor = make_editor()
    editor._action("canvas_layer")
    canvas_layer = editor.project.layers[-1]
    editor.current_ms = 500

    editor._action("canvas_state:0")
    assert canvas_layer.value_at("canvas_enabled", 500) is False
    assert editor.selected_keyframes == {(canvas_layer.id, "canvas_enabled", 500)}

    editor.current_ms = 1000
    editor._action("canvas_state:1")
    assert canvas_layer.value_at("canvas_enabled", 1000) is True

    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert {"canvas_state:0", "canvas_state:1"}.issubset(actions)


def test_shapes_and_random_led_effects_cannot_be_added_to_canvas_layer():
    editor = make_editor()
    editor._action("canvas_layer")
    canvas_layer = editor.project.layers[-1]

    editor._create_shape("ellipse", (0.5, 0.5))
    editor._action("random_led_editor")

    assert canvas_layer.shapes == []
    assert canvas_layer.effects == []


def test_layer_can_be_renamed_from_inspector_and_undone():
    editor = make_editor()
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert "rename_layer:0" in actions

    editor._action("rename_layer:0")
    assert editor.layer_name_editing_id == editor.project.layers[0].id
    editor.layer_name_input = "Main sparkle pass"
    editor._handle_layer_name_input(pygame.event.Event(
        pygame.KEYDOWN,
        {"key": pygame.K_RETURN, "mod": 0, "unicode": "\r"},
    ))

    assert editor.project.layers[0].name == "Main sparkle pass"
    assert editor.layer_name_editing_id is None
    editor._undo()
    assert editor.project.layers[0].name == "Layer 1"
    editor._redo()
    assert editor.project.layers[0].name == "Main sparkle pass"


def test_f2_starts_layer_rename_and_escape_keeps_original_name():
    editor = make_editor()

    editor._handle_key(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_F2, "mod": 0, "unicode": ""},
    ))
    editor.layer_name_input = "Discarded name"
    editor._handle_layer_name_input(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_ESCAPE, "mod": 0, "unicode": ""},
    ))

    assert editor.layer_name_editing_id is None
    assert editor.project.layers[0].name == "Layer 1"


def test_imported_overlay_sentinel_is_transparent_in_led_preview():
    editor = make_editor()
    editor.imported_effects = [ImportedEffect(
        "overlay",
        [[(255, 0, 255)] * EFFECT_LEDS],
        overlay=True,
    )]
    editor.active_import_index = 0

    assert set(editor._preview_led_colors()) == {(0, 0, 0)}


def test_help_button_and_f1_open_a_modal_that_escape_closes():
    editor = make_editor()
    editor.draw()

    assert "help" in {action for _rect, action, _label in editor.buttons}

    editor.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_F1, "mod": 0, "unicode": ""},
    ))
    assert editor.help_open is True

    editor.draw()
    assert "help_close" in {action for _rect, action, _label in editor.buttons}

    editor.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_F1, "mod": 0, "unicode": ""},
    ))
    assert editor.help_open is False

    editor._action("help")
    editor.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_ESCAPE, "mod": 0, "unicode": ""},
    ))
    assert editor.help_open is False


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
    frame_ms = 1000 / editor.project.fps
    assert abs(moved / frame_ms - round(moved / frame_ms)) < 0.02


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


def test_deleting_first_keyframe_promotes_first_remaining_value_to_shape_base():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.selected.width = 0.2
    editor.selected.add_keyframe("width", 0, 0.1)
    editor.selected.add_keyframe("width", 1000, 0.8)
    editor.selected_keyframes = {(editor.selected.id, "width", 0)}

    editor._delete_selected_keyframes()

    assert [frame.time_ms for frame in editor.selected.keyframes["width"]] == [1000]
    assert editor.selected.width == 0.8
    assert editor.selected.state_at(0)["width"] == 0.8
    editor._undo()
    assert editor.selected.width == 0.2
    assert editor.selected.state_at(0)["width"] == 0.1


def test_ctrl_c_v_pastes_selected_keyframe_at_playhead_and_preserves_easing():
    editor = make_editor()
    editor._create_shape("rectangle", (0.5, 0.5))
    shape = editor.selected
    shape.add_keyframe("x", 1000, 0.4)
    shape.add_keyframe("x", 2500, 0.9)
    source = shape.keyframes["x"][0]
    source.easing = "bezier"
    source.bezier = (0.2, -0.3, 0.8, 1.4)
    editor.selected_keyframes = {(shape.id, "x", 1000)}

    editor.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_c, "mod": pygame.KMOD_CTRL, "unicode": "c"},
    ))
    editor.current_ms = 2500
    editor.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_v, "mod": pygame.KMOD_CTRL, "unicode": "v"},
    ))

    pasted = next(frame for frame in shape.keyframes["x"] if frame.time_ms == 2500)
    assert pasted.value == 0.4
    assert pasted.easing == "bezier"
    assert pasted.bezier == (0.2, -0.3, 0.8, 1.4)
    assert editor.selected_keyframes == {(shape.id, "x", 2500)}
    editor._undo()
    restored = editor._find_keyframe_target(shape.id)
    assert next(frame for frame in restored.keyframes["x"] if frame.time_ms == 2500).value == 0.9


def test_keyframe_clipboard_preserves_multi_target_offsets_and_extends_timeline():
    editor = make_editor()
    editor._create_shape("ellipse", (0.4, 0.4))
    first = editor.selected
    first.add_keyframe("x", 1000, 0.3)
    editor._action("layer")
    editor._create_shape("rectangle", (0.6, 0.6))
    second = editor.selected
    second.add_keyframe("opacity", 1600, 0.25)
    editor.selected_keyframes = {
        (first.id, "x", 1000), (second.id, "opacity", 1600),
    }

    editor._copy_selected_keyframes()
    editor.current_ms = 4800
    editor._paste_keyframes_at_playhead()

    assert (first.id, "x", 4800) in editor.selected_keyframes
    assert (second.id, "opacity", 5400) in editor.selected_keyframes
    assert next(frame for frame in first.keyframes["x"] if frame.time_ms == 4800).value == 0.3
    assert next(frame for frame in second.keyframes["opacity"] if frame.time_ms == 5400).value == 0.25
    assert editor.project.duration_ms == 5400


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
    assert editor.timeline_selected_layer_id == editor.project.layers[1].id
    assert editor.selected is None
    editor.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_DELETE, "mod": 0, "unicode": ""},
    ))
    assert len(editor.project.layers) == 1
    assert editor.active_layer == 0
    assert editor.timeline_selected_layer_id is None


def test_timeline_layer_rows_scroll_and_keep_absolute_actions():
    editor = make_editor()
    editor.project.layers = [Layer(f"Layer {index}") for index in range(8)]
    editor.project.layers[7].shapes.append(Shape("ellipse", "Last shape"))
    _, _, timeline = editor.layout()
    capacity = editor._timeline_layer_capacity(timeline)

    editor.draw()
    visible_selects = {
        action for _rect, action, _label in editor.buttons if action.startswith("select_layer:")
    }
    assert visible_selects == {f"select_layer:{index}" for index in range(capacity)}

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEWHEEL,
        {"y": -2, "x": 0, "pos": (timeline.x + 60, editor._timeline_track(timeline).y + 10)},
    ))
    editor.draw()
    visible_selects = {
        action for _rect, action, _label in editor.buttons if action.startswith("select_layer:")
    }
    assert editor.timeline_layer_scroll == 2
    assert visible_selects == {f"select_layer:{index}" for index in range(2, 2 + capacity)}

    editor._action("select_layer:7")
    assert editor.timeline_layer_scroll == 8 - capacity
    assert editor._selected_timeline_row_y(timeline) == editor._timeline_track(timeline).y + 15 + (capacity - 1) * 32


def test_timeline_on_off_button_toggles_layer_without_selecting_another_row():
    editor = make_editor()
    editor.project.layers = [Layer("First"), Layer("Second")]
    editor.active_layer = 0
    editor.draw()
    toggle = next(
        rect for rect, action, _label in editor.buttons if action == "toggle_layer:1"
    )

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": toggle.center},
    ))

    assert editor.project.layers[1].visible is False
    assert editor.active_layer == 0
    assert "OFF" in editor.status


def test_timeline_layer_expands_into_individual_property_rows():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.selected.add_keyframe("x", 1000, 0.7)
    editor.selected.add_keyframe("opacity", 1500, 0.3)

    editor._action("toggle_timeline_layer:0")
    rows = editor._timeline_rows()

    assert [(row.kind, row.prop) for row in rows] == [
        ("layer", None), ("property", "x"), ("property", "opacity"),
    ]
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert "toggle_timeline_layer:0" in actions
    assert f"select_timeline_channel:{editor.selected.id}:x" in actions


def test_expanded_property_row_selects_only_its_own_keyframe():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.selected.add_keyframe("x", 1000, 0.7)
    editor.selected.add_keyframe("opacity", 1000, 0.3)
    editor._action("toggle_timeline_layer:0")
    _, _, timeline = editor.layout()
    x_row_y = next(
        row_y for row, row_y in editor._timeline_visible_rows(timeline)
        if row.prop == "x"
    )
    key_x = round(editor._time_to_timeline_x(1000, timeline))

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": (key_x, x_row_y)},
    ))

    assert editor.selected_keyframes == {(editor.selected.id, "x", 1000)}


def test_marquee_and_drag_offset_keyframes_across_property_rows_and_targets():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    first = editor.selected
    first.add_keyframe("x", 1000, 0.7)
    editor._create_shape("rectangle", (0.4, 0.4))
    second = editor.selected
    second.add_keyframe("opacity", 2000, 0.2)
    editor._action("toggle_timeline_layer:0")
    _, _, timeline = editor.layout()
    visible = editor._timeline_visible_rows(timeline)
    x_row_y = next(row_y for row, row_y in visible if row.target_id == first.id)
    opacity_row_y = next(row_y for row, row_y in visible if row.target_id == second.id)
    first_x = round(editor._time_to_timeline_x(1000, timeline))
    second_x = round(editor._time_to_timeline_x(2000, timeline))

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN,
        {"button": 3, "pos": (first_x - 10, x_row_y - 10)},
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEMOTION,
        {
            "pos": (second_x + 10, opacity_row_y + 10),
            "rel": (0, 0), "buttons": (0, 0, 1),
        },
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP,
        {"button": 3, "pos": (second_x + 10, opacity_row_y + 10)},
    ))
    assert editor.selected_keyframes == {
        (first.id, "x", 1000), (second.id, "opacity", 2000),
    }

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": (first_x, x_row_y)},
    ))
    target_x = round(editor._time_to_timeline_x(1500, timeline))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEMOTION,
        {"pos": (target_x, x_row_y), "rel": (0, 0), "buttons": (1, 0, 0)},
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, {"button": 1, "pos": (target_x, x_row_y)},
    ))

    assert [frame.time_ms for frame in first.keyframes["x"]] == [1500]
    assert [frame.time_ms for frame in second.keyframes["opacity"]] == [2500]

    editor._undo()
    restored_first = editor._find_keyframe_target(first.id)
    restored_second = editor._find_keyframe_target(second.id)
    assert [frame.time_ms for frame in restored_first.keyframes["x"]] == [1000]
    assert [frame.time_ms for frame in restored_second.keyframes["opacity"]] == [2000]


def test_graph_editor_chooses_active_numeric_channel_and_toggles_back():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.selected.add_keyframe("x", 0, 0.2)
    editor.selected.add_keyframe("x", 1000, 0.8)

    editor._action("toggle_timeline_mode")

    assert editor.timeline_mode == "graph"
    assert editor.graph_target_id == editor.selected.id
    assert editor.graph_prop == "x"
    editor.draw()
    assert "toggle_timeline_mode" in {
        action for _rect, action, _label in editor.buttons
    }

    editor._action("toggle_timeline_mode")
    assert editor.timeline_mode == "dope"


def test_graph_keyframe_drag_changes_time_and_value_and_undoes_together():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    shape = editor.selected
    shape.add_keyframe("x", 1000, 0.25)
    shape.add_keyframe("x", 2000, 0.75)
    editor._action("toggle_timeline_mode")
    _, _, timeline = editor.layout()
    start = editor._graph_point(1000, 0.25, timeline)
    target_x = round(editor._time_to_timeline_x(1500, timeline))
    target_y = start[1] - round(editor._graph_area(timeline).height * 0.25)

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": start},
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEMOTION,
        {"pos": (target_x, target_y), "rel": (0, 0), "buttons": (1, 0, 0)},
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, {"button": 1, "pos": (target_x, target_y)},
    ))

    moved = shape.keyframes["x"][0]
    assert moved.time_ms == 1500
    assert abs(moved.value - 0.5) < 0.01
    assert editor.selected_keyframes == {(shape.id, "x", 1500)}

    editor._undo()
    restored = editor._find_keyframe_target(shape.id)
    assert [(frame.time_ms, frame.value) for frame in restored.keyframes["x"]] == [
        (1000, 0.25), (2000, 0.75),
    ]


def test_graph_keyframe_right_click_opens_easing_menu():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    shape = editor.selected
    shape.add_keyframe("opacity", 1000, 0.4)
    editor.graph_target_id = shape.id
    editor.graph_prop = "opacity"
    editor.timeline_mode = "graph"
    _, _, timeline = editor.layout()
    point = editor._graph_point(1000, 0.4, timeline)

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"button": 3, "pos": point},
    ))

    assert editor.selected_keyframes == {(shape.id, "opacity", 1000)}
    assert editor.context_menu_pos == point


def test_context_menu_converts_selected_keyframe_to_custom_bezier_and_undoes():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    shape = editor.selected
    shape.add_keyframe("x", 0, 0.2)
    shape.add_keyframe("x", 1000, 0.8)
    editor.selected_keyframes = {(shape.id, "x", 1000)}

    editor._apply_keyframe_context("custom_bezier")

    destination = shape.keyframes["x"][1]
    assert destination.easing == "bezier"
    assert destination.bezier == (0.25, 0.10, 0.25, 1.0)
    editor._undo()
    restored = editor._find_keyframe_target(shape.id)
    assert restored.keyframes["x"][1].easing == "linear"
    assert restored.keyframes["x"][1].bezier is None


def test_graph_editor_bezier_handle_drag_updates_controls_and_undoes():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    shape = editor.selected
    shape.add_keyframe("x", 0, 0.0)
    shape.add_keyframe("x", 1000, 1.0)
    editor.selected_keyframes = {(shape.id, "x", 1000)}
    editor._apply_keyframe_context("custom_bezier")
    editor.graph_target_id = shape.id
    editor.graph_prop = "x"
    editor.timeline_mode = "graph"
    _, _, timeline = editor.layout()
    handles = editor._bezier_handle_points(timeline)
    start = handles[1]
    target_x = round(editor._time_to_timeline_x(400, timeline))
    target_y = editor._graph_area(timeline).top

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": start},
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEMOTION,
        {"pos": (target_x, target_y), "rel": (0, 0), "buttons": (1, 0, 0)},
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, {"button": 1, "pos": (target_x, target_y)},
    ))

    destination = shape.keyframes["x"][1]
    assert abs(destination.bezier[0] - 0.4) < 0.02
    assert abs(destination.bezier[1] - 1.0) < 0.02
    editor._undo()
    restored = editor._find_keyframe_target(shape.id)
    assert restored.keyframes["x"][1].bezier == (0.25, 0.10, 0.25, 1.0)


def test_canvas_mode_is_an_undoable_export_setting():
    editor = make_editor()
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}

    assert "toggle_overlay" in actions
    assert editor.project.overlay is False

    editor._action("toggle_overlay")
    assert editor.project.overlay is True
    assert "CANVAS" in editor.status

    editor._undo()
    assert editor.project.overlay is False


def test_ctrl_d_duplicates_active_layer_with_unique_ids_and_undo():
    editor = make_editor()
    editor._create_shape("rectangle", (0.4, 0.4))
    source_layer = editor.project.layers[0]
    source_shape = source_layer.shapes[0]
    source_shape.add_keyframe("x", 1000, 0.7)

    editor.handle_event(
        pygame.event.Event(
            pygame.KEYDOWN,
            {"key": pygame.K_d, "mod": pygame.KMOD_CTRL, "unicode": "d"},
        )
    )

    assert len(editor.project.layers) == 2
    clone_layer = editor.project.layers[1]
    assert editor.active_layer == 1
    assert clone_layer.name == "Layer 1 copy"
    assert clone_layer.id != source_layer.id
    assert clone_layer.shapes[0].id != source_shape.id
    assert clone_layer.shapes[0].keyframes["x"][0].value == 0.7
    assert editor.selected is clone_layer.shapes[0]

    editor._undo()
    assert len(editor.project.layers) == 1


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


def test_transform_field_can_be_clicked_typed_and_undone():
    editor = make_editor()
    editor._create_shape("rectangle", (0.5, 0.5))
    editor.draw()
    field = next(rect for rect, action, _label in editor.buttons if action == "scrub:x")
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": field.center},
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, {"button": 1, "pos": field.center},
    ))

    assert editor.property_editing == "x"
    for character in "0.25":
        editor.handle_event(pygame.event.Event(
            pygame.KEYDOWN,
            {"key": ord(character) if character != "." else pygame.K_PERIOD, "unicode": character, "mod": 0},
        ))
    editor.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_RETURN, "unicode": "\r", "mod": 0},
    ))

    assert editor.selected.x == 0.25
    assert editor.property_editing is None
    editor._undo()
    assert editor.selected.x == 0.5


def test_transform_field_accepts_numeric_clipboard_and_normalizes_rotation():
    editor = make_editor()
    editor._create_shape("rectangle", (0.5, 0.5))
    editor.property_editing = "rotation"
    editor.property_input = "0.0"
    editor.property_input_select_all = True
    editor._clipboard_text = lambda: "450"

    editor._handle_property_input(pygame.event.Event(
        pygame.KEYDOWN,
        {"key": pygame.K_v, "unicode": "v", "mod": pygame.KMOD_CTRL},
    ))
    editor._handle_property_input(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_RETURN, "unicode": "\r", "mod": 0},
    ))

    assert editor.selected.rotation == 90.0
    assert editor.selected.rotation_turns == 1.0


def test_transform_field_records_two_complete_rotation_turns_and_undoes():
    editor = make_editor()
    editor._create_shape("rectangle", (0.5, 0.5))
    editor.property_editing = "rotation"
    editor.property_input = "720"
    editor.property_input_select_all = False

    editor._handle_property_input(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_RETURN, "unicode": "\r", "mod": 0},
    ))

    assert editor.selected.rotation == 0.0
    assert editor.selected.rotation_turns == 2.0
    assert editor.selected.state_at(0)["rotation_total"] == 720.0
    editor._undo()
    restored = editor.project.layers[0].shapes[0]
    assert restored.rotation == 0.0
    assert restored.rotation_turns == 0.0


def test_inspector_exposes_rotation_turns_field():
    editor = make_editor()
    editor._create_shape("rectangle", (0.5, 0.5))

    editor.draw()

    assert any(action == "scrub:rotation_turns" for _rect, action, _label in editor.buttons)


def test_inspector_exposes_keyframeable_mask_fields_and_accepts_percent_input():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert {"scrub:feather", "scrub:mask_expansion"} <= actions

    editor.property_editing = "feather"
    editor.property_input = "5"
    editor.property_input_select_all = False
    editor._handle_property_input(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_RETURN, "unicode": "\r", "mod": 0},
    ))

    assert editor.selected.feather == 0.05


def test_graph_range_keeps_negative_mask_expansion_visible():
    shape = Shape("rectangle", "contracting mask", mask_expansion=-0.04)
    shape.add_keyframe("mask_expansion", 1000, 0.04)

    low, high = Editor._graph_value_range(shape, "mask_expansion")

    assert low < 0.0 < high


def test_transform_field_rejects_out_of_range_opacity():
    editor = make_editor()
    editor._create_shape("rectangle", (0.5, 0.5))
    editor.property_editing = "opacity"
    editor.property_input = "1.5"
    editor.property_input_select_all = False

    editor._handle_property_input(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_RETURN, "unicode": "\r", "mod": 0},
    ))

    assert editor.selected.opacity == 1.0
    assert editor.property_editing == "opacity"
    assert "must be 0–1" in editor.status


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


def test_led_name_accepts_ctrl_v_from_clipboard():
    editor = make_editor()
    editor._action("edit_led_name")
    editor._clipboard_text = lambda: "Left sling\r\nwindow"

    editor._handle_led_name_input(
        pygame.event.Event(
            pygame.KEYDOWN,
            {"key": pygame.K_v, "mod": pygame.KMOD_CTRL, "unicode": "v"},
        )
    )

    assert editor.led_name_input == "Left sling window"
    assert "press Enter" in editor.status


def test_timeline_switches_to_frame_ruler_when_zoomed_in():
    editor = make_editor()
    _, _, timeline = editor.layout()
    editor.timeline_zoom = 1.0
    _, end = editor._timeline_window()
    assert editor._timeline_uses_frame_ruler(timeline, end) is False

    editor.timeline_zoom = 20.0
    start, end = editor._timeline_window()
    assert editor._timeline_uses_frame_ruler(timeline, end - start) is True


def test_playhead_and_keyframe_snap_to_frames_in_frame_ruler_mode():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.timeline_zoom = 20.0
    editor.timeline_scroll_ms = 1900
    editor.snap = False
    _, _, timeline = editor.layout()
    target_x = round(editor._time_to_timeline_x(2017, timeline))

    editor._set_playhead(target_x, timeline)
    frame_ms = 1000 / editor.project.fps
    assert abs(editor.current_ms / frame_ms - round(editor.current_ms / frame_ms)) < 0.02

    editor.selected.add_keyframe("x", 1900, 0.5)
    editor.selected_keyframes = {(editor.selected.id, "x", 1900)}
    editor.drag_key_time = 1900
    editor._move_keyframe(target_x, timeline)
    moved = editor.selected.keyframes["x"][0].time_ms
    assert abs(moved / frame_ms - round(moved / frame_ms)) < 0.02


def test_right_drag_selects_multiple_keyframes_and_offsets_them_together():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.selected.add_keyframe("x", 1000, 0.4)
    editor.selected.add_keyframe("x", 2000, 0.6)
    _, _, timeline = editor.layout()
    row_y = editor._selected_timeline_row_y(timeline)
    first_x = round(editor._time_to_timeline_x(1000, timeline))
    second_x = round(editor._time_to_timeline_x(2000, timeline))
    start = (first_x - 12, row_y - 10)
    end = (second_x + 12, row_y + 10)

    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 3, "pos": start})
    )
    editor.handle_event(
        pygame.event.Event(
            pygame.MOUSEMOTION,
            {"pos": end, "rel": (0, 0), "buttons": (0, 0, 1)},
        )
    )
    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, {"button": 3, "pos": end})
    )
    assert {time for _shape, _prop, time in editor.selected_keyframes} == {1000, 2000}

    editor.handle_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            {"button": 1, "pos": (first_x, row_y)},
        )
    )
    target_x = round(editor._time_to_timeline_x(1500, timeline))
    editor.handle_event(
        pygame.event.Event(
            pygame.MOUSEMOTION,
            {"pos": (target_x, row_y), "rel": (0, 0), "buttons": (1, 0, 0)},
        )
    )
    editor.handle_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONUP,
            {"button": 1, "pos": (target_x, row_y)},
        )
    )

    moved_times = [frame.time_ms for frame in editor.selected.keyframes["x"]]
    assert moved_times == [1500, 2500]
    assert moved_times[1] - moved_times[0] == 1000


def test_color_palette_applies_to_every_selected_keyframe_time():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.selected.add_keyframe("x", 1000, 0.4)
    editor.selected.add_keyframe("x", 2000, 0.6)
    editor.selected_keyframes = {
        (editor.selected.id, "x", 1000),
        (editor.selected.id, "x", 2000),
    }

    editor._action("color:4")

    color_frames = editor.selected.keyframes["color"]
    assert [frame.time_ms for frame in color_frames] == [1000, 2000]
    assert [frame.value for frame in color_frames] == [(30, 180, 255), (30, 180, 255)]
    assert (editor.selected.id, "color", 1000) in editor.selected_keyframes
    assert (editor.selected.id, "color", 2000) in editor.selected_keyframes


def test_color_palette_applies_across_selected_shape_targets():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    first = editor.selected
    first.add_keyframe("x", 1000, 0.4)
    editor._create_shape("rectangle", (0.4, 0.4))
    second = editor.selected
    second.add_keyframe("opacity", 2000, 0.4)
    editor.selected_keyframes = {
        (first.id, "x", 1000), (second.id, "opacity", 2000),
    }

    editor._action("color:4")

    assert first.keyframes["color"][0].value == (30, 180, 255)
    assert first.keyframes["color"][0].time_ms == 1000
    assert second.keyframes["color"][0].value == (30, 180, 255)
    assert second.keyframes["color"][0].time_ms == 2000


def test_project_save_load_round_trip_and_dirty_state(tmp_path):
    editor = make_editor()
    editor.project.name = "Round trip"
    editor._create_shape("rectangle", (0.3, 0.4))
    editor.selected.color = (12, 34, 56)
    path_without_suffix = tmp_path / "my-effect"

    editor.save_project_file(path_without_suffix)
    saved_path = path_without_suffix.with_suffix(".cnclight")
    assert saved_path.exists()
    assert editor.project_path == saved_path.resolve()
    assert editor._project_is_dirty() is False

    editor.selected.name = "Unsaved mutation"
    assert editor._project_is_dirty() is True
    editor.load_project_file(saved_path)

    assert editor.project.name == "Round trip"
    assert editor.project.layers[0].shapes[0].name == "Rectangle 1"
    assert editor.project.layers[0].shapes[0].color == (12, 34, 56)
    assert editor._project_is_dirty() is False


def test_new_button_starts_a_blank_unsaved_project_without_dialog():
    editor = make_editor()
    editor._create_shape("rectangle", (0.3, 0.4))
    editor.draw()

    assert "new" in {action for _rect, action, _label in editor.buttons}
    assert editor._new_project(confirm=False) is True
    assert editor.project.name == "Untitled effect"
    assert len(editor.project.layers) == 1
    assert editor.project.layers[0].shapes == []
    assert editor.project_path is None
    assert editor._project_is_dirty() is True
    assert editor.undo_stack == []


def test_autosave_recovery_restores_unsaved_project_and_manual_save_clears_it(tmp_path):
    pygame.init()
    screen = pygame.display.set_mode((1280, 900))
    source_project = Project("Recovered sparkle", layers=[Layer("Recovered layer")])
    autosave_dir = tmp_path / "autosave"
    source_path = tmp_path / "recovered.cnclight"

    writer = Editor(screen, autosave_directory=autosave_dir, check_recovery=False)
    writer.project = source_project
    writer.project_path = source_path
    assert writer._maybe_autosave(force=True, now_ms=30_000) is True

    editor = Editor(screen, autosave_directory=autosave_dir, check_recovery=True)
    assert editor.recovery_open is True
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert {"recovery_restore", "recovery_discard"} <= actions
    assert editor._recover_autosave() is True
    assert editor.project.name == "Recovered sparkle"
    assert editor.project.layers[0].name == "Recovered layer"
    assert editor.project_path == source_path.resolve()
    assert editor._project_is_dirty() is True

    editor.save_project_file(source_path)
    assert editor.autosave_manager.latest() is None
    assert editor._project_is_dirty() is False


def test_ctrl_n_starts_a_new_project():
    editor = make_editor()
    editor.saved_project_state = editor.project.to_dict()

    editor._handle_key(pygame.event.Event(
        pygame.KEYDOWN,
        {"key": pygame.K_n, "mod": pygame.KMOD_CTRL, "unicode": "n"},
    ))

    assert editor.project.name == "Untitled effect"
    assert editor.project_path is None


def test_stroke_width_accepts_manual_values_up_to_five_percent():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.stroke_editing = True
    editor.stroke_input = "5"

    editor._handle_stroke_input(
        pygame.event.Event(
            pygame.KEYDOWN,
            {"key": pygame.K_RETURN, "mod": 0, "unicode": "\r"},
        )
    )

    assert editor.selected.state_at(editor.current_ms)["stroke_width"] == 0.05
    assert editor.stroke_editing is False


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
    if original_count >= 68:
        editor._delete_led()
        original_count -= 1
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


def test_led_map_uses_select_then_move_and_escape_restores_position():
    editor = make_editor()
    editor.calibration = True
    editor.led_move_ready_id = None
    canvas, _, _ = editor.layout()
    led = editor._current_led()
    original = (led.x, led.y)
    marker = editor._world_to_screen(editor.led_points[0], canvas)
    click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": marker})
    release = pygame.event.Event(pygame.MOUSEBUTTONUP, {"button": 1, "pos": marker})

    editor.handle_event(click)
    editor.handle_event(release)
    assert editor.drag_mode is None
    assert (led.x, led.y) == original

    editor.handle_event(click)
    editor.handle_event(release)
    assert editor.drag_mode == "led_move"

    editor.handle_event(
        pygame.event.Event(
            pygame.MOUSEMOTION,
            {"pos": canvas.center, "rel": (0, 0), "buttons": (0, 0, 0)},
        )
    )
    assert (led.x, led.y) != original

    editor.handle_event(
        pygame.event.Event(
            pygame.KEYDOWN,
            {"key": pygame.K_ESCAPE, "mod": 0, "unicode": ""},
        )
    )
    assert editor.drag_mode is None
    assert (led.x, led.y) == original


def test_led_move_is_confirmed_with_another_playfield_click():
    editor = make_editor()
    editor.calibration = True
    canvas, _, _ = editor.layout()
    led = editor._current_led()
    marker = editor._world_to_screen(editor.led_points[0], canvas)
    editor.led_move_ready_id = led.id

    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": marker})
    )
    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, {"button": 1, "pos": marker})
    )
    target = (canvas.centerx + 25, canvas.centery - 30)
    editor.handle_event(
        pygame.event.Event(
            pygame.MOUSEMOTION,
            {"pos": target, "rel": (0, 0), "buttons": (0, 0, 0)},
        )
    )
    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": target})
    )

    expected_x = round((target[0] - canvas.x) / canvas.width * editor.led_map.width)
    expected_y = round((target[1] - canvas.y) / canvas.height * editor.led_map.height)
    assert editor.drag_mode is None
    assert (led.x, led.y) == (expected_x, expected_y)
    assert "placed" in editor.status


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


def test_gradient_editor_adds_moves_and_recolors_stops():
    editor = make_editor()
    editor._create_shape("rectangle", (0.5, 0.5))

    editor._action("gradient_editor")
    editor._action("gradient_type:linear")
    editor._action("gradient_add_stop")

    assert editor.gradient_editor_open is True
    assert editor.selected.gradient_type == "linear"
    assert len(editor.selected.gradient_stops) == 3
    selected = editor._selected_gradient_stop()
    editor._action("color:4")
    assert selected.color == (30, 180, 255)

    bar = editor._gradient_bar_rect()
    editor._move_gradient_stop(bar.x + round(bar.width * 0.73))
    assert abs(selected.position - 0.73) < 0.01

    angle = editor._gradient_angle_rect()
    editor._set_gradient_angle_from_x(angle.centerx)
    assert editor.selected.gradient_angle == 180.0


def test_gradient_panel_exposes_type_stop_and_angle_controls():
    editor = make_editor()
    editor._create_shape("rectangle", (0.5, 0.5))
    editor._action("gradient_editor")
    editor._action("gradient_type:linear")
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}

    assert {"gradient_type:linear", "gradient_type:radial", "gradient_bar", "gradient_angle"} <= actions


def test_stroke_style_exposes_and_keeps_gradient_editor_open():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor._action("fill_mode:stroke")
    editor.draw()

    actions = {action for _rect, action, _label in editor.buttons}
    assert {"scrub:stroke_width", "gradient_editor"} <= actions

    editor._action("gradient_editor")
    editor._action("gradient_type:linear")
    editor.draw()

    assert editor.gradient_editor_open is True
    assert "Gradient stroke" in editor.status


def test_radial_gradient_panel_switches_between_radius_and_angular_modes():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor._action("gradient_editor")
    editor._action("gradient_type:radial")
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}

    assert {"gradient_radial_mode:radius", "gradient_radial_mode:angular"} <= actions
    assert "gradient_angle" not in actions

    editor._action("gradient_radial_mode:angular")
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert editor.selected.gradient_radial_mode == "angular"
    assert "gradient_angle" in actions

    angle = editor._gradient_angle_rect()
    editor._set_gradient_angle_from_x(angle.centerx)
    assert editor.selected.gradient_angle == 180.0
    assert editor.status.startswith("Angular gradient phase")


def test_random_led_editor_creates_effect_and_keyframes_enabled_state():
    editor = make_editor()

    editor._action("random_led_editor")
    effect = editor._active_random_led_effect()
    editor.current_ms = 500
    editor._action("random_led_toggle")

    assert editor.random_led_editor_open is True
    assert effect is not None
    assert effect.value_at("enabled", 499) is False
    assert effect.value_at("enabled", 500) is False
    assert editor._keyframe_keys_at(500) == {(effect.id, "enabled", 500)}


def test_random_led_panel_exposes_requested_parameters():
    editor = make_editor()
    editor._action("random_led_editor")
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}

    assert {
        "random_led_field:seed", "random_led_field:life_ms",
        "random_led_field:born_speed", "random_led_field:particle_count",
        "random_led_field:opacity", "random_led_toggle", "random_led_keyframe",
        "random_led_opacity_keyframe",
    } <= actions

    editor.current_ms = 350
    editor._action("random_led_opacity_keyframe")
    effect = editor._active_random_led_effect()
    assert editor.selected_keyframes == {(effect.id, "opacity", 350)}
    assert effect.keyframes["opacity"][0].value == 1.0


def test_random_led_parameters_support_mouse_scrubbing_and_typed_input():
    editor = make_editor()
    editor._action("random_led_editor")
    editor.current_ms = 500
    editor.draw()
    opacity_field = next(
        rect for rect, action, _label in editor.buttons
        if action == "random_led_field:opacity"
    )

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": opacity_field.center},
    ))
    dragged = (opacity_field.centerx - 40, opacity_field.centery)
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEMOTION, {"pos": dragged, "rel": (-40, 0), "buttons": (1, 0, 0)},
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, {"button": 1, "pos": dragged},
    ))

    effect = editor._active_random_led_effect()
    assert effect.value_at("opacity", 500) == 0.8
    assert editor.selected_keyframes == {(effect.id, "opacity", 500)}

    editor.draw()
    life_field = next(
        rect for rect, action, _label in editor.buttons
        if action == "random_led_field:life_ms"
    )
    for event_type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        editor.handle_event(pygame.event.Event(
            event_type, {"button": 1, "pos": life_field.center},
        ))
    assert editor.random_effect_editing == "life_ms"
    editor.random_effect_input = "750"
    editor._handle_random_effect_input(pygame.event.Event(
        pygame.KEYDOWN,
        {"key": pygame.K_RETURN, "mod": 0, "unicode": "\r"},
    ))

    assert effect.life_ms == 750
    assert editor.random_effect_editing is None


def test_random_led_preview_matches_firmware_order_export():
    editor = make_editor()
    editor._action("random_led_editor")

    preview = editor._preview_led_colors()
    firmware_frame = sample_project_frames(editor.project, editor.led_map.export_slots())[0]
    firmware_colors = [tuple(firmware_frame[index:index + 3]) for index in range(0, len(firmware_frame), 3)]

    for led, preview_color in zip(editor.led_map.leds, preview):
        assert preview_color == firmware_colors[led.firmware_index]


def test_random_led_keyframe_delete_and_undo_round_trip():
    editor = make_editor()
    editor._action("random_led_editor")
    editor.current_ms = 600
    editor._action("random_led_keyframe")
    effect_id = editor._active_random_led_effect().id

    editor._delete_selected_keyframes()
    assert editor._active_random_led_effect().keyframes == {}

    editor._undo()
    restored = editor._find_keyframe_target(effect_id)
    assert restored.keyframes["enabled"][0].time_ms == 600


def test_inactive_layer_keyframes_remain_available_for_timeline_drawing():
    inactive_shape = Shape("ellipse", "inactive")
    inactive_shape.add_keyframe("opacity", 250, 0.0)
    active_shape = Shape("rectangle", "active")
    active_shape.add_keyframe("x", 500, 0.7)
    editor = make_editor()
    editor.project.layers = [
        Layer("inactive layer", shapes=[inactive_shape]),
        Layer("active layer", shapes=[active_shape]),
    ]
    editor.active_layer = 1
    editor.selected = active_shape

    assert editor._inactive_layer_keyframe_times(editor.project.layers[0], active_shape) == {250}
    editor.draw()


def test_export_toolbar_opens_visual_effect_bank_with_memory_blocks():
    editor = make_editor()
    editor._action("export")
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}

    assert editor.export_bank_open is True
    assert {
        "export_bank_map", "export_bank_write", "export_bank_close",
        "export_bank_select:-1", "export_bank_edit_name", "export_bank_edit_id",
    } <= actions


def test_effect_bank_maps_header_and_edits_imported_name_and_id(tmp_path):
    black = [[(0, 0, 0)] * 68]
    header = export_effect_bank(
        [ImportedEffect("Mapped pulse", black * 2, effect_id=8)],
        tmp_path / "effect_data.h",
    )
    editor = make_editor()
    editor.project.effect_id = 1
    editor.map_effect_bank_file(header)

    assert editor.export_bank_effects[0].name == "Mapped pulse"
    assert editor._export_bank_used_bytes() == editor.project.flash_bytes + 408
    editor.export_bank_selected = 0
    editor._export_bank_action("export_bank_edit_name")
    editor.export_bank_name_input = "Renamed mapped effect"
    assert editor._commit_export_bank_input() is True
    editor._export_bank_action("export_bank_edit_id")
    editor.export_bank_id_input = "9"
    assert editor._commit_export_bank_input() is True

    assert editor.export_bank_effects[0].name == "Renamed mapped effect"
    assert editor.export_bank_effects[0].effect_id == 9


def test_effect_bank_loads_an_embedded_editable_project(tmp_path):
    source_project = Project(
        "Editable pulse", duration_ms=150, frame_ms=50, effect_id=13,
        layers=[Layer("Artwork", shapes=[Shape("rectangle", "Box", color=(12, 34, 56))])],
    )
    header = export_effect_bank(
        [project_to_imported_effect(source_project, [(0.5, 0.5)])],
        tmp_path / "effect_data.h",
    )
    editor = make_editor()
    editor.map_effect_bank_file(header)
    editor.export_bank_selected = 0
    editor.export_bank_open = True

    assert editor.export_bank_effects[0].project_data is not None
    editor._export_bank_action("export_bank_edit_name")
    editor.export_bank_name_input = "Renamed editable pulse"
    assert editor._commit_export_bank_input() is True
    editor._export_bank_action("export_bank_edit_id")
    editor.export_bank_id_input = "14"
    assert editor._commit_export_bank_input() is True
    editor.draw()
    assert "export_bank_load_project" in {
        action for _rect, action, _label in editor.buttons
    }
    assert editor._load_export_bank_project(confirm=False) is True
    assert editor.export_bank_open is False
    assert (editor.project.name, editor.project.effect_id) == ("Renamed editable pulse", 14)
    assert editor.project.layers[0].shapes[0].name == "Box"
    assert editor.project.layers[0].shapes[0].color == (12, 34, 56)
    assert editor.project_path is None
    assert editor._project_is_dirty() is True


def test_effect_bank_rejects_duplicate_id_and_exports_current_project_with_bank(tmp_path):
    black = [[(0, 0, 0)] * 68]
    editor = make_editor()
    editor.project.name = "Current animation"
    editor.project.effect_id = 4
    editor.project.duration_ms = 50
    editor.project.frame_ms = 50
    editor.export_bank_effects = [ImportedEffect("Existing", black, effect_id=7)]
    editor.export_bank_selected = 0
    editor._export_bank_action("export_bank_edit_id")
    editor.export_bank_id_input = "4"

    assert editor._commit_export_bank_input() is False
    assert "already used" in editor.export_bank_status

    path = editor.export_effect_bank_file(tmp_path / "combined.h")
    effects = parse_effect_data(path.read_text(encoding="utf-8"))
    assert [(effect.effect_id, effect.name) for effect in effects] == [
        (7, "Existing"), (4, "Current animation"),
    ]
    assert effects[0].project_data is None
    assert effects[1].project_data is not None


def test_effect_bank_edits_current_export_name_and_id_as_undoable_project_settings():
    editor = make_editor()
    editor.export_bank_selected = -1
    editor._export_bank_action("export_bank_edit_name")
    editor.export_bank_name_input = "Firmware launch"
    assert editor._commit_export_bank_input() is True
    editor._export_bank_action("export_bank_edit_id")
    editor.export_bank_id_input = "12"
    assert editor._commit_export_bank_input() is True

    assert (editor.project.name, editor.project.effect_id) == ("Firmware launch", 12)
    editor._undo()
    assert editor.project.effect_id == 1
