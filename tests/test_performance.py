import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")  # see test_editor_interactions.py for why

import pygame

from cnc_light_editor.app import Editor
from cnc_light_editor.performance import is_raspberry_pi, recommended_preview_fps


def make_editor(**kwargs) -> Editor:
    pygame.init()
    screen = pygame.display.get_surface()
    if screen is None or screen.get_size() != (1280, 1024):
        screen = pygame.display.set_mode((1280, 1024))
    return Editor(screen, check_recovery=False, **kwargs)


def test_preview_fps_adapts_to_pi_model_without_changing_project_fps():
    assert is_raspberry_pi(model="Raspberry Pi 3 Model B Plus Rev 1.3") is True
    assert is_raspberry_pi(model="Desktop PC") is False
    assert recommended_preview_fps(model="Raspberry Pi 3 Model B Plus Rev 1.3") == 20
    assert recommended_preview_fps(model="Raspberry Pi 4 Model B Rev 1.5") == 40
    assert recommended_preview_fps(model="Desktop PC") == 60
    assert recommended_preview_fps(override="24", model="Desktop PC") == 24
    assert recommended_preview_fps(override="999", model="Desktop PC") == 120


def test_scaled_playfield_is_cached_between_frames(monkeypatch):
    editor = make_editor()
    editor.stencil = True
    editor._scaled_view_cache.clear()
    calls = 0
    original = pygame.transform.smoothscale

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pygame.transform, "smoothscale", counted)
    editor.draw()
    editor.draw()

    assert calls == 1


def test_max_zoom_allocations_are_limited_to_visible_workspace():
    editor = make_editor()
    editor.stencil = True
    editor.zoom = 5.0
    editor._scaled_view_cache.clear()
    editor._glow_canvas_cache.clear()

    canvas, panel, timeline = editor.layout()
    viewport = pygame.Rect(68, 54, panel.x - 68, timeline.y - 54)
    assert canvas.height > viewport.height

    editor.draw()

    cached_surfaces = [
        *editor._scaled_view_cache.values(),
        *editor._glow_canvas_cache.values(),
    ]
    assert cached_surfaces
    assert all(surface.get_width() <= viewport.width for surface in cached_surfaces)
    assert all(surface.get_height() <= viewport.height for surface in cached_surfaces)


def test_zoom_cache_does_not_accumulate_full_resolution_surfaces():
    editor = make_editor()
    editor.stencil = True

    for zoom in (5.0, 4.0, 3.0, 2.0, 1.0, 0.5, 0.35):
        editor.zoom = zoom
        editor.draw()

    assert len(editor._scaled_view_cache) <= 2
    assert len(editor._glow_canvas_cache) <= 2


def test_smoothscale_never_receives_a_shared_buffer_subsurface(monkeypatch):
    editor = make_editor()
    editor.stencil = True
    editor.zoom = 5.0
    editor._scaled_view_cache.clear()
    parents = []
    original = pygame.transform.smoothscale

    def inspect_source(source, *args, **kwargs):
        parents.append(source.get_parent())
        return original(source, *args, **kwargs)

    monkeypatch.setattr(pygame.transform, "smoothscale", inspect_source)
    editor.draw()

    assert parents
    assert all(parent is None for parent in parents)


def test_raspberry_pi_safe_scaling_avoids_smoothscale_native_path(monkeypatch):
    editor = make_editor(safe_scaling=True)
    editor.stencil = True
    editor.zoom = 5.0
    editor._scaled_view_cache.clear()
    scale_calls = 0
    original_scale = pygame.transform.scale

    def counted_scale(*args, **kwargs):
        nonlocal scale_calls
        scale_calls += 1
        return original_scale(*args, **kwargs)

    def forbidden_smoothscale(*_args, **_kwargs):
        raise AssertionError("Pi-safe rendering must not call smoothscale")

    monkeypatch.setattr(pygame.transform, "scale", counted_scale)
    monkeypatch.setattr(pygame.transform, "smoothscale", forbidden_smoothscale)
    editor.draw()

    assert scale_calls >= 1


