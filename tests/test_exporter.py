from cnc_light_editor.exporter import export_arduino_header
from cnc_light_editor.model import Layer, Project, Shape


def test_export_pads_playfield_map_to_68_leds(tmp_path):
    shape = Shape("rectangle", "all", width=1.0, height=1.0, color=(1, 2, 3))
    project = Project(duration_ms=100, fps=10, layers=[Layer("one", shapes=[shape])])
    path = export_arduino_header(project, [(0.5, 0.5)] * 59, tmp_path / "effect.h")
    text = path.read_text(encoding="utf-8")
    assert "CNC_EFFECT_LED_COUNT = 68" in text
    assert "PROGMEM" in text

    frame_line = next(line for line in text.splitlines() if line.startswith("  { "))
    values = [int(value.strip()) for value in frame_line.strip(" {},").split(",")]
    assert len(values) == 68 * 3
    assert values[:3] == [1, 2, 3]
    assert values[-9 * 3:] == [0] * (9 * 3)


def test_null_slot_exports_black_rgb(tmp_path):
    shape = Shape("rectangle", "all", width=1.0, height=1.0, color=(10, 20, 30))
    project = Project(duration_ms=100, fps=10, layers=[Layer("one", shapes=[shape])])
    slots = [(0.5, 0.5)] * 59
    slots[7] = None
    path = export_arduino_header(project, slots, tmp_path / "effect.h")
    frame_line = next(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("  { ")
    )
    values = [int(value.strip()) for value in frame_line.strip(" {},").split(",")]
    assert values[6 * 3:7 * 3] == [10, 20, 30]
    assert values[7 * 3:8 * 3] == [0, 0, 0]
    assert values[8 * 3:9 * 3] == [10, 20, 30]
