import os
from pathlib import Path
import subprocess
import tempfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
# Without this, pygame.init() probes real audio hardware - normally quick,
# but on a machine with a slow-to-enumerate audio device (e.g. Bluetooth)
# this alone can take several seconds PER TEST. The app's own main() forces
# this same dummy driver for the identical reason (it never plays sound).
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from cnc_light_editor.app import (
    CRASH_LOG_MAX_ENTRIES,
    HELP_COLUMNS,
    ROOT,
    Editor,
    _parse_resolution,
    format_crash_entry,
    get_git_commit,
    log_crash,
)
from cnc_light_editor.effect_importer import EFFECT_LEDS, ImportedEffect, parse_effect_data
from cnc_light_editor.exporter import (
    TRANSPARENT_SENTINEL,
    export_effect_bank,
    project_to_imported_effect,
    sample_project_frames,
)
from cnc_light_editor.model import Layer, Project, Shape
from cnc_light_editor.updater import UpdateEvent


def make_editor() -> Editor:
    pygame.init()
    screen = pygame.display.get_surface()
    if screen is None or screen.get_size() != (1280, 900):
        screen = pygame.display.set_mode((1280, 900))
    # An isolated, disposable settings file per Editor - map_effect_bank_file
    # persists the mapped path here now, and this helper is used by dozens of
    # tests that must never write into the real projects/.editor_settings.json.
    settings_path = Path(tempfile.mkdtemp()) / "editor_settings.json"
    editor = Editor(screen, settings_path=settings_path, check_recovery=False)
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
    assert "exit" in actions


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


def test_loop_bracket_drag_sets_loop_boundary_and_is_undoable():
    editor = make_editor()
    editor.project.duration_ms = 4000
    editor.project.frame_ms = 50
    editor.project.loops = 3
    editor.project.loop_frames = 0
    editor.draw()
    _canvas, _panel, timeline = editor.layout()
    handle = next(rect for rect, action, _label in editor.buttons if action == "loop_bracket_handle")
    target_x = round(editor._time_to_timeline_x(1500, timeline))

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, button=1, pos=handle.center, clicks=1,
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEMOTION, pos=(target_x, handle.centery), rel=(10, 0), buttons=(1, 0, 0),
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, button=1, pos=(target_x, handle.centery),
    ))

    assert editor.project.loop_frames == 30
    assert editor.project.loops == 3  # dragging the boundary never touches the repeat count

    editor._undo()
    assert editor.project.loop_frames == 0


def test_intro_bracket_drag_sets_intro_boundary_and_is_undoable():
    editor = make_editor()
    editor.project.duration_ms = 4000
    editor.project.frame_ms = 50
    editor.project.loop_frames = 60  # leave room in [0, 60) for an intro
    editor.project.intro_frames = 0
    editor.draw()
    _canvas, _panel, timeline = editor.layout()
    handle = next(rect for rect, action, _label in editor.buttons if action == "intro_bracket_handle")
    target_x = round(editor._time_to_timeline_x(500, timeline))

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, button=1, pos=handle.center, clicks=1,
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEMOTION, pos=(target_x, handle.centery), rel=(10, 0), buttons=(1, 0, 0),
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, button=1, pos=(target_x, handle.centery),
    ))

    assert editor.project.intro_frames == 10  # 500ms / 50ms frame

    editor._undo()
    assert editor.project.intro_frames == 0


def test_intro_bracket_cannot_be_dragged_past_the_loop_end():
    editor = make_editor()
    editor.project.duration_ms = 4000
    editor.project.frame_ms = 50
    editor.project.loop_frames = 20
    editor.draw()
    _canvas, _panel, timeline = editor.layout()
    handle = next(rect for rect, action, _label in editor.buttons if action == "intro_bracket_handle")
    # Try to drag it far past the loop end (60 frames == 3000ms).
    target_x = round(editor._time_to_timeline_x(3000, timeline))

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, button=1, pos=handle.center, clicks=1,
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEMOTION, pos=(target_x, handle.centery), rel=(10, 0), buttons=(1, 0, 0),
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, button=1, pos=(target_x, handle.centery),
    ))

    assert editor.project.normalized_intro_frames == 20  # clamped to the loop end, not 60


def test_loop_bracket_hides_and_ignores_drags_on_imported_effects():
    editor = make_editor()
    black = [(0, 0, 0)] * EFFECT_LEDS
    imported = ImportedEffect("Sample", [black, black], effect_id=1, loop_frames=1)
    editor.imported_effects = [imported]
    editor.active_import_index = 0
    editor.draw()

    assert not any(
        action in ("loop_bracket_handle", "intro_bracket_handle")
        for _rect, action, _label in editor.buttons
    )


def test_full_no_outro_button_clears_loop_frames():
    editor = make_editor()
    editor.project.loop_frames = 12
    editor.draw()
    button = next(rect for rect, action, _label in editor.buttons if action == "clear_loop_end")

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, button=1, pos=button.center, clicks=1,
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, button=1, pos=button.center,
    ))

    assert editor.project.loop_frames == 0


def test_space_and_timeline_controls_share_reliable_playback_state():
    editor = make_editor()

    editor.handle_event(pygame.event.Event(
        pygame.KEYDOWN, key=pygame.K_SPACE, mod=0, unicode=" ", repeat=False,
    ))
    assert editor.playing is True
    editor.handle_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE, mod=0))

    editor.draw()
    timeline = editor.layout()[2]
    timeline_controls = {
        action: label for rect, action, label in editor.buttons
        if timeline.collidepoint(rect.center)
    }
    assert timeline_controls["play"] == "Stop"
    assert {"step_frame:-1", "step_frame:1"} <= timeline_controls.keys()
    transport_rects = [
        rect for rect, action, _label in editor.buttons
        if action in {"step_frame:-1", "play", "step_frame:1"}
    ]
    track = editor._timeline_track(timeline)
    assert max(rect.bottom for rect in transport_rects) <= track.y - 24

    editor._action("play")
    assert editor.playing is False

    editor.duration_editing = True
    editor.handle_event(pygame.event.Event(
        pygame.KEYDOWN, key=pygame.K_SPACE, mod=0, unicode=" ", repeat=False,
    ))
    assert editor.playing is True


def test_shift_space_loops_the_section_and_plain_space_switches_to_full_loop():
    editor = make_editor()

    editor.handle_event(pygame.event.Event(
        pygame.KEYDOWN, key=pygame.K_SPACE, mod=pygame.KMOD_SHIFT, unicode=" ", repeat=False,
    ))
    assert editor.playing is True
    assert editor.loop_playback is True

    # Pressing plain Space while section-looping switches to full-sequence
    # looping, without interrupting playback or resetting the playhead.
    editor.current_ms = 123
    editor.handle_event(pygame.event.Event(
        pygame.KEYDOWN, key=pygame.K_SPACE, mod=0, unicode=" ", repeat=False,
    ))
    assert editor.playing is True
    assert editor.loop_playback is False
    assert editor.current_ms == 123

    # Pressing the same button again (plain Space, now full-sequence loop) stops it.
    editor.handle_event(pygame.event.Event(
        pygame.KEYDOWN, key=pygame.K_SPACE, mod=0, unicode=" ", repeat=False,
    ))
    assert editor.playing is False


