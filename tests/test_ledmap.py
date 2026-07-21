from cnc_light_editor.ledmap import Led, LedMap


def make_map() -> LedMap:
    return LedMap(100, 200, [
        Led(id=0, firmware_index=0, x=10, y=20),
        Led(id=1, firmware_index=1, x=80, y=160),
    ])


def test_map_validates_contiguous_indices():
    assert make_map().validate() == []


def test_index_assignment_swaps_existing_index():
    led_map = make_map()
    led_map.set_firmware_index(0, 1)
    assert [led.firmware_index for led in led_map.leds] == [1, 0]
    assert led_map.validate() == []


def test_firmware_order_controls_export_point_order():
    led_map = make_map()
    led_map.set_firmware_index(0, 1)
    assert led_map.normalized_points(firmware_order=True) == [(0.8, 0.8), (0.1, 0.1)]


def test_round_trip_preserves_mapping(tmp_path):
    led_map = make_map()
    path = tmp_path / "led_map.json"
    led_map.save(path)
    loaded = LedMap.load(path)
    assert loaded == led_map
