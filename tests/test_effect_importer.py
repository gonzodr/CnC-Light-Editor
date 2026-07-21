from cnc_light_editor.effect_importer import EFFECT_LEDS, load_effect_data, parse_effect_data


def array(name: str, rows: list[list[int]]) -> str:
    values = ",".join(str(value) for row in rows for value in row)
    return f"const uint8_t {name}[] PROGMEM = {{ {values} }};"


def test_parses_firmware_palette_and_68_led_frames():
    row = [0] * EFFECT_LEDS
    row[3] = 9
    effect = parse_effect_data(array("EffectID3", [row]))[0]

    assert effect.duration_ms == 60
    assert len(effect.frames[0]) == 68
    assert effect.frames[0][3] == (148, 0, 211)


def test_ignores_documentation_examples_in_comments():
    row = [0] * EFFECT_LEDS
    source = "// const uint8_t EffectID7[] PROGMEM = { ... };\n" + array("EffectID1", [row])

    assert [effect.name for effect in parse_effect_data(source)] == ["EffectID1"]


def test_expands_pulsing_mask_effect_like_firmware():
    rows = []
    for index in range(4):
        row = [0] * EFFECT_LEDS
        row[index] = 1
        rows.append(row)
    effect = parse_effect_data(array("EffectID2", rows))[0]

    assert len(effect.frames) == 32
    assert effect.frames[0][0] == (255, 0, 0)
    assert effect.frames[1][0] == (0, 0, 0)
    assert effect.frames[8][0] == (255, 165, 0)


def test_loads_effect_data_header_from_file(tmp_path):
    rows = [[0] * EFFECT_LEDS for _ in range(7)]
    path = tmp_path / "effect_data.h"
    path.write_text(array("EffectID1", rows), encoding="utf-8")
    effects = load_effect_data(path)

    assert [effect.name for effect in effects] == ["EffectID1"]
    assert len(effects[0].frames) == 7