def test_play_loop_button_is_registered_next_to_play_and_toggles():
    editor = make_editor()
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert "play_loop" in actions

    editor._action("play_loop")
    assert editor.playing is True
    assert editor.loop_playback is True

    editor.draw()
    label = next(label for _rect, action, label in editor.buttons if action == "play_loop")
    assert label == "Play Loop"

    editor._action("play_loop")
    assert editor.playing is False
    assert editor.loop_playback is False


def test_frame_step_snaps_to_project_frames_pauses_and_clamps():
    editor = make_editor()
    editor.project.frame_ms = 50
    editor.project.duration_ms = 500
    editor.current_ms = 75
    editor.playing = True

    editor._action("step_frame:-1")
    assert editor.current_ms == 50
    assert editor.playing is False

    editor._action("step_frame:1")
    assert editor.current_ms == 100

    editor.current_ms = 450
    editor._action("step_frame:1")
    assert editor.current_ms == 450


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


def test_falloff_layer_is_unique_keyframeable_and_has_duration_controls():
    editor = make_editor()

    editor._action("falloff_layer")
    falloff = editor.project.layers[-1]

    assert falloff.is_falloff is True
    assert falloff.falloff_ms == 600
    assert editor.project.falloff_duration_at(0) == 600
    editor.current_ms = 500
    editor._action("falloff_state:0")
    assert editor.project.falloff_duration_at(500) == 0
    editor._action("falloff_ms:100")
    assert falloff.falloff_ms == 700
    editor._action("falloff_layer")
    assert sum(layer.is_falloff for layer in editor.project.layers) == 1

    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert {"falloff_state:0", "falloff_state:1", "falloff_ms:-100", "falloff_ms:100"} <= actions


def test_shapes_and_generator_effects_cannot_be_added_to_falloff_layer():
    editor = make_editor()
    editor._action("falloff_layer")
    falloff = editor.project.layers[-1]

    editor._create_shape("ellipse", (0.5, 0.5))
    editor._action("random_led_editor")

    assert falloff.shapes == []
    assert falloff.effects == []


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


def test_layer_lock_blocks_canvas_and_keyframe_edits_until_unlocked():
    editor = make_editor()
    editor._create_shape("rectangle", (0.5, 0.5))
    shape = editor.selected
    assert shape is not None
    shape.add_keyframe("x", 500, 0.7)
    original_shape_count = len(editor.project.layers[0].shapes)

    editor._action("toggle_layer_lock:0")

    assert editor.project.layers[0].locked is True
    assert editor.selected is None
    assert editor._pick((0.5, 0.5)) is None
    editor._create_shape("ellipse", (0.4, 0.4))
    assert len(editor.project.layers[0].shapes) == original_shape_count

    editor.selected_keyframes = {(shape.id, "x", 500)}
    editor._delete_selected_keyframes()
    assert shape.keyframes["x"][0].time_ms == 500
    assert "Unlock" in editor.status

    editor._action("toggle_layer_lock:0")
    assert editor.project.layers[0].locked is False
    assert editor._pick((0.7, 0.5)) is shape


def test_layer_opacity_field_is_typeable_and_undoable():
    editor = make_editor()
    editor.draw()
    field = next(
        rect for rect, action, _label in editor.buttons if action == "scrub:layer_opacity"
    )

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, button=1, pos=field.center, clicks=1,
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, button=1, pos=field.center,
    ))
    assert editor.property_editing == "layer_opacity"

    editor.property_input = "35"
    editor.property_input_select_all = False
    editor._handle_property_input(pygame.event.Event(
        pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="",
    ))
    assert editor.project.layers[0].opacity == 0.35

    editor._undo()
    assert editor.project.layers[0].opacity == 1.0

    editor.draw()
    lock_actions = [
        action for _rect, action, _label in editor.buttons
        if action == "toggle_layer_lock:0"
    ]
    assert len(lock_actions) >= 2


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


def test_hover_tooltip_explains_snap_behavior(monkeypatch):
    editor = make_editor()
    editor.screen.fill((1, 2, 3))
    target = pygame.Rect(100, 100, 50, 30)
    editor.buttons = [(target, "snap", "Snap")]
    monkeypatch.setattr(pygame.mouse, "get_pos", lambda: target.center)

    editor._draw_hover_tooltip()

    assert editor.screen.get_at((target.centerx + 18, target.centery + 20))[:3] != (1, 2, 3)


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


def test_canvas_zoom_recovers_an_offscreen_playfield_and_keeps_it_reachable():
    editor = make_editor()
    editor.zoom = 0.4
    editor.pan.update(10000, 10000)
    _canvas, panel, timeline = editor.layout()
    viewport = pygame.Rect(68, 54, panel.x - 68, timeline.y - 54)

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEWHEEL,
        {"y": -1, "x": 0, "pos": viewport.center},
    ))

    canvas, _, _ = editor.layout()
    assert viewport.contains(canvas)


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
    second_row = (timeline.x + 120, editor._timeline_track(timeline).y + 47)
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


def test_palette_has_sixteen_distinct_colors():
    from cnc_light_editor.app import PALETTE

    assert len(PALETTE) == 16
    assert len(set(PALETTE)) == 16  # no accidental duplicates


def test_custom_color_swatch_applies_and_is_undoable():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.custom_colors = [(12, 34, 56)]

    editor._action("custom_color:0")
    assert tuple(editor.selected.state_at(editor.current_ms)["color"]) == (12, 34, 56)

    editor._undo()
    assert tuple(editor.selected.state_at(editor.current_ms)["color"]) != (12, 34, 56)


def test_open_color_picker_seeds_rgb_from_the_selected_shapes_color():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor._action("color:4")  # (30, 180, 255)

    editor._action("open_color_picker")
    assert editor.color_picker_open is True
    assert editor.color_picker_rgb == [30, 180, 255]


def test_color_slider_drag_updates_the_working_rgb_value():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.color_picker_open = True
    editor.color_picker_rgb = [0, 0, 0]
    editor.draw()  # populates editor.buttons with the picker's slider hit-boxes
    panel = editor._color_picker_panel_rect()
    slider = editor._color_slider_rect(panel, 1)  # G channel

    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": (slider.centerx, slider.centery)})
    )
    assert editor.drag_mode == "color_slider:1"
    assert 100 < editor.color_picker_rgb[1] < 156  # roughly the middle of the 0-255 track
    assert editor.color_picker_rgb[0] == 0 and editor.color_picker_rgb[2] == 0  # other channels untouched

    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, {"button": 1, "pos": (slider.centerx, slider.centery)})
    )
    assert editor.drag_mode is None


def test_apply_color_picker_applies_saves_custom_color_and_is_undoable():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor._action("open_color_picker")
    editor.color_picker_rgb = [9, 99, 199]
    editor.draw()  # populates editor.buttons with the picker's own layout
    apply_rect = next(rect for rect, action, _label in editor.buttons if action == "apply_color_picker")

    editor.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": apply_rect.center}))

    assert editor.color_picker_open is False
    assert tuple(editor.selected.state_at(editor.current_ms)["color"]) == (9, 99, 199)
    assert (9, 99, 199) in [tuple(color) for color in editor.custom_colors]

    editor._undo()
    assert tuple(editor.selected.state_at(editor.current_ms)["color"]) != (9, 99, 199)