def test_idle_run_draws_once_until_something_changes(monkeypatch):
    editor = make_editor()
    draw_calls = 0

    class FiniteClock:
        def __init__(self):
            self.ticks = 0

        def tick(self, _fps):
            self.ticks += 1
            if self.ticks >= 10:
                editor.exit_requested = True
            return 50

    def counted_draw():
        nonlocal draw_calls
        draw_calls += 1

    editor.clock = FiniteClock()
    editor.draw = counted_draw
    monkeypatch.setattr(pygame.event, "get", lambda: [])
    monkeypatch.setattr(pygame.display, "flip", lambda: None)

    editor.run(auto_update=False)

    assert draw_calls == 1


def test_playback_keeps_rendering_at_preview_ticks(monkeypatch):
    editor = make_editor()
    editor.playing = True
    draw_calls = 0

    class FiniteClock:
        def __init__(self):
            self.ticks = 0

        def tick(self, _fps):
            self.ticks += 1
            if self.ticks >= 6:
                editor.exit_requested = True
            return 50

    def counted_draw():
        nonlocal draw_calls
        draw_calls += 1

    editor.clock = FiniteClock()
    editor.draw = counted_draw
    monkeypatch.setattr(pygame.event, "get", lambda: [])
    monkeypatch.setattr(pygame.display, "flip", lambda: None)

    editor.run(auto_update=False)

    assert draw_calls == 6
    assert editor.current_ms == 300


def test_plain_play_loops_the_whole_sequence_forever(monkeypatch):
    editor = make_editor()
    editor.project.duration_ms = 250
    editor.playing = True
    editor.loop_playback = False

    class FiniteClock:
        def __init__(self):
            self.ticks = 0

        def tick(self, _fps):
            self.ticks += 1
            if self.ticks >= 8:
                editor.exit_requested = True
            return 50

    editor.clock = FiniteClock()
    editor.draw = lambda: None
    monkeypatch.setattr(pygame.event, "get", lambda: [])
    monkeypatch.setattr(pygame.display, "flip", lambda: None)

    editor.run(auto_update=False)

    # 8 ticks * 50ms = 400ms travelled against a 250ms duration -> wrapped to 150ms.
    # Plain Play never stops - it keeps looping the whole stored sequence.
    assert editor.playing is True
    assert editor.current_ms == 150


def test_play_loop_repeats_only_the_loop_section(monkeypatch):
    editor = make_editor()
    editor.project.duration_ms = 1000
    editor.project.frame_ms = 50
    editor.project.intro_frames = 2  # loop section starts at 100ms
    editor.project.loop_frames = 10  # loop section ends at 500ms
    editor.playing = True
    editor.loop_playback = True
    editor.current_ms = 0  # outside the loop section - must self-correct

    class FiniteClock:
        def __init__(self):
            self.ticks = 0

        def tick(self, _fps):
            self.ticks += 1
            if self.ticks >= 8:
                editor.exit_requested = True
            return 50

    editor.clock = FiniteClock()
    editor.draw = lambda: None
    monkeypatch.setattr(pygame.event, "get", lambda: [])
    monkeypatch.setattr(pygame.display, "flip", lambda: None)

    editor.run(auto_update=False)

    # Snaps into [100, 500) on the first tick, then 8 * 50ms = 400ms (exactly
    # one full lap of the 400ms-long loop section) brings it back to 100.
    assert editor.playing is True
    assert editor.current_ms == 100


def test_glow_sprites_reuse_quantized_color_surfaces():
    editor = make_editor()

    first = editor._glow_sprite(40, (254, 126, 62))
    second = editor._glow_sprite(40, (255, 127, 63))

    assert first is second


def test_timeline_length_control_fits_beside_transport_buttons_at_min_width():
    # The Length control (input + slider) lives in the timeline's header row
    # now, next to the transport buttons, instead of the top toolbar - this
    # checks it still fits there, without overlap, at the editor's smallest
    # supported window.
    pygame.init()
    screen = pygame.display.set_mode((1024, 720))
    editor = Editor(screen, check_recovery=False, target_fps=30)

    editor.draw()
    _canvas, _panel, timeline = editor.layout()

    transport_rects = [
        rect for rect, action, _label in editor.buttons
        if action in {"step_frame:-1", "step_frame:1"}
    ]
    duration_field = next(rect for rect, action, _label in editor.buttons if action == "edit_duration")
    duration_slider = next(rect for rect, action, _label in editor.buttons if action == "duration_slider")

    assert max(rect.right for rect in transport_rects) < duration_field.x
    assert duration_slider.right <= timeline.right
