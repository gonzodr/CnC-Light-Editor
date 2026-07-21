from cnc_light_editor.engine import render_leds
from cnc_light_editor.model import Layer, Project, Shape


def test_keyframes_interpolate_scale_and_color():
    shape = Shape("ellipse", "pulse", width=0.1, color=(0, 0, 0))
    shape.add_keyframe("width", 0, 0.1)
    shape.add_keyframe("width", 1000, 0.5)
    shape.add_keyframe("color", 0, (0, 0, 0))
    shape.add_keyframe("color", 1000, (200, 100, 50))
    assert shape.value_at("width", 500) == 0.3
    assert shape.value_at("color", 500) == (100, 50, 25)


def test_visibility_is_stepped():
    shape = Shape("rectangle", "switch")
    shape.add_keyframe("visible", 100, False)
    shape.add_keyframe("visible", 200, True)
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


def test_atomic_project_round_trip_preserves_color_keyframe_tuple(tmp_path):
    shape = Shape("ellipse", "color pulse")
    shape.add_keyframe("color", 100, (10, 20, 30))
    project = Project(layers=[Layer("color", shapes=[shape])])
    path = tmp_path / "roundtrip.cnclight"

    project.save(path)
    loaded = Project.load(path)

    assert loaded.layers[0].shapes[0].keyframes["color"][0].value == (10, 20, 30)
    assert not (tmp_path / ".roundtrip.cnclight.tmp").exists()
