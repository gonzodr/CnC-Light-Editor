from __future__ import annotations

import json

from cnc_light_editor import bridge


def test_bridge_request_roundtrip(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    monkeypatch.setattr(bridge, "INBOX_DIR", inbox)
    monkeypatch.setattr(bridge, "OUTBOX_DIR", outbox)

    request_id = bridge.submit("seek", time_ms=1250)
    requests = bridge.read_requests()
    assert len(requests) == 1
    path, request = requests[0]
    assert request == {"id": request_id, "command": "seek", "time_ms": 1250}

    bridge.complete(path, request_id, ok=True, message="ready")
    assert not path.exists()
    reply = bridge.wait_for_reply(request_id, timeout=0.1)
    assert reply == {"id": request_id, "ok": True, "message": "ready"}
    assert not (outbox / f"{request_id}.json").exists()


def test_bridge_reports_malformed_json(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setattr(bridge, "INBOX_DIR", inbox)
    (inbox / "broken.json").write_text("{nope", encoding="utf-8")

    path, request = bridge.read_requests()[0]
    assert path.name == "broken.json"
    assert request["command"] == "invalid"
    assert request["id"] == "broken"
    assert request["error"]
