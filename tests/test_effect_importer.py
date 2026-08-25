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
        '{ 7, "pulse", fx_pulse, 3, 50, 2, 2, 1 },',
        f"const uint8_t fx_pulse[] PROGMEM = {{ {data} }};",
    )

    effect = parse_effect_data(source)[0]

    assert (effect.effect_id, effect.name, effect.symbol) == (7, "pulse", "fx_pulse")
    assert effect.flash_bytes == 3 * 204
    assert effect.overlay is True
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
    assert [effect.overlay for effect in effects] == [False, False]


def test_v4_import_rejects_invalid_overlay_flag():
    values = ",".join("0" for _ in range(EFFECT_LEDS * 3))
    source = v4_header(
        '{ 4, "invalid", fx_invalid, 1, 50, 1, 1, 2 },',
        f"const uint8_t fx_invalid[] PROGMEM = {{ {values} }};",
    )

    with pytest.raises(ValueError, match="overlay must be 0"):
        parse_effect_data(source)


def test_parses_v4_intro_loop_outro_playback():
    intro = [50, 50, 50] * EFFECT_LEDS
    loop_a = [255, 0, 0] * EFFECT_LEDS
    loop_b = [0, 0, 255] * EFFECT_LEDS
    outro = [255, 255, 255] * EFFECT_LEDS
    data = ",".join(map(str, intro + loop_a + loop_b + outro))
    source = v4_header(
        '{ 9, "intro-loop-outro", fx_ilo, 4, 50, 2, 3, 0, 1 },',
        f"const uint8_t fx_ilo[] PROGMEM = {{ {data} }};",
    )

    effect = parse_effect_data(source)[0]

    assert effect.intro_frames == 1
    assert effect.normalized_intro_frames == 1
    # intro plays once at t=0
    assert effect.colors_at(0)[0] == (50, 50, 50)
    # loop region [1,3) repeats twice: 50,100 -> loop_a; 100,150 -> loop_b (wraps)
    assert effect.colors_at(50)[0] == (255, 0, 0)
    assert effect.colors_at(100)[0] == (0, 0, 255)
    assert effect.colors_at(150)[0] == (255, 0, 0)
    assert effect.colors_at(200)[0] == (0, 0, 255)
    # outro plays once after the loop finishes
    assert effect.colors_at(250)[0] == (255, 255, 255)


def test_v4_import_rejects_intro_frames_past_loop_end():
    values = ",".join("0" for _ in range(EFFECT_LEDS * 3))
    source = v4_header(
        '{ 5, "bad-intro", fx_bad_intro, 1, 50, 1, 1, 0, 2 },',
        f"const uint8_t fx_bad_intro[] PROGMEM = {{ {values} }};",
    )

    with pytest.raises(ValueError, match="introFrames"):
        parse_effect_data(source)


def test_imports_far_progmem_firmware_rows_without_legacy_data_pointer():
    red = ",".join(map(str, [255, 0, 0] * EFFECT_LEDS))
    blue = ",".join(map(str, [0, 0, 255] * EFFECT_LEDS))
    source = f"""
    #define FX_DATA_PROGMEM __attribute__((section(".text.fxdata"), used))
    const uint8_t fx_red[] FX_DATA_PROGMEM = {{ {red} }};
    const uint8_t fx_blue[] FX_DATA_PROGMEM = {{ {blue} }};
    static inline uint_farptr_t bakedEffectFarAddress(uint8_t id) {{
      switch (id) {{
        case 9: return pgm_get_far_address(fx_blue);
        case 3: return pgm_get_far_address(fx_red);
        default: return 0;
      }}
    }}
    const EffectDef bakedEffects[] = {{
      {{ 9, "Blue", 1, 50, 1, 1, 0, 0 }},
      {{ 3, "Red", 1, 50, 1, 1, 0, 0 }},
    }};
    """

    effects = parse_effect_data(source)

    assert [(effect.effect_id, effect.symbol) for effect in effects] == [
        (9, "fx_blue"), (3, "fx_red"),
    ]
    assert effects[0].frames[0][0] == (0, 0, 255)
    assert effects[1].frames[0][0] == (255, 0, 0)


def mask_test_header(values: list[int], define: str = "") -> str:
    body = ",".join(str(value) for value in values)
    return (
        f"{define}\n"
        f"const uint8_t fx_one[] PROGMEM = {{ {body} }};\n"
        "const EffectDef bakedEffects[] = {\n"
        '  { 1, "One", fx_one, 1, 50, 1, 1, 0, 0 },\n'
        "};\n"
    )


def test_v4_header_without_a_mask_define_is_read_as_raw_data():
    values = [7] * (EFFECT_LEDS * 3)
    effect = parse_effect_data(mask_test_header(values))[0]

    assert effect.frames[0][0] == (7, 7, 7)


def test_v4_header_with_a_mask_define_is_decoded_back_to_real_rgb():
    values = [7 ^ 0x5A] * (EFFECT_LEDS * 3)
    source = mask_test_header(values, "#define FX_DATA_MASK_APPLIED 0x5A")

    assert parse_effect_data(source)[0].frames[0][0] == (7, 7, 7)


def test_a_commented_out_mask_define_does_not_scramble_the_import():
    values = [7] * (EFFECT_LEDS * 3)
    source = mask_test_header(values, "// #define FX_DATA_MASK_APPLIED 0x5A")

    assert parse_effect_data(source)[0].frames[0][0] == (7, 7, 7)


def test_an_out_of_range_mask_define_is_rejected():
    values = [7] * (EFFECT_LEDS * 3)
    source = mask_test_header(values, "#define FX_DATA_MASK_APPLIED 300")

    with pytest.raises(ValueError, match="between 0 and 255"):
        parse_effect_data(source)
