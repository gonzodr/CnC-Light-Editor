from cnc_light_editor.ui_settings import UiSettingsStore


def test_ui_settings_round_trip_and_invalid_file_fallback(tmp_path):
    path = tmp_path / "editor_settings.json"
    store = UiSettingsStore(path)

    assert store.load() == {}
    store.save({"timeline_height": 344})
    assert store.load() == {"timeline_height": 344}

    path.write_text("not-json", encoding="utf-8")
    assert store.load() == {}