def test_color_picker_escape_closes_without_applying():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor._action("color:4")
    original = tuple(editor.selected.state_at(editor.current_ms)["color"])

    editor._action("open_color_picker")
    editor.color_picker_rgb = [1, 2, 3]
    editor.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE, "mod": 0, "unicode": ""}))

    assert editor.color_picker_open is False
    assert tuple(editor.selected.state_at(editor.current_ms)["color"]) == original


def test_custom_colors_are_capped_and_evict_oldest_first():
    from cnc_light_editor.app import CUSTOM_COLOR_CAP

    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    for i in range(CUSTOM_COLOR_CAP + 3):
        editor._apply_custom_color((i, i, i))

    saved = [tuple(color) for color in editor.custom_colors]
    assert len(saved) == CUSTOM_COLOR_CAP
    assert (0, 0, 0) not in saved  # the oldest entries rolled off
    last = CUSTOM_COLOR_CAP + 2
    assert (last, last, last) in saved


def test_custom_colors_persist_across_editor_instances(tmp_path):
    pygame.init()
    screen = pygame.display.set_mode((1280, 1024), pygame.RESIZABLE)
    settings_path = tmp_path / "settings.json"
    editor = Editor(screen, settings_path=settings_path, check_recovery=False)
    editor._create_shape("ellipse", (0.5, 0.5))
    editor._apply_custom_color((5, 6, 7))

    restored = Editor(screen, settings_path=settings_path, check_recovery=False)
    assert (5, 6, 7) in [tuple(color) for color in restored.custom_colors]


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


def test_headless_file_browser_saves_project_without_desktop_dialogs(tmp_path):
    editor = make_editor()
    editor.project.name = "Headless save"
    editor._open_file_browser(
        title="Save CnC Light project",
        mode="save",
        purpose="project_save",
        initial_directory=tmp_path,
        extension=".cnclight",
        filename="pi-effect",
    )

    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert {
        "file_browser_path", "file_browser_up", "file_browser_filename",
        "file_browser_cancel", "file_browser_accept",
    } <= actions
    assert editor._accept_file_browser() is True

    saved = tmp_path / "pi-effect.cnclight"
    assert saved.exists()
    assert editor.project_path == saved.resolve()
    assert editor.file_browser_open is False


def test_headless_file_browser_filters_files_and_confirms_dirty_load(tmp_path):
    source = Project("Load on Pi")
    project_path = tmp_path / "load-me.cnclight"
    source.save(project_path)
    (tmp_path / "ignore.txt").write_text("not a project", encoding="utf-8")
    folder = tmp_path / "Subfolder"
    folder.mkdir()
    editor = make_editor()
    editor.project.name = "Unsaved current project"
    editor._open_file_browser(
        title="Open CnC Light project",
        mode="open",
        purpose="project_load",
        initial_directory=tmp_path,
        extension=".cnclight",
    )

    names = [entry.name for entry in editor._file_browser_entries()]
    assert names == ["Subfolder", "load-me.cnclight"]
    editor.file_browser_selected = names.index("load-me.cnclight")
    assert editor._accept_file_browser() is False
    assert editor.confirmation_open is True
    assert editor.confirmation_action == "load_project"
    assert editor.project.name == "Unsaved current project"

    editor._resolve_confirmation(True)
    assert editor.confirmation_open is False
    assert editor.project.name == "Load on Pi"
    assert editor.project_path == project_path.resolve()


def test_all_file_operations_open_the_in_app_browser():
    editor = make_editor()

    editor._choose_project_save()
    assert (editor.file_browser_mode, editor.file_browser_purpose) == ("save", "project_save")
    editor._cancel_file_browser()
    editor._choose_project_load()
    assert (editor.file_browser_mode, editor.file_browser_purpose) == ("open", "project_load")
    editor._cancel_file_browser()
    editor._choose_effect_data()
    assert (editor.file_browser_mode, editor.file_browser_purpose) == ("open", "effect_import")
    editor._cancel_file_browser()
    editor._choose_export_bank_map()
    assert (editor.file_browser_mode, editor.file_browser_purpose) == ("open", "bank_map")
    editor._cancel_file_browser()
    editor._choose_export_bank_save()
    assert (editor.file_browser_mode, editor.file_browser_purpose) == ("save", "bank_export")


def test_dirty_new_project_uses_in_app_confirmation():
    editor = make_editor()
    editor.project.name = "Keep until confirmed"

    assert editor._new_project(confirm=True) is False
    assert editor.confirmation_open is True
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert {"confirmation_yes", "confirmation_no"} <= actions

    editor._resolve_confirmation(True)
    assert editor.confirmation_open is False
    assert editor.project.name == "Untitled effect"


def test_exit_button_preserves_dirty_project_in_recovery_before_closing(monkeypatch):
    editor = make_editor()
    autosaved = []
    monkeypatch.setattr(
        editor, "_maybe_autosave",
        lambda **kwargs: autosaved.append(kwargs) or True,
    )

    editor._action("exit")
    assert editor.confirmation_open is True
    assert editor.confirmation_action == "exit_application"
    assert editor.exit_requested is False

    editor._resolve_confirmation(True)
    assert autosaved == [{"force": True}]
    assert editor.exit_requested is True


def test_update_restart_event_autosaves_dirty_project(monkeypatch):
    editor = make_editor()
    autosaved = []
    monkeypatch.setattr(
        editor, "_maybe_autosave",
        lambda **kwargs: autosaved.append(kwargs) or True,
    )
    editor.update_events.put(UpdateEvent("restart", "Update installed — restarting…"))

    editor._poll_update_events()

    assert autosaved == [{"force": True}]
    assert editor.restart_requested is True
    assert editor.update_state == "restart"


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
    world_x, world_y = editor._screen_to_world(canvas.center, canvas)
    editor._move_selected_led(canvas.center, canvas)
    moved = editor._current_led()
    assert moved.x == round(world_x * editor.led_map.width)
    assert moved.y == round(world_y * editor.led_map.height)

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


def test_led_move_is_undoable_and_redoable_via_its_own_history():
    editor = make_editor()
    editor.calibration = True
    canvas, _, _ = editor.layout()
    led = editor._current_led()
    original = (led.x, led.y)
    marker = editor._world_to_screen(editor.led_points[0], canvas)
    editor.led_move_ready_id = led.id

    editor.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": marker}))
    editor.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, {"button": 1, "pos": marker}))
    target = (canvas.centerx + 25, canvas.centery - 30)
    editor.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, {"pos": target, "rel": (0, 0), "buttons": (0, 0, 0)})
    )
    editor.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": target}))
    moved = (led.x, led.y)
    assert moved != original

    assert len(editor.led_map_undo_stack) == 1
    editor._undo_led_map()
    assert (editor._current_led().x, editor._current_led().y) == original
    assert "LED map: Undo" in editor.status

    editor._redo_led_map()
    assert (editor._current_led().x, editor._current_led().y) == moved
    assert "LED map: Redo" in editor.status


