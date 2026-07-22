from cnc_light_editor.autosave import AutosaveManager


def test_autosave_rotates_and_returns_latest_valid_snapshot(tmp_path):
    manager = AutosaveManager(tmp_path / "autosave", slots=3)
    manager.write({"name": "first", "layers": []}, None)
    manager.write({"name": "second", "layers": []}, tmp_path / "source.cnclight")

    record = manager.latest()
    assert record is not None
    assert record.project_data["name"] == "second"
    assert record.source_path == (tmp_path / "source.cnclight").resolve()
    assert len(list((tmp_path / "autosave").glob("*.json"))) == 2


def test_autosave_skips_corrupt_newest_slot_and_can_be_cleared(tmp_path):
    manager = AutosaveManager(tmp_path / "autosave", slots=3)
    manager.write({"name": "recoverable", "layers": []}, None)
    manager.write({"name": "newest", "layers": []}, None)
    (tmp_path / "autosave" / "recovery-0.cnclight.autosave.json").write_text(
        "not json", encoding="utf-8",
    )

    record = manager.latest()
    assert record is not None
    assert record.project_data["name"] == "recoverable"

    manager.clear()
    assert manager.latest() is None
