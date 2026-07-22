from __future__ import annotations

import base64
import binascii
import json
import re
import zlib


BUNDLE_FORMAT = "cnc-light-project"
BUNDLE_VERSION = 1
MAX_PROJECT_JSON_BYTES = 2 * 1024 * 1024
DATA_CHUNK_SIZE = 100


def embedded_project_lines(effect_id: int, project_data: dict) -> list[str]:
    envelope = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "project": project_data,
    }
    raw = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressed = zlib.compress(raw, level=9)
    payload = base64.b64encode(compressed).decode("ascii")
    lines = [
        f"// CNLIGHT_PROJECT_V1_BEGIN id={effect_id} json={len(raw)} compressed={len(compressed)}",
    ]
    lines.extend(
        f"// CNLIGHT_PROJECT_V1_DATA {payload[offset:offset + DATA_CHUNK_SIZE]}"
        for offset in range(0, len(payload), DATA_CHUNK_SIZE)
    )
    lines.append("// CNLIGHT_PROJECT_V1_END")
    return lines


def extract_embedded_projects(source: str) -> dict[int, dict]:
    projects: dict[int, dict] = {}
    active_id: int | None = None
    chunks: list[str] = []
    for line in source.splitlines():
        comment = re.match(r"\s*//\s*(.*?)\s*$", line)
        if not comment:
            continue
        content = comment.group(1)
        begin = re.fullmatch(r"CNLIGHT_PROJECT_V1_BEGIN\s+id=(\d+)(?:\s+.*)?", content)
        if begin:
            if active_id is not None:
                raise ValueError("Nested CnC Light project bundle")
            active_id = int(begin.group(1))
            chunks = []
            continue
        data = re.fullmatch(r"CNLIGHT_PROJECT_V1_DATA\s+([A-Za-z0-9+/=]+)", content)
        if data and active_id is not None:
            chunks.append(data.group(1))
            continue
        if content == "CNLIGHT_PROJECT_V1_END" and active_id is not None:
            if active_id in projects:
                raise ValueError(f"Duplicate embedded project for effect ID {active_id}")
            projects[active_id] = _decode_project(active_id, "".join(chunks))
            active_id = None
            chunks = []
    if active_id is not None:
        raise ValueError(f"Unterminated embedded project for effect ID {active_id}")
    return projects


def _decode_project(effect_id: int, payload: str) -> dict:
    try:
        compressed = base64.b64decode(payload, validate=True)
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(compressed, MAX_PROJECT_JSON_BYTES + 1)
        if len(raw) > MAX_PROJECT_JSON_BYTES or decompressor.unconsumed_tail:
            raise ValueError("project JSON exceeds safety limit")
        raw += decompressor.flush(MAX_PROJECT_JSON_BYTES + 1 - len(raw))
        if len(raw) > MAX_PROJECT_JSON_BYTES:
            raise ValueError("project JSON exceeds safety limit")
        if not decompressor.eof or decompressor.unused_data:
            raise ValueError("project payload is incomplete or has trailing data")
        envelope = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, zlib.error, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"Invalid embedded project for effect ID {effect_id}: {error}") from error
    if not isinstance(envelope, dict):
        raise ValueError(f"Invalid embedded project for effect ID {effect_id}: envelope must be an object")
    if envelope.get("format") != BUNDLE_FORMAT or envelope.get("version") != BUNDLE_VERSION:
        raise ValueError(f"Unsupported embedded project format for effect ID {effect_id}")
    project = envelope.get("project")
    if not isinstance(project, dict):
        raise ValueError(f"Invalid embedded project for effect ID {effect_id}: project must be an object")
    return project
