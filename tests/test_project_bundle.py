import pytest

from cnc_light_editor.project_bundle import embedded_project_lines, extract_embedded_projects


def test_embedded_project_round_trip_preserves_unicode_and_nested_data():
    project = {
        "name": "Színátmenetes fény",
        "effect_id": 17,
        "layers": [{"name": "Fő réteg", "shapes": [{"color": [1, 2, 3]}]}],
    }
    source = "\n".join(embedded_project_lines(17, project))

    assert extract_embedded_projects(source) == {17: project}
    assert all(line.startswith("// ") for line in source.splitlines())


def test_embedded_project_rejects_duplicate_effect_association():
    bundle = "\n".join(embedded_project_lines(4, {"name": "one"}))

    with pytest.raises(ValueError, match="Duplicate embedded project"):
        extract_embedded_projects(bundle + "\n" + bundle)


def test_embedded_project_rejects_corrupt_payload():
    source = "\n".join((
        "// CNLIGHT_PROJECT_V1_BEGIN id=8 json=1 compressed=1",
        "// CNLIGHT_PROJECT_V1_DATA not-valid-base64=",
        "// CNLIGHT_PROJECT_V1_END",
    ))

    with pytest.raises(ValueError, match="Invalid embedded project for effect ID 8"):
        extract_embedded_projects(source)
