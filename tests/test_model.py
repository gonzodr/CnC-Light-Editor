import json

from cnc_light_editor.engine import render_leds
from cnc_light_editor.model import GradientStop, Layer, Project, RandomLedEffect, Shape


def test_keyframes_interpolate_scale_and_color():
    shape = Shape("ellipse", "pulse", width=0.1, color=(0, 0, 0))
    shape.add_keyframe("width", 0, 0.1)
    shape.add_keyframe("width", 1000, 0.5)
    shape.add_keyframe("color", 0, (0, 0, 0))
    shape.add_keyframe("color", 1000, (200, 100, 50))
    assert shape.value_at("width", 500) == 0.3
    assert shape.value_at("color", 500) == (100, 50, 25)


def test_first_keyframe_holds_back_to_timeline_start_for_every_target_type():
    shape = Shape(
        "ellipse", "future state", x=0.1, color=(1, 2, 3), visible=True,
    )
    shape.add_keyframe("x", 800, 0.75)
    shape.add_keyframe("color", 800, (120, 80, 40))
    shape.add_keyframe("visible", 800, False)
    effect = RandomLedEffect(enabled=False)
    effect.add_keyframe("enabled", 600, True)
    canvas = Layer("Canvas", is_canvas=True, canvas_enabled=False)
    canvas.add_keyframe("canvas_enabled", 500, True)

    assert shape.value_at("x", 0) == 0.75
    assert shape.value_at("color", 400) == (120, 80, 40)
    assert shape.value_at("visible", 799) is False
    assert shape.value_at("width", 0) == shape.width
    assert effect.value_at("enabled", 0) is True
    assert canvas.value_at("canvas_enabled", 0) is True


def test_rotation_turns_preserve_full_revolutions_during_interpolation():
    shape = Shape("rectangle", "spinner")
    shape.add_keyframe("rotation", 0, 0.0)
    shape.add_keyframe("rotation", 1000, 0.0)
    shape.add_keyframe("rotation_turns", 0, 0.0)
    shape.add_keyframe("rotation_turns", 1000, 2.0)

    assert shape.state_at(250)["rotation_total"] == 180.0
    assert shape.state_at(500)["rotation_total"] == 360.0
    assert shape.state_at(1000)["rotation_total"] == 720.0


def test_rotation_turns_round_trip_with_project(tmp_path):
    shape = Shape("triangle", "spinner", rotation=15.0, rotation_turns=1.0)
    shape.add_keyframe("rotation", 1000, 0.0)
    shape.add_keyframe("rotation_turns", 1000, 2.0)
    project = Project(layers=[Layer("rotation", shapes=[shape])])
    path = tmp_path / "rotation.cnclight"

    project.save(path)
    loaded = Project.load(path)
    loaded_shape = loaded.layers[0].shapes[0]

    assert loaded_shape.rotation_turns == 1.0
    assert loaded_shape.state_at(1000)["rotation_total"] == 720.0


def test_visibility_is_stepped():
    shape = Shape("rectangle", "switch")
    shape.add_keyframe("visible", 100, False)
    shape.add_keyframe("visible", 200, True)
    assert shape.value_at("visible", 0) is False
    assert shape.value_at("visible", 150) is False


def test_layer_composition_reaches_led():
    shape = Shape("rectangle", "fill", width=1.0, height=1.0, color=(100, 50, 20))
    project = Project(layers=[Layer("base", shapes=[shape])])
    assert render_leds(project, [(0.5, 0.5)], 0) == [(100, 50, 20)]


def test_hidden_layer_does_not_reach_led():
    shape = Shape("ellipse", "fill", width=1.0, height=1.0, color=(255, 0, 0))
    project = Project(layers=[Layer("hidden", visible=False, shapes=[shape])])
    assert render_leds(project, [(0.5, 0.5)], 0) == [(0, 0, 0)]


def test_easing_changes_interpolation_curve():
    shape = Shape("ellipse", "ease", x=0.0)
    shape.add_keyframe("x", 0, 0.0)
    shape.add_keyframe("x", 1000, 1.0)
    shape.keyframes["x"][1].easing = "ease_in"
    assert shape.value_at("x", 500) == 0.25

    shape.keyframes["x"][1].easing = "ease_out"
    assert shape.value_at("x", 500) == 0.75

    shape.keyframes["x"][1].easing = "ease_in_out"
    assert shape.value_at("x", 500) == 0.5