def test_led_move_cancelled_with_escape_creates_no_undo_entry():
    editor = make_editor()
    editor.calibration = True
    editor.led_move_ready_id = None
    canvas, _, _ = editor.layout()
    marker = editor._world_to_screen(editor.led_points[0], canvas)
    click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": marker})
    release = pygame.event.Event(pygame.MOUSEBUTTONUP, {"button": 1, "pos": marker})
    editor.handle_event(click)
    editor.handle_event(release)
    editor.handle_event(click)
    editor.handle_event(release)
    editor.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, {"pos": canvas.center, "rel": (0, 0), "buttons": (0, 0, 0)})
    )

    editor.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE, "mod": 0, "unicode": ""}))

    assert editor.led_map_undo_stack == []
    assert editor.led_map_change_snapshot is None


def test_add_and_delete_led_are_each_undoable():
    editor = make_editor()
    editor.calibration = True
    if len(editor.led_map.leds) >= 68:
        editor._delete_led()
    original_count = len(editor.led_map.leds)

    editor._action("add_led")
    assert len(editor.led_map.leds) == original_count + 1
    editor._undo_led_map()
    assert len(editor.led_map.leds) == original_count

    editor._action("add_led")
    editor._action("delete_led")
    assert len(editor.led_map.leds) == original_count
    editor._undo_led_map()  # undoes the delete
    assert len(editor.led_map.leds) == original_count + 1
    editor._undo_led_map()  # undoes the add
    assert len(editor.led_map.leds) == original_count


def test_ctrl_z_routes_to_led_map_history_only_while_calibration_is_open():
    editor = make_editor()
    editor.calibration = True
    led = editor._current_led()
    editor._begin_led_change()
    led.name = "Renamed while calibrating"
    editor._commit_led_change()
    assert len(editor.led_map_undo_stack) == 1
    project_undo_depth_before = len(editor.undo_stack)

    editor.handle_event(
        pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_z, "mod": pygame.KMOD_CTRL, "unicode": ""})
    )
    assert editor._current_led().name != "Renamed while calibrating"
    assert len(editor.undo_stack) == project_undo_depth_before  # project history untouched

    editor.calibration = False
    editor._begin_change()
    editor.project.name = "Changed while not calibrating"
    editor._commit_change()
    led_undo_depth_before = len(editor.led_map_undo_stack)

    editor.handle_event(
        pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_z, "mod": pygame.KMOD_CTRL, "unicode": ""})
    )
    assert editor.project.name != "Changed while not calibrating"
    assert len(editor.led_map_undo_stack) == led_undo_depth_before  # LED history untouched


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


def test_stencil_uses_light_blue_background_while_canvas_is_active():
    editor = make_editor()
    editor.project.overlay = True
    editor._action("canvas_layer")
    canvas, _, _ = editor.layout()
    sample = (canvas.x + 10, canvas.y + 10)
    editor.screen.fill((0, 0, 0))

    editor._draw_leds(canvas, stencil_back=True)
    active = editor.screen.get_at(sample)

    assert active.b > active.r
    assert active.g > active.r
    editor.current_ms = 50
    editor._action("canvas_state:0")
    editor.screen.fill((0, 0, 0))
    editor._draw_leds(canvas, stencil_back=True)
    assert editor.screen.get_at(sample)[:3] == (0, 0, 0)


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


def test_noise_gradient_button_is_offered_and_disables_radial_angle_controls():
    editor = make_editor()
    editor._create_shape("rectangle", (0.5, 0.5))
    editor._action("gradient_editor")
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert "gradient_type:noise" in actions

    editor._action("gradient_type:noise")
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}

    assert editor.selected.gradient_type == "noise"
    assert "gradient_radial_mode:radius" not in actions
    assert "gradient_angle" not in actions


def test_wiggle_toggle_offsets_position_and_is_undoable():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    shape = editor.selected
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert "toggle_wiggle" in actions
    assert shape.wiggle_enabled is False

    editor._action("toggle_wiggle")

    assert shape.wiggle_enabled is True

    editor._undo()

    assert editor.selected.wiggle_enabled is False


def test_wiggle_amplitude_and_speed_fields_appear_only_once_enabled():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert "scrub:wiggle_amplitude" not in actions

    editor._action("toggle_wiggle")
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}

    assert {"scrub:wiggle_amplitude", "scrub:wiggle_speed"} <= actions


def test_dragging_wiggle_amplitude_and_speed_does_not_crash():
    # Regression: Shape.state_at() didn't include these two props, so the
    # generic scrub handler's drag_shape_state[prop] lookup raised KeyError
    # the instant the user dragged either field.
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor._action("toggle_wiggle")
    editor.draw()

    for prop in ("wiggle_amplitude", "wiggle_speed"):
        field = next(
            rect for rect, action, _label in editor.buttons if action == f"scrub:{prop}"
        )
        editor.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1, pos=field.center, clicks=1,
        ))
        dragged = (field.centerx + 20, field.centery)
        editor.handle_event(pygame.event.Event(
            pygame.MOUSEMOTION, pos=dragged, rel=(20, 0), buttons=(1, 0, 0),
        ))
        editor.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONUP, button=1, pos=dragged,
        ))
        editor.draw()

    assert editor.selected.wiggle_amplitude != 0.02
    assert editor.selected.wiggle_speed != 2.0


def test_noise_gradient_is_visible_in_the_guide_view_canvas_and_animates():
    # Regression: noise only fed the LED/stencil render path at first, so the
    # shape's own on-canvas preview (guide view) showed a flat solid color -
    # indistinguishable from having no gradient at all while editing normally.
    editor = make_editor()
    editor._create_shape("rectangle", (0.5, 0.5))
    editor.selected.width = 0.3
    editor.selected.height = 0.3
    editor._action("gradient_editor")
    editor._action("gradient_type:noise")
    canvas, _panel, _timeline = editor.layout()

    editor.current_ms = 0
    editor.draw()
    frame_a = screenshot_pixels(editor.screen, canvas)
    editor.current_ms = 400
    editor.draw()
    frame_b = screenshot_pixels(editor.screen, canvas)

    assert frame_a != frame_b


def screenshot_pixels(screen, rect):
    return [
        screen.get_at((x, y))
        for x in range(rect.x, rect.x + rect.width, 8)
        for y in range(rect.y, rect.y + rect.height, 8)
    ]


def test_inspector_scrolls_to_reveal_duplicate_and_delete_when_content_overflows():
    # Worst case for the shape Inspector's vertical budget: Stroke fill mode
    # adds a stroke-width row and Wiggle adds its own toggle + amount/speed
    # row, all above a fixed-height status footer at the panel bottom. Once
    # this overflows the visible area, the panel should scroll (like the
    # timeline) rather than let content spill under the footer.
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor._action("toggle_wiggle")
    editor._action("fill_mode:stroke")
    editor.draw()
    footer_top = editor.layout()[1].bottom - 47

    for rect, action, _label in editor.buttons:
        if action in ("duplicate", "delete"):
            assert rect.bottom <= footer_top

    assert editor.inspector_max_scroll > 0

    editor.inspector_scroll = editor.inspector_max_scroll
    editor.draw()
    revealed = {
        action: rect for rect, action, _label in editor.buttons if action in ("duplicate", "delete")
    }

    assert revealed.keys() == {"duplicate", "delete"}
    for rect in revealed.values():
        assert editor.layout()[1].y + 76 <= rect.top
        assert rect.bottom <= footer_top


