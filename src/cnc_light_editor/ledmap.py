from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass
class Led:
    id: int
    firmware_index: int
    x: float
    y: float
    name: str = ""


@dataclass
class LedMap:
    width: int
    height: int
    leds: list[Led]
    mapping_status: str = "provisional_spatial_order"
    schema_version: int = 1
    notes: str = ""

    @classmethod
    def load(cls, path: str | Path) -> "LedMap":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        canvas = raw["canvas"]
        return cls(
            width=canvas["width"],
            height=canvas["height"],
            leds=[
                Led(
                    id=led["id"],
                    firmware_index=led["firmware_index"],
                    x=led["x"],
                    y=led["y"],
                    name=led.get("name", f"LED {led['id']:02d}"),
                )
                for led in raw["leds"]
            ],
            mapping_status=raw.get("mapping_status", "provisional_spatial_order"),
            schema_version=raw.get("schema_version", 1),
            notes=raw.get("notes", ""),
        )

    def save(self, path: str | Path) -> None:
        raw = {
            "schema_version": self.schema_version,
            "canvas": {"width": self.width, "height": self.height},
            "mapping_status": self.mapping_status,
            "notes": self.notes,
            "leds": [
                {
                    "id": led.id,
                    "firmware_index": led.firmware_index,
                    "x": led.x,
                    "y": led.y,
                    "name": led.name,
                }
                for led in self.leds
            ],
        }
        Path(path).write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    def normalized_points(self, firmware_order: bool = False) -> list[tuple[float, float]]:
        leds = sorted(self.leds, key=lambda led: led.firmware_index) if firmware_order else self.leds
        return [(led.x / self.width, led.y / self.height) for led in leds]

    def export_slots(self) -> list[tuple[float, float] | None]:
        slots: list[tuple[float, float] | None] = [None] * len(self.leds)
        for led in self.leds:
            if led.name.strip().upper() != "NULL":
                slots[led.firmware_index] = (led.x / self.width, led.y / self.height)
        return slots

    def validate(self, require_contiguous: bool = True) -> list[str]:
        errors: list[str] = []
        ids = [led.id for led in self.leds]
        indices = [led.firmware_index for led in self.leds]
        if len(ids) != len(set(ids)):
            errors.append("LED ids must be unique")
        if len(indices) != len(set(indices)):
            errors.append("Firmware indices must be unique")
        if any(index < 0 for index in indices):
            errors.append("Firmware indices cannot be negative")
        if require_contiguous and sorted(indices) != list(range(len(self.leds))):
            errors.append(f"Firmware indices must cover 0..{len(self.leds) - 1}")
        return errors

    def set_firmware_index(self, led_id: int, firmware_index: int, swap: bool = True) -> None:
        selected = next(led for led in self.leds if led.id == led_id)
        other = next((led for led in self.leds if led.firmware_index == firmware_index), None)
        if swap and other is not None and other is not selected:
            other.firmware_index = selected.firmware_index
        selected.firmware_index = firmware_index
