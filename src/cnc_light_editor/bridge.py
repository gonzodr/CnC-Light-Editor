from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
BRIDGE_DIR = ROOT / "projects" / ".bridge"
INBOX_DIR = BRIDGE_DIR / "inbox"
OUTBOX_DIR = BRIDGE_DIR / "outbox"


def submit(command: str, **payload: Any) -> str:
    """Atomically enqueue one command for the running editor."""
    request_id = uuid4().hex
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    request = {"id": request_id, "command": command, **payload}
    target = INBOX_DIR / f"{request_id}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(request, indent=2), encoding="utf-8")
    temporary.replace(target)
    return request_id


def read_requests() -> list[tuple[Path, dict[str, Any]]]:
    """Read complete requests; malformed files are returned as error commands."""
    if not INBOX_DIR.is_dir():
        return []
    requests: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(INBOX_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime_ns):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("request must be a JSON object")
        except (OSError, json.JSONDecodeError, ValueError) as error:
            value = {"id": path.stem, "command": "invalid", "error": str(error)}
        requests.append((path, value))
    return requests


def complete(path: Path, request_id: str, *, ok: bool, message: str) -> None:
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTBOX_DIR / f"{request_id}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"id": request_id, "ok": ok, "message": message}, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)
    path.unlink(missing_ok=True)


def wait_for_reply(request_id: str, timeout: float = 3.0) -> dict[str, Any] | None:
    target = OUTBOX_DIR / f"{request_id}.json"
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if target.is_file():
            reply = json.loads(target.read_text(encoding="utf-8"))
            target.unlink(missing_ok=True)
            return reply
        time.sleep(0.05)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Control a running CnC Light Editor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    load = subparsers.add_parser("load", help="Load a .cnclight project")
    load.add_argument("path", type=Path)
    load.add_argument("--play", action="store_true")
    bank = subparsers.add_parser("bank", help="Map an effect_data.h and open the Effect Bank")
    bank.add_argument("path", type=Path)
    subparsers.add_parser("play", help="Play the current project")
    subparsers.add_parser("stop", help="Stop playback")
    seek = subparsers.add_parser("seek", help="Move the playhead")
    seek.add_argument("time_ms", type=int)
    args = parser.parse_args()

    payload: dict[str, Any] = {}
    if args.command == "load":
        payload = {"path": str(args.path.resolve()), "play": bool(args.play)}
    elif args.command == "bank":
        payload = {"path": str(args.path.resolve())}
    elif args.command == "seek":
        payload = {"time_ms": args.time_ms}
    request_id = submit(args.command, **payload)
    reply = wait_for_reply(request_id)
    if reply is None:
        raise SystemExit(
            "Bridge request queued, but no running editor replied. Start or restart the editor."
        )
    print(reply["message"])
    if not reply.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