def test_inspector_panel_scroll_wheel_is_clamped_to_content_bounds():
    editor = make_editor()
    editor._create_shape("ellipse", (0.5, 0.5))
    editor._action("toggle_wiggle")
    editor._action("fill_mode:stroke")
    editor.draw()
    panel = editor.layout()[1]

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEWHEEL, {"y": -100, "x": 0, "pos": panel.center},
    ))
    assert editor.inspector_scroll == editor.inspector_max_scroll

    editor.handle_event(pygame.event.Event(
        pygame.MOUSEWHEEL, {"y": 100, "x": 0, "pos": panel.center},
    ))
    assert editor.inspector_scroll == 0


def test_random_led_editor_creates_effect_and_keyframes_enabled_state():
    editor = make_editor()

    editor._action("random_led_editor")
    effect = editor._active_random_led_effect()
    editor.current_ms = 500
    editor._action("generator_toggle")

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
        "generator_field:seed", "generator_field:life_ms",
        "generator_field:born_speed", "generator_field:particle_count",
        "generator_field:opacity", "generator_toggle", "generator_keyframe",
        "generator_opacity_keyframe",
    } <= actions

    editor.current_ms = 350
    editor._action("generator_opacity_keyframe")
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
        if action == "generator_field:opacity"
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
        if action == "generator_field:life_ms"
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


def test_generator_keyframe_delete_and_undo_round_trip():
    editor = make_editor()
    editor._action("random_led_editor")
    editor.current_ms = 600
    editor._action("generator_keyframe")
    effect_id = editor._active_random_led_effect().id

    editor._delete_selected_keyframes()
    assert editor._active_random_led_effect().keyframes == {}

    editor._undo()
    restored = editor._find_keyframe_target(effect_id)
    assert restored.keyframes["enabled"][0].time_ms == 600


def test_fx_menu_lists_all_five_generator_kinds_and_picks_one():
    editor = make_editor()
    editor._action("generator_menu_toggle")
    editor.draw()
    menu_actions = {action for _rect, action, _label in editor.buttons}

    assert {
        "random_led_editor", "strobe_editor", "color_cycle_editor",
        "pulse_editor", "comet_editor",
    } <= menu_actions

    editor._action("strobe_editor")

    assert editor.strobe_editor_open is True
    assert editor.generator_menu_open is False
    assert len(editor.project.layers[0].strobe_effects) == 1


def test_opening_a_second_generator_panel_closes_the_first():
    editor = make_editor()
    editor._action("random_led_editor")
    assert editor.random_led_editor_open is True

    editor._action("strobe_editor")

    assert editor.strobe_editor_open is True
    assert editor.random_led_editor_open is False
    assert editor._active_generator_panel_kind() == "strobe"


def test_strobe_panel_exposes_frequency_and_duty_cycle_and_scrubs():
    editor = make_editor()
    editor._action("strobe_editor")
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}

    assert {
        "generator_field:frequency_hz", "generator_field:duty_cycle",
        "generator_field:opacity", "generator_toggle", "generator_keyframe",
        "generator_delete",
    } <= actions

    frequency_field = next(
        rect for rect, action, _label in editor.buttons
        if action == "generator_field:frequency_hz"
    )
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": frequency_field.center},
    ))
    dragged = (frequency_field.centerx + 200, frequency_field.centery)
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEMOTION, {"pos": dragged, "rel": (200, 0), "buttons": (1, 0, 0)},
    ))
    editor.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, {"button": 1, "pos": dragged},
    ))

    effect = editor._active_generator_effect("strobe")
    assert effect.frequency_hz > 4.0


def test_strobe_blackout_toggle_flips_and_is_undoable():
    editor = make_editor()
    editor._action("strobe_editor")
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}
    assert "generator_toggle_blackout" in actions

    effect = editor._active_generator_effect("strobe")
    assert effect.blackout is False

    editor._action("generator_toggle_blackout")
    assert effect.blackout is True

    editor._undo()
    assert editor._active_generator_effect("strobe").blackout is False


def test_color_cycle_panel_has_no_color_swatch_but_has_direction_toggle():
    editor = make_editor()
    editor._action("color_cycle_editor")
    editor.draw()
    actions = {action for _rect, action, _label in editor.buttons}

    assert "generator_toggle_direction" in actions
    assert not any(action.startswith("color:") for action in actions)

    effect = editor._active_generator_effect("color_cycle")
    assert effect.direction == 1
    editor._action("generator_toggle_direction")
    assert effect.direction == -1


def test_comet_panel_delete_removes_the_effect_and_closes_the_panel():
    editor = make_editor()
    editor._action("comet_editor")
    assert len(editor.project.layers[0].comet_effects) == 1

    editor._action("generator_delete")

    assert editor.project.layers[0].comet_effects == []
    assert editor._active_generator_panel_kind() is None


def test_new_generator_kinds_are_blocked_on_a_locked_layer():
    editor = make_editor()
    editor.project.layers[0].locked = True

    editor._action("strobe_editor")

    assert editor.project.layers[0].strobe_effects == []
    assert editor.strobe_editor_open is False


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
        "export_bank_map", "export_bank_write", "export_bank_write_as", "export_bank_close",
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


def test_export_effect_bank_file_replaces_bank_entry_sharing_current_project_id(tmp_path):
    black = [[(0, 0, 0)] * 68]
    editor = make_editor()
    editor.project.name = "New version"
    editor.project.effect_id = 4
    editor.project.duration_ms = 50
    editor.project.frame_ms = 50
    editor.export_bank_effects = [ImportedEffect("Old version", black, effect_id=4)]

    # Sharing an ID with the project is a replace, not a duplicate: the byte
    # estimate and validation must agree before export actually happens.
    assert editor._export_bank_used_bytes() == editor.project.flash_bytes
    assert editor._export_bank_validation_errors() == []

    path = editor.export_effect_bank_file(tmp_path / "combined.h")
    effects = parse_effect_data(path.read_text(encoding="utf-8"))
    assert [(effect.effect_id, effect.name) for effect in effects] == [(4, "New version")]
    assert effects[0].project_data is not None


def test_mapped_bank_saves_in_place_and_refreshes_without_remapping(tmp_path):
    black = [[(0, 0, 0)] * 68]
    target = export_effect_bank(
        [ImportedEffect("Existing", black, effect_id=7)],
        tmp_path / "effect_data.h",
    )
    editor = make_editor()
    editor.project.name = "New effect"
    editor.project.effect_id = 8
    editor.project.duration_ms = 50
    editor.project.frame_ms = 50
    editor.map_effect_bank_file(target)

    assert editor.save_mapped_effect_bank() is True

    assert editor.file_browser_open is False
    assert [(effect.effect_id, effect.name) for effect in editor.export_bank_effects] == [
        (7, "Existing"), (8, "New effect"),
    ]
    assert [(effect.effect_id, effect.name) for effect in editor.imported_effects] == [
        (7, "Existing"), (8, "New effect"),
    ]
    assert [(effect.effect_id, effect.name) for effect in parse_effect_data(
        target.read_text(encoding="utf-8")
    )] == [(7, "Existing"), (8, "New effect")]