def test_custom_bezier_easing_supports_linear_and_overshoot_curves():
    shape = Shape("ellipse", "bezier", x=0.0)
    shape.add_keyframe("x", 0, 0.0)
    shape.add_keyframe("x", 1000, 1.0)
    destination = shape.keyframes["x"][1]
    destination.easing = "bezier"
    destination.bezier = (0.0, 0.0, 1.0, 1.0)
    assert abs(shape.value_at("x", 500) - 0.5) < 0.00001

    destination.bezier = (0.34, 1.56, 0.64, 1.0)
    assert shape.value_at("x", 500) > 1.0


def test_project_round_trip_preserves_custom_bezier_controls(tmp_path):
    shape = Shape("ellipse", "bezier")
    shape.add_keyframe("opacity", 0, 0.0)
    shape.add_keyframe("opacity", 1000, 1.0)
    shape.keyframes["opacity"][1].easing = "bezier"
    shape.keyframes["opacity"][1].bezier = (0.2, -0.4, 0.7, 1.6)
    project = Project(layers=[Layer("bezier", shapes=[shape])])
    path = tmp_path / "bezier.cnclight"

    project.save(path)
    loaded = Project.load(path)
    frame = loaded.layers[0].shapes[0].keyframes["opacity"][1]

    assert frame.easing == "bezier"
    assert frame.bezier == (0.2, -0.4, 0.7, 1.6)


def test_stroked_shape_only_reaches_border_leds():
    shape = Shape(
        "rectangle", "outline", x=0.5, y=0.5, width=0.6, height=0.6,
        color=(120, 80, 40), fill_mode="stroke", stroke_width=0.05,
    )
    project = Project(layers=[Layer("outline", shapes=[shape])])
    assert render_leds(project, [(0.5, 0.5), (0.78, 0.5)], 0) == [
        (0, 0, 0), (120, 80, 40),
    ]


def test_opacity_is_precomposited_into_rgb():
    shape = Shape(
        "rectangle", "half", width=1.0, height=1.0,
        color=(200, 100, 40), opacity=0.5,
    )
    project = Project(layers=[Layer("opacity", shapes=[shape])])
    assert render_leds(project, [(0.5, 0.5)], 0) == [(100, 50, 20)]


def test_mask_expansion_changes_led_coverage_in_export_renderer():
    expanded = Shape(
        "rectangle", "expanded", width=0.2, height=0.2,
        color=(200, 100, 40), mask_expansion=0.02,
    )
    contracted = Shape(
        "rectangle", "contracted", width=0.2, height=0.2,
        color=(200, 100, 40), mask_expansion=-0.02,
    )

    assert render_leds(Project(layers=[Layer("expanded", shapes=[expanded])]), [(0.61, 0.5)], 0) == [
        (200, 100, 40),
    ]
    assert render_leds(Project(layers=[Layer("contracted", shapes=[contracted])]), [(0.59, 0.5)], 0) == [
        (0, 0, 0),
    ]


def test_feather_produces_partial_led_brightness_at_mask_edge():
    shape = Shape(
        "rectangle", "soft", width=0.2, height=0.2,
        color=(200, 100, 40), feather=0.04,
    )
    project = Project(layers=[Layer("soft", shapes=[shape])])

    assert render_leds(project, [(0.6, 0.5)], 0) == [(100, 50, 20)]


def test_feather_and_mask_expansion_are_keyframeable_and_persisted(tmp_path):
    shape = Shape("ellipse", "soft")
    shape.add_keyframe("feather", 0, 0.0)
    shape.add_keyframe("feather", 1000, 0.04)
    shape.add_keyframe("mask_expansion", 0, -0.02)
    shape.add_keyframe("mask_expansion", 1000, 0.02)
    path = tmp_path / "mask.cnclight"
    Project(layers=[Layer("mask", shapes=[shape])]).save(path)

    loaded = Project.load(path).layers[0].shapes[0]

    assert loaded.state_at(500)["feather"] == 0.02
    assert loaded.state_at(500)["mask_expansion"] == 0.0


