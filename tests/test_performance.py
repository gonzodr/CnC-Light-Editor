import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from cnc_light_editor.app import Editor
from cnc_light_editor.performance import recommended_preview_fps


def make_editor() -> Editor:
    pygame.init()
    screen = pygame.display.get_surface()
    if screen is None or screen.get_size() != (1280, 1024):
        screen = pygame.display.set_mode((1280, 1024))
    return Editor(screen, check_recovery=False)


def test_preview_fps_adapts_to_pi_model_without_changing_project_fps():
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


def test_glow_sprites_reuse_quantized_color_surfaces():
    editor = make_editor()

    first = editor._glow_sprite(40, (254, 126, 62))
    second = editor._glow_sprite(40, (255, 127, 63))

    assert first is second


def test_compact_toolbar_keeps_actions_clear_of_duration_control():
    pygame.init()
    screen = pygame.display.set_mode((1024, 720))
    editor = Editor(screen, check_recovery=False, target_fps=30)

    editor.draw()

    main_actions = {"play", "keyframe", "stencil", "calibration", "save", "load", "export"}
    action_rects = [rect for rect, action, _label in editor.buttons if action in main_actions]
    assert max(rect.right for rect in action_rects) < editor._duration_input_rect().x