def test_mapped_bank_requires_confirmation_before_accidental_id_replacement(tmp_path):
    black = [[(0, 0, 0)] * 68]
    target = export_effect_bank(
        [ImportedEffect("Protected", black, effect_id=4)],
        tmp_path / "effect_data.h",
    )
    before = target.read_text(encoding="utf-8")
    editor = make_editor()
    editor.project.name = "Unrelated project"
    editor.project.effect_id = 4
    editor.project.duration_ms = 50
    editor.project.frame_ms = 50
    editor.map_effect_bank_file(target)

    assert editor.save_mapped_effect_bank() is False
    assert editor.confirmation_open is True
    assert editor.confirmation_action == "save_mapped_bank"
    assert target.read_text(encoding="utf-8") == before

    editor._resolve_confirmation(True)
    restored = parse_effect_data(target.read_text(encoding="utf-8"))[0]
    assert restored.name == "Unrelated project"
    assert restored.symbol == "fx_protected"  # rename does not churn the far-address symbol
    assert editor.export_bank_project_origin_id == 4


def test_export_bank_validation_allows_project_id_match_but_flags_bank_internal_duplicates():
    black = [[(0, 0, 0)] * 68]
    editor = make_editor()
    editor.project.effect_id = 4
    editor.export_bank_effects = [ImportedEffect("Existing", black, effect_id=4)]

    assert editor._export_bank_validation_errors() == []

    editor.export_bank_effects.append(ImportedEffect("Existing 2", black, effect_id=4))
    errors = editor._export_bank_validation_errors()
    assert any("Duplicate ID 4" in error for error in errors)


def test_export_bank_search_filters_and_sort_modes_reorder_bank_effects():
    black = [[(0, 0, 0)] * 68]
    editor = make_editor()
    editor.export_bank_effects = [
        ImportedEffect("Alpha", black * 3, effect_id=5, frame_ms=50),
        ImportedEffect("Bravo", black * 1, effect_id=2, frame_ms=50),
        ImportedEffect("Charlie", black * 2, effect_id=9, frame_ms=100),
    ]

    def names(pairs):
        return [effect.name for _index, effect in pairs]

    assert editor.export_bank_sort_mode == "id"
    assert names(editor._export_bank_visible_pairs()) == ["Bravo", "Alpha", "Charlie"]

    editor._export_bank_action("export_bank_sort_cycle")
    assert editor.export_bank_sort_mode == "name"
    assert names(editor._export_bank_visible_pairs()) == ["Alpha", "Bravo", "Charlie"]

    editor._export_bank_action("export_bank_sort_cycle")
    assert editor.export_bank_sort_mode == "size"
    assert names(editor._export_bank_visible_pairs()) == ["Alpha", "Charlie", "Bravo"]

    editor._export_bank_action("export_bank_sort_cycle")
    assert editor.export_bank_sort_mode == "duration"
    assert names(editor._export_bank_visible_pairs()) == ["Charlie", "Alpha", "Bravo"]

    editor._export_bank_action("export_bank_sort_cycle")
    assert editor.export_bank_sort_mode == "id"

    editor.export_bank_query = "al"
    filtered = editor._export_bank_visible_pairs()
    assert names(filtered) == ["Alpha"]
    assert filtered[0][0] == 0  # real index into export_bank_effects, not the filtered position


def test_export_bank_current_project_is_last_in_content_list_and_memory_bar():
    black = [[(0, 0, 0)] * 68]
    editor = make_editor()
    editor.export_bank_effects = [
        ImportedEffect("First", black, effect_id=1),
        ImportedEffect("Second", black, effect_id=2),
    ]
    editor._action("export")
    editor.draw()

    select_actions = [
        action for _rect, action, _label in editor.buttons
        if action.startswith("export_bank_select:")
    ]
    # Both the memory bar (first contiguous block of these actions) and the
    # Bank Content list (second block) put the current project last.
    assert select_actions[0] == "export_bank_select:0"
    assert select_actions[-1] == "export_bank_select:-1"

    assert editor._export_bank_color_index(0) == 0
    assert editor._export_bank_color_index(1) == 1
    assert editor._export_bank_color_index(-1) == 2  # one past the bank's own effects


def test_suggest_effect_id_fixes_collision_but_leaves_free_default_alone():
    black = [[(0, 0, 0)] * 68]
    editor = make_editor()
    assert editor.project.effect_id == 1

    editor._suggest_effect_id_if_still_default()
    assert editor.project.effect_id == 1  # no bank yet - nothing to fix

    editor.export_bank_effects = [ImportedEffect("Other", black, effect_id=5)]
    editor._suggest_effect_id_if_still_default()
    assert editor.project.effect_id == 1  # ID 1 is free in this bank - leave it

    editor.export_bank_effects.append(ImportedEffect("Collides", black, effect_id=1))
    editor._suggest_effect_id_if_still_default()
    assert editor.project.effect_id == 6  # real collision - next free is max(5, 1) + 1

    # Once bumped off the pristine default, later bank changes never
    # silently override an ID the project already has.
    editor.export_bank_effects.append(ImportedEffect("Higher", black, effect_id=20))
    editor._suggest_effect_id_if_still_default()
    assert editor.project.effect_id == 6


def test_map_effect_bank_file_suggests_next_id_when_project_id_collides(tmp_path):
    black = [[(0, 0, 0)] * 68]
    header = export_effect_bank(
        [
            ImportedEffect("Loop - Jackpot", black, effect_id=1),
            ImportedEffect("UFO Lottery", black, effect_id=2),
            ImportedEffect("Shooter", black, effect_id=3),
        ],
        tmp_path / "effect_data.h",
    )
    editor = make_editor()
    assert editor.project.effect_id == 1
    editor.map_effect_bank_file(header)
    assert editor.project.effect_id == 4


def test_export_effect_bank_file_keeps_a_single_rolling_backup_of_the_previous_export(tmp_path):
    editor = make_editor()
    editor.project.effect_id = 1
    editor.project.duration_ms = 50
    editor.project.frame_ms = 50
    target = tmp_path / "effect_data.h"
    backup_path = editor._export_bank_backup_path(target)

    editor.project.name = "Version A"
    editor.export_effect_bank_file(target)
    assert not backup_path.exists()  # nothing existed yet - nothing to back up
    assert "backed up" not in editor.export_bank_status

    editor.project.name = "Version B"
    editor.export_effect_bank_file(target)
    assert backup_path.is_file()
    assert "backed up" in editor.export_bank_status
    assert parse_effect_data(backup_path.read_text(encoding="utf-8"))[0].name == "Version A"

    editor.project.name = "Version C"
    editor.export_effect_bank_file(target)
    # The backup rolls over to "B" - always exactly one file, no history pile-up.
    assert parse_effect_data(backup_path.read_text(encoding="utf-8"))[0].name == "Version B"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["effect_data.h", "effect_data.h.bak"]


