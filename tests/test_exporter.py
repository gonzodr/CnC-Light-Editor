from cnc_light_editor.exporter import export_arduino_header
from cnc_light_editor.model import Layer, Project, Shape


def test_export_is_59_led_progmem_header(tmp_path):
    shape = Shape("rectangle", "all", width=1.0, height=1.0, color=(1, 2, 3))
    project = Project(duration_ms=100, fps=10, layers=[Layer("one", shapes=[shape])])
    path = export_arduino_header(project, [(0.5, 0.5)] * 59, tmp_path / "effect.h")
    text = path.read_text(encoding="utf-8")
    assert "CNC_EFFECT_LED_COUNT = 59" in text
    assert "PROGMEM" in text
    assert "1, 2, 3" in text
