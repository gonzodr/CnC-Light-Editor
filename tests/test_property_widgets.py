from cnc_light_editor.property_widgets import (
    LAYER_PROPERTY_SPECS,
    RANDOM_LED_PROPERTY_SPECS,
    SHAPE_PROPERTY_SPECS,
)


def test_shared_property_specs_handle_display_units_and_limits():
    feather = SHAPE_PROPERTY_SPECS["feather"]
    opacity = RANDOM_LED_PROPERTY_SPECS["opacity"]

    assert feather.display_text(0.05) == "5.0%"
    assert feather.from_input(7.5) == 0.075
    assert feather.from_input(50.0) == 0.1
    assert opacity.display_text(0.625) == "62.5%"
    assert opacity.from_input(25.0) == 0.25


def test_shared_property_specs_normalize_integer_and_decimal_parameters():
    count = RANDOM_LED_PROPERTY_SPECS["particle_count"]
    speed = RANDOM_LED_PROPERTY_SPECS["born_speed"]

    assert count.normalize(17.6) == 18
    assert count.normalize(100) == 68
    assert speed.normalize(8.56) == 8.6


def test_layer_opacity_uses_the_shared_percentage_widget_contract():
    spec = LAYER_PROPERTY_SPECS["layer_opacity"]

    assert spec.display_text(0.625) == "62.5%"
    assert spec.from_input(35) == 0.35
    assert spec.from_input(125) == 1.0