def test_restore_export_bank_backup_is_an_immediate_on_disk_undo_and_can_redo(tmp_path):
    editor = make_editor()
    editor.project.effect_id = 1
    editor.project.duration_ms = 50
    editor.project.frame_ms = 50
    target = tmp_path / "effect_data.h"

    editor.project.name = "Version A"
    editor.export_effect_bank_file(target)
    editor.project.name = "Version B"
    editor.export_effect_bank_file(target)

    assert editor.restore_export_bank_backup() is True
    assert [effect.name for effect in editor.export_bank_effects] == ["Version A"]
    assert "Restored backup" in editor.export_bank_status
    assert parse_effect_data(target.read_text(encoding="utf-8"))[0].name == "Version A"
    assert parse_effect_data(editor._export_bank_backup_path(target).read_text(encoding="utf-8"))[0].name == "Version B"

    assert editor.restore_export_bank_backup() is True
    assert parse_effect_data(target.read_text(encoding="utf-8"))[0].name == "Version B"


def test_restore_export_bank_backup_reports_when_none_exists(tmp_path):
    editor = make_editor()
    editor.export_bank_path = tmp_path / "effect_data.h"

    assert editor.restore_export_bank_backup() is False
    assert editor.export_bank_status == "No backup available to restore"


def test_export_bank_restore_backup_button_only_shows_once_a_backup_exists(tmp_path):
    editor = make_editor()
    editor.project.effect_id = 1
    editor.project.duration_ms = 50
    editor.project.frame_ms = 50
    target = tmp_path / "effect_data.h"
    editor.export_bank_path = target

    def has_restore_button():
        editor._action("export")
        editor.draw()
        return "export_bank_restore_backup" in {
            action for _rect, action, _label in editor.buttons
        }

    assert has_restore_button() is False

    editor.export_effect_bank_file(target)  # first export - nothing to restore yet
    assert has_restore_button() is False

    editor.export_effect_bank_file(target)  # second export creates the backup
    assert has_restore_button() is True


def test_effect_thumbnail_is_cached_and_picks_the_brightest_frame():
    editor = make_editor()
    dim_frame = [(10, 10, 10)] * 68
    bright_frame = [(250, 200, 30)] * 68
    effect = ImportedEffect("Sparkle", [dim_frame, bright_frame, dim_frame], effect_id=5)

    assert editor._effect_preview_frame_index(effect) == 1

    first = editor._effect_thumbnail(effect, 34)
    second = editor._effect_thumbnail(effect, 34)
    assert first is second  # cached, not regenerated
    assert first.get_size() == (34, 34)

    larger = editor._effect_thumbnail(effect, 64)
    assert larger is not first  # different size -> its own cache entry
    assert larger.get_size() == (64, 64)


def test_effect_preview_frame_index_ignores_overlay_transparent_cells():
    mostly_transparent = [TRANSPARENT_SENTINEL] * 67 + [(255, 255, 255)]
    modestly_lit = [(40, 40, 40)] * 68
    editor = make_editor()
    effect = ImportedEffect(
        "Overlay flash", [mostly_transparent, modestly_lit], effect_id=6, overlay=True,
    )

    # Raw (sentinel-included) brightness would make frame 0 look far
    # brighter than frame 1 - but for an overlay effect almost all of frame
    # 0 is transparent (not actually lit), so frame 1 must win instead.
    assert editor._effect_preview_frame_index(effect) == 1


def test_export_bank_detail_buttons_render_after_metadata_not_anchored_to_panel_bottom():
    black = [[(0, 0, 0)] * 68]
    editor = make_editor()
    editor.export_bank_effects = [
        ImportedEffect("Mapped", black, effect_id=9, project_data={"name": "Mapped"}),
    ]
    editor._action("export")
    editor.export_bank_selected = 0
    editor.draw()

    detail = editor._export_bank_layout()[2]
    load_rect = next(rect for rect, action, _label in editor.buttons if action == "export_bank_load_project")
    remove_rect = next(rect for rect, action, _label in editor.buttons if action == "export_bank_remove")

    # Same additive layout _draw_export_bank_details itself uses: 16 (badge)
    # + 35 (to Name) + 67 (to Firmware ID) + 75 (to metadata) + 7 rows * 20px
    # + a 14px gap. Buttons must never start before this point regardless of
    # the panel's fixed height - anchoring them to detail.bottom (the
    # previous code) let them slide up and overlap the last metadata rows.
    metadata_bottom = detail.y + 16 + 35 + 67 + 75 + 7 * 20 + 14
    assert load_rect.y >= metadata_bottom
    assert remove_rect.y >= load_rect.bottom

    # Pushing the buttons below the metadata (the fix above) only helps if
    # the panel's own frame is tall enough to still contain them - it
    # wasn't: this exact case (mapped effect with an embedded project, so
    # both buttons show) used to spill 52px past detail.bottom.
    assert remove_rect.bottom <= detail.bottom


def test_help_panel_is_tall_enough_for_its_tallest_column():
    editor = make_editor()
    panel = editor._help_panel_rect()

    # Mirrors _draw_help's own additive layout: content starts at
    # panel.y + 84, each section costs 29 (title) + rows * 26 + 14 (gap),
    # and the footer line is anchored at panel.bottom - 31.
    def column_height(sections):
        return sum(29 + len(rows) * 26 + 14 for _title, rows in sections)

    tallest_column = max(column_height(sections) for sections in HELP_COLUMNS)
    footer_gap = 31 + 16  # footer's own offset + a small breathing gap above it
    # The right column (LAYERS/CANVAS + VIEWPORT/SHAPES + LED MAP) used to
    # run past panel.bottom - its last rows overlapped the footer/frame.
    assert 84 + tallest_column <= panel.height - footer_gap


def test_export_bank_query_focus_and_typing_updates_query_without_closing_panel():
    editor = make_editor()
    editor.export_bank_open = True

    editor._export_bank_action("export_bank_query_focus")
    assert editor.export_bank_query_editing is True

    for character in "abc":
        editor._handle_export_bank_query_input(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, unicode=character, mod=0)
        )
    assert editor.export_bank_query == "abc"

    editor._handle_export_bank_query_input(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, unicode="", mod=0)
    )
    assert editor.export_bank_query_editing is False
    assert editor.export_bank_open is True
    assert editor.export_bank_query == "abc"

    # Routed through the real dispatcher (not the handler directly): once
    # Escape cleared query_editing, a Backspace keydown isn't routed to the
    # query handler anymore, so the typed text survives untouched.
    editor._handle_export_bank_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE, unicode="", mod=0)
    )
    assert editor.export_bank_query == "abc"
    assert editor.export_bank_open is True

    # Any other bank action clears query focus (mirrors clicking elsewhere).
    editor._export_bank_action("export_bank_query_focus")
    editor._export_bank_action("export_bank_close")
    assert editor.export_bank_query_editing is False