def test_atomic_project_round_trip_preserves_color_keyframe_tuple(tmp_path):
    shape = Shape("ellipse", "color pulse")
    shape.add_keyframe("color", 100, (10, 20, 30))
    project = Project(layers=[Layer("color", shapes=[shape])])
    path = tmp_path / "roundtrip.cnclight"

    project.save(path)
    loaded = Project.load(path)

    assert loaded.layers[0].shapes[0].keyframes["color"][0].value == (10, 20, 30)
    assert not (tmp_path / ".roundtrip.cnclight.tmp").exists()


def test_v4_metadata_and_memory_estimates_round_trip(tmp_path):
    project = Project(
        "loop with outro", duration_ms=5000, effect_id=8, frame_ms=50,
        loops=5, loop_frames=20, overlay=True,
    )
    path = tmp_path / "metadata.cnclight"

    project.save(path)
    loaded = Project.load(path)

    assert loaded.effect_id == 8
    assert loaded.frame_ms == 50
    assert loaded.overlay is True
    assert loaded.actual_fps == 20
    assert loaded.stored_frame_count == 100
    assert loaded.flash_bytes == 20400
    assert loaded.firmware_playback_ms == 9000


def test_canvas_layer_enabled_state_is_stepped_keyframeable_and_persisted(tmp_path):
    canvas = Layer("Canvas", is_canvas=True, canvas_enabled=False)
    canvas.add_keyframe("canvas_enabled", 0, False)
    canvas.add_keyframe("canvas_enabled", 500, True)
    project = Project(overlay=True, layers=[Layer("Art"), canvas])
    path = tmp_path / "canvas-layer.cnclight"

    project.save(path)
    loaded = Project.load(path)

    assert loaded.canvas_transparency_at(250) is False
    assert loaded.canvas_transparency_at(500) is True
    assert loaded.layers[1].is_canvas is True
    assert [frame.time_ms for frame in loaded.layers[1].keyframes["canvas_enabled"]] == [0, 500]


def test_old_project_fps_is_migrated_to_integer_frame_ms(tmp_path):
    path = tmp_path / "legacy.cnclight"
    path.write_text(json.dumps({"name": "old", "fps": 30, "layers": []}), encoding="utf-8")

    loaded = Project.load(path)

    assert loaded.frame_ms == 33
    assert round(loaded.actual_fps, 2) == 30.30


def test_linear_gradient_is_shared_by_led_renderer():
    shape = Shape(
        "rectangle", "gradient", width=1.0, height=1.0,
        gradient_type="linear", gradient_angle=0,
        gradient_stops=[GradientStop(0.0, (0, 0, 0)), GradientStop(1.0, (200, 100, 0))],
    )
    project = Project(layers=[Layer("gradient", shapes=[shape])])

    assert render_leds(project, [(0.25, 0.5), (0.75, 0.5)], 0) == [
        (50, 25, 0), (150, 75, 0),
    ]


def test_radial_gradient_runs_from_center_to_shape_edge():
    shape = Shape(
        "ellipse", "radial", width=1.0, height=1.0,
        gradient_type="radial",
        gradient_stops=[GradientStop(0.0, (255, 255, 255)), GradientStop(1.0, (0, 0, 0))],
    )
    project = Project(layers=[Layer("gradient", shapes=[shape])])

    assert render_leds(project, [(0.5, 0.5), (1.0, 0.5)], 0) == [
        (255, 255, 255), (0, 0, 0),
    ]


def test_angular_radial_gradient_runs_clockwise_around_shape_center():
    shape = Shape(
        "ellipse", "angular", width=1.0, height=1.0,
        gradient_type="radial", gradient_radial_mode="angular",
        gradient_stops=[GradientStop(0.0, (0, 0, 0)), GradientStop(1.0, (200, 0, 0))],
    )
    project = Project(layers=[Layer("gradient", shapes=[shape])])

    assert render_leds(
        project,
        [(1.0, 0.5), (0.5, 1.0), (0.0, 0.5), (0.5, 0.0)],
        0,
    ) == [(0, 0, 0), (50, 0, 0), (100, 0, 0), (150, 0, 0)]


