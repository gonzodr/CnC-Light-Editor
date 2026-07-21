from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

Color = tuple[int, int, int]


@dataclass
class Keyframe:
    time_ms: int
    value: Any
    easing: str = "linear"


@dataclass
class Shape:
    kind: str
    name: str
    x: float = 0.5
    y: float = 0.5
    width: float = 0.2
    height: float = 0.2
    rotation: float = 0.0
    color: Color = (255, 90, 20)
    opacity: float = 1.0
    visible: bool = True
    id: str = field(default_factory=lambda: uuid4().hex[:10])
    keyframes: dict[str, list[Keyframe]] = field(default_factory=dict)

    def add_keyframe(self, prop: str, time_ms: int, value: Any) -> None:
        frames = self.keyframes.setdefault(prop, [])
        frames[:] = [frame for frame in frames if frame.time_ms != time_ms]
        frames.append(Keyframe(int(time_ms), value))
        frames.sort(key=lambda frame: frame.time_ms)

    def value_at(self, prop: str, time_ms: int) -> Any:
        base = getattr(self, prop)
        frames = self.keyframes.get(prop, [])
        if not frames or time_ms < frames[0].time_ms:
            return base
        before = frames[0]
        after = None
        for frame in frames[1:]:
            if frame.time_ms > time_ms:
                after = frame
                break
            before = frame
        if after is None or prop == "visible":
            return before.value
        span = after.time_ms - before.time_ms
        amount = 0.0 if span == 0 else (time_ms - before.time_ms) / span
        return _lerp(before.value, after.value, amount)

    def state_at(self, time_ms: int) -> dict[str, Any]:
        return {prop: self.value_at(prop, time_ms) for prop in (
            "x", "y", "width", "height", "rotation", "color", "opacity", "visible"
        )}


@dataclass
class Layer:
    name: str
    visible: bool = True
    shapes: list[Shape] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex[:10])


@dataclass
class Project:
    name: str = "Untitled effect"
    duration_ms: int = 5000
    fps: int = 30
    width: int = 3043
    height: int = 6305
    layers: list[Layer] = field(default_factory=lambda: [Layer("Layer 1")])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        layers: list[Layer] = []
        for layer_raw in raw.pop("layers", []):
            shapes: list[Shape] = []
            for shape_raw in layer_raw.pop("shapes", []):
                keyframes = {
                    prop: [Keyframe(**frame) for frame in frames]
                    for prop, frames in shape_raw.pop("keyframes", {}).items()
                }
                if "color" in shape_raw:
                    shape_raw["color"] = tuple(shape_raw["color"])
                shapes.append(Shape(**shape_raw, keyframes=keyframes))
            layers.append(Layer(**layer_raw, shapes=shapes))
        return cls(**raw, layers=layers or [Layer("Layer 1")])


def _lerp(a: Any, b: Any, amount: float) -> Any:
    if isinstance(a, (list, tuple)):
        values = [int(round(x + (y - x) * amount)) for x, y in zip(a, b)]
        return tuple(values) if isinstance(a, tuple) else values
    if isinstance(a, bool):
        return a
    return a + (b - a) * amount