def test_map_effect_bank_file_remembers_path_and_reloads_on_next_editor(tmp_path):
    black = [[(0, 0, 0)] * 68]
    header = export_effect_bank(
        [ImportedEffect("Remembered", black, effect_id=6)], tmp_path / "effect_data.h",
    )
    pygame.init()
    screen = pygame.display.set_mode((1280, 1024), pygame.RESIZABLE)
    settings_path = tmp_path / "editor_settings.json"

    editor = Editor(screen, settings_path=settings_path, check_recovery=False)
    assert editor.export_bank_effects == []  # nothing remembered yet
    editor.map_effect_bank_file(header)
    assert editor.export_bank_effects[0].name == "Remembered"

    # A brand-new Editor reading the SAME settings file re-maps it on its
    # own, without the user having to click "Map header..." again.
    restored = Editor(screen, settings_path=settings_path, check_recovery=False)
    assert [effect.name for effect in restored.export_bank_effects] == ["Remembered"]
    assert restored.export_bank_path == header.resolve()


def test_remembered_effect_bank_header_missing_file_does_not_block_startup(tmp_path):
    from cnc_light_editor.ui_settings import UiSettingsStore

    settings_path = tmp_path / "editor_settings.json"
    UiSettingsStore(settings_path).save({"last_effect_bank_header": str(tmp_path / "gone.h")})

    pygame.init()
    screen = pygame.display.set_mode((1280, 1024), pygame.RESIZABLE)
    editor = Editor(screen, settings_path=settings_path, check_recovery=False)  # must not raise

    assert editor.export_bank_effects == []
    assert "gone.h" in editor.export_bank_status


def test_native_file_dialog_falls_back_to_in_app_browser_when_headless(tmp_path):
    # The whole test module forces SDL_VIDEODRIVER=dummy - the exact signal
    # used (regardless of the real OS) to mean "no real desktop, don't pop
    # up a native picker that would hang waiting for input".
    pygame.init()
    screen = pygame.display.set_mode((1280, 1024), pygame.RESIZABLE)
    editor = Editor(screen, settings_path=tmp_path / "settings.json", check_recovery=False)

    handled = editor._try_native_file_dialog(
        title="Map firmware effect_data.h", mode="open", purpose="bank_map",
        directory=tmp_path, extension=".h",
    )
    assert handled is False
    assert editor.file_browser_open is False


class _FakeTkRoot:
    """Stands in for tkinter.Tk() in tests: _try_native_file_dialog only ever
    calls withdraw/attributes/destroy on the root, and creating a REAL Tk
    root twice in one pytest process is unreliable on some Tcl/Tk installs
    (missing/relocated init.tcl on the second instantiation) - this avoids
    touching the real Tcl runtime at all."""

    def withdraw(self) -> None:
        pass

    def attributes(self, *_args, **_kwargs) -> None:
        pass

    def destroy(self) -> None:
        pass


def test_native_file_dialog_used_on_windows_and_maps_the_chosen_header(monkeypatch, tmp_path):
    tkinter = pytest.importorskip("tkinter")
    from tkinter import filedialog

    black = [[(0, 0, 0)] * 68]
    header = export_effect_bank(
        [ImportedEffect("Picked", black, effect_id=2)], tmp_path / "picked.h",
    )
    pygame.init()
    screen = pygame.display.set_mode((1280, 1024), pygame.RESIZABLE)
    editor = Editor(screen, settings_path=tmp_path / "settings.json", check_recovery=False)

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delenv("SDL_VIDEODRIVER", raising=False)
    monkeypatch.setattr(tkinter, "Tk", _FakeTkRoot)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **_kwargs: str(header))

    handled = editor._try_native_file_dialog(
        title="Map firmware effect_data.h", mode="open", purpose="bank_map",
        directory=tmp_path, extension=".h",
    )
    assert handled is True
    assert editor.file_browser_open is False
    assert editor.export_bank_effects[0].name == "Picked"


def test_native_file_dialog_cancel_reports_status_without_opening_in_app_browser(monkeypatch, tmp_path):
    tkinter = pytest.importorskip("tkinter")
    from tkinter import filedialog

    pygame.init()
    screen = pygame.display.set_mode((1280, 1024), pygame.RESIZABLE)
    editor = Editor(screen, settings_path=tmp_path / "settings.json", check_recovery=False)

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delenv("SDL_VIDEODRIVER", raising=False)
    monkeypatch.setattr(tkinter, "Tk", _FakeTkRoot)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **_kwargs: "")

    handled = editor._try_native_file_dialog(
        title="Map firmware effect_data.h", mode="open", purpose="bank_map",
        directory=tmp_path, extension=".h",
    )
    assert handled is True
    assert editor.file_browser_open is False
    assert editor.export_bank_status == "File selection cancelled"


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


def test_format_crash_entry_includes_timestamp_and_traceback():
    try:
        raise ValueError("boom")
    except ValueError as exc:
        entry = format_crash_entry(exc)

    assert "ValueError: boom" in entry
    assert "Traceback (most recent call last)" in entry
    # A timestamp line like "[2026-07-23 15:42:10]" - just check the shape,
    # not the exact value (that would make the test flaky around midnight).
    lines = entry.splitlines()
    assert lines[0] == "=" * 70
    assert lines[1].startswith("[") and lines[1].endswith("]")


def test_log_crash_appends_and_caps_at_max_entries(tmp_path):
    path = tmp_path / "crash.log"

    for i in range(CRASH_LOG_MAX_ENTRIES + 5):
        try:
            raise RuntimeError(f"failure {i}")
        except RuntimeError as exc:
            log_crash(exc, path)

    content = path.read_text(encoding="utf-8")
    # Only the most recent CRASH_LOG_MAX_ENTRIES survive - not an ever-growing file.
    assert content.count("RuntimeError: failure") == CRASH_LOG_MAX_ENTRIES
    assert "failure 0" not in content  # the oldest ones rolled off
    assert f"failure {CRASH_LOG_MAX_ENTRIES + 4}" in content  # the newest survives


def test_log_crash_never_raises_even_if_the_path_is_unwritable(tmp_path):
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("i am a file, not a directory", encoding="utf-8")
    bad_path = blocked / "crash.log"  # parent.mkdir() will fail: blocked is a file

    try:
        raise KeyError("unwritable")
    except KeyError as exc:
        log_crash(exc, bad_path)  # must not raise

    assert not bad_path.exists()


def test_get_git_commit_returns_a_plausible_short_hash_for_this_repo():
    commit = get_git_commit(ROOT)
    # This repo really is a git checkout, so a short hex hash is expected -
    # but stay tolerant of a None result (e.g. a shallow clone with no git
    # binary on PATH), since that's a supported fallback, not a bug.
    assert commit is None or (all(c in "0123456789abcdef" for c in commit) and 4 <= len(commit) <= 12)


def test_get_git_commit_is_cached_and_does_not_reshell_out(monkeypatch):
    get_git_commit(ROOT)  # warm the cache (a no-op if already warm)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("get_git_commit should hit its cache, not call subprocess.run again")

    monkeypatch.setattr(subprocess, "run", fail_if_called)
    get_git_commit(ROOT)  # must be served from the cache