def test_angular_radial_gradient_angle_sets_start_phase():
    shape = Shape(
        "ellipse", "angular", width=1.0, height=1.0,
        gradient_type="radial", gradient_radial_mode="angular", gradient_angle=90,
        gradient_stops=[GradientStop(0.0, (0, 0, 0)), GradientStop(1.0, (200, 0, 0))],
    )
    project = Project(layers=[Layer("gradient", shapes=[shape])])

    assert render_leds(project, [(0.5, 1.0)], 0) == [(0, 0, 0)]


def test_project_round_trip_preserves_gradient_stops(tmp_path):
    stop = GradientStop(0.35, (12, 34, 56))
    shape = Shape(
        "rectangle", "gradient", gradient_type="radial",
        gradient_radial_mode="angular", gradient_stops=[stop],
    )
    project = Project(layers=[Layer("gradient", shapes=[shape])])
    path = tmp_path / "gradient.cnclight"

    project.save(path)
    loaded = Project.load(path)

    loaded_stop = loaded.layers[0].shapes[0].gradient_stops[0]
    assert loaded_stop.position == 0.35
    assert loaded_stop.color == (12, 34, 56)
    assert loaded_stop.id == stop.id
    assert loaded.layers[0].shapes[0].gradient_radial_mode == "angular"


def test_random_led_effect_ignores_layer_shape_mask_and_is_deterministic():
    effect = RandomLedEffect(seed=42, color=(200, 80, 20), life_ms=250, born_speed=8)
    project = Project(layers=[Layer("empty layer", effects=[effect])])
    points = [(0.1, 0.1), (0.5, 0.5), (0.9, 0.9)]

    first = render_leds(project, points, 0)
    second = render_leds(project, points, 0)

    assert first == second
    assert (200, 80, 20) in first
    assert sum(color != (0, 0, 0) for color in first) == 1


def test_random_led_enabled_can_be_keyframed_off():
    effect = RandomLedEffect(seed=1, color=(255, 255, 255))
    effect.add_keyframe("enabled", 0, True)
    effect.add_keyframe("enabled", 100, False)
    project = Project(layers=[Layer("fx", effects=[effect])])

    assert any(color != (0, 0, 0) for color in render_leds(project, [(0.2, 0.2), (0.8, 0.8)], 0))
    assert render_leds(project, [(0.2, 0.2), (0.8, 0.8)], 200) == [(0, 0, 0), (0, 0, 0)]


def test_random_led_opacity_is_interpolated_and_applied_to_rendering():
    effect = RandomLedEffect(seed=42, color=(200, 80, 20), opacity=1.0)
    effect.add_keyframe("opacity", 0, 1.0)
    effect.add_keyframe("opacity", 100, 0.0)
    project = Project(layers=[Layer("fx", effects=[effect])])

    assert effect.value_at("opacity", 50) == 0.5
    assert (80, 32, 8) in render_leds(project, [(0.2, 0.2), (0.8, 0.8)], 50)
    assert render_leds(project, [(0.2, 0.2), (0.8, 0.8)], 100) == [(0, 0, 0), (0, 0, 0)]


def test_project_round_trip_preserves_random_led_settings(tmp_path):
    effect = RandomLedEffect(
        seed=99, life_ms=400, born_speed=12.5, particle_count=17,
        color=(4, 5, 6), opacity=0.65,
    )
    effect.add_keyframe("enabled", 700, False)
    project = Project(layers=[Layer("fx", effects=[effect])])
    path = tmp_path / "random-led.cnclight"

    project.save(path)
    loaded = Project.load(path)
    loaded_effect = loaded.layers[0].effects[0]

    assert (loaded_effect.seed, loaded_effect.life_ms, loaded_effect.born_speed) == (99, 400, 12.5)
    assert loaded_effect.particle_count == 17
    assert loaded_effect.color == (4, 5, 6)
    assert loaded_effect.opacity == 0.65
    assert loaded_effect.value_at("enabled", 800) is False
