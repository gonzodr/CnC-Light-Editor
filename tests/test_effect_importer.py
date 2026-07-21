import pytest

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


def v4_header(rows: str, arrays: str) -> str:
    return f"""
    {arrays}
    const EffectDef bakedEffects[] = {{
      {rows}
    }};
    """


def test_parses_v4_rgb_metadata_and_loop_outro_playback():
    red = [255, 0, 0] * EFFECT_LEDS
    blue = [0, 0, 255] * EFFECT_LEDS
    white = [255, 255, 255] * EFFECT_LEDS
    data = ",".join(map(str, red + blue + white))
    source = v4_header(
        '{ 7, "pulse", fx_pulse, 3, 50, 2, 2 },',
        f"const uint8_t fx_pulse[] PROGMEM = {{ {data} }};",
    )

    effect = parse_effect_data(source)[0]

    assert (effect.effect_id, effect.name, effect.symbol) == (7, "pulse", "fx_pulse")
    assert effect.flash_bytes == 3 * 204
    assert effect.duration_ms == 250
    assert effect.colors_at(0)[0] == (255, 0, 0)
    assert effect.colors_at(50)[0] == (0, 0, 255)
    assert effect.colors_at(100)[0] == (255, 0, 0)
    assert effect.colors_at(200)[0] == (255, 255, 255)


def test_v4_import_rejects_duplicate_ids():
    values = ",".join("0" for _ in range(EFFECT_LEDS * 3))
    source = v4_header(
        '{ 1, "one", fx_one, 1, 50, 1, 1 }, { 1, "two", fx_two, 1, 50, 1, 1 },',
        (
            f"const uint8_t fx_one[] PROGMEM = {{ {values} }};"
            f"const uint8_t fx_two[] PROGMEM = {{ {values} }};"
        ),
    )

    with pytest.raises(ValueError, match="Duplicate effect ID 1"):
        parse_effect_data(source)


def test_v4_import_validates_exact_rgb_data_length():
    source = v4_header(
        '{ 2, "short", fx_short, 1, 50, 1, 1 },',
        "const uint8_t fx_short[] PROGMEM = { 1, 2, 3 };",
    )

    with pytest.raises(ValueError, match="3 data bytes found, expected 204"):
        parse_effect_data(source)


def test_v4_import_uses_explicit_id_not_table_position():
    values = ",".join("0" for _ in range(EFFECT_LEDS * 3))
    source = v4_header(
        '{ 9, "later", fx_later, 1, 40, 1, 0 }, { 3, "earlier", fx_earlier, 1, 50, 1, 0 },',
        (
            f"const uint8_t fx_later[] PROGMEM = {{ {values} }};"
            f"const uint8_t fx_earlier[] PROGMEM = {{ {values} }};"
        ),
    )

    effects = parse_effect_data(source)

    assert [effect.effect_id for effect in effects] == [9, 3]
    assert [effect.name for effect in effects] == ["later", "earlier"]
