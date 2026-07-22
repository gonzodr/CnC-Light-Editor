from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

Color = tuple[int, int, int]


@dataclass
class Keyframe:
    time_ms: int
    value: Any
    easing: str = "linear"
    bezier: tuple[float, float, float, float] | None = None


@dataclass
class GradientStop:
    position: float
    color: Color
    id: str = field(default_factory=lambda: uuid4().hex[:8])


@dataclass
class RandomLedEffect:
    name: str = "Random LED"
    enabled: bool = True
    seed: int = 1
    life_ms: int = 250
    born_speed: float = 8.0
    particle_count: int = 8
    color: Color = (255, 230, 80)
    id: str = field(default_factory=lambda: uuid4().hex[:10])
    keyframes: dict[str, list[Keyframe]] = field(default_factory=dict)

    def add_keyframe(self, prop: str, time_ms: int, value: Any) -> None:
        frames = self.keyframes.setdefault(prop, [])
        frames[:] = [frame for frame in frames if frame.time_ms != time_ms]
        frames.append(Keyframe(int(time_ms), value))
        frames.sort(key=lambda frame: frame.time_ms)

    def value_at(self, prop: str, time_ms: int) -> Any:
        value = getattr(self, prop)
        for frame in self.keyframes.get(prop, []):
            if frame.time_ms > time_ms:
                break
            value = frame.value
        return value


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
    fill_mode: str = "fill"
    stroke_width: float = 0.012
    gradient_type: str = "solid"
    gradient_radial_mode: str = "radius"
    gradient_angle: float = 0.0
    gradient_stops: list[GradientStop] = field(default_factory=list)
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
        if after is None or prop in {
            "visible", "fill_mode", "gradient_type", "gradient_radial_mode",
        }:
            return before.value
        span = after.time_ms - before.time_ms
        amount = 0.0 if span == 0 else (time_ms - before.time_ms) / span
        amount = _ease(amount, after.easing, after.bezier)
        return _lerp(before.value, after.value, amount)

    def state_at(self, time_ms: int) -> dict[str, Any]:
        state = {prop: self.value_at(prop, time_ms) for prop in (
            "x", "y", "width", "height", "rotation", "color", "opacity",
            "fill_mode", "stroke_width", "gradient_type", "gradient_radial_mode",
            "gradient_angle", "visible",
        )}
        state["gradient_stops"] = self.gradient_stops
        return state


@dataclass
class Layer:
    name: str
    visible: bool = True
    shapes: list[Shape] = field(default_factory=list)
    effects: list[RandomLedEffect] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex[:10])


@dataclass
class Project:
    name: str = "Untitled effect"
    duration_ms: int = 5000
    fps: int = 20
    width: int = 3043
    height: int = 6305
    layers: list[Layer] = field(default_factory=lambda: [Layer("Layer 1")])
    effect_id: int = 1
    frame_ms: int | None = None
    loops: int = 1
    loop_frames: int = 0
    overlay: bool = False

    def __post_init__(self) -> None:
        # ``fps`` is kept in the file format so projects saved by early builds
        # still load. V4 firmware timing is canonically represented by the
        # integer ``frame_ms`` value.
        if self.frame_ms is None:
            self.frame_ms = max(1, round(1000 / max(1, self.fps)))

    @property
    def actual_fps(self) -> float:
        return 1000.0 / max(1, self.frame_ms or 1)

    @property
    def stored_frame_count(self) -> int:
        return max(1, math.ceil(self.duration_ms / max(1, self.frame_ms or 1)))

    @property
    def normalized_loop_frames(self) -> int:
        frames = self.stored_frame_count
        return frames if self.loop_frames == 0 else min(frames, max(1, self.loop_frames))

    @property
    def flash_bytes(self) -> int:
        return self.stored_frame_count * 68 * 3

    @property
    def firmware_playback_ms(self) -> int:
        loop_frames = self.normalized_loop_frames
        steps = loop_frames * max(1, self.loops) + self.stored_frame_count - loop_frames
        return steps * max(1, self.frame_ms or 1)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        temporary.replace(target)

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
                for frames in keyframes.values():
                    for frame in frames:
                        if frame.bezier is not None:
                            frame.bezier = tuple(frame.bezier)
                for frame in keyframes.get("color", []):
                    frame.value = tuple(frame.value)
                if "color" in shape_raw:
                    shape_raw["color"] = tuple(shape_raw["color"])
                gradient_stops = []
                for stop_raw in shape_raw.pop("gradient_stops", []):
                    stop_raw["color"] = tuple(stop_raw["color"])
                    gradient_stops.append(GradientStop(**stop_raw))
                shapes.append(Shape(**shape_raw, keyframes=keyframes, gradient_stops=gradient_stops))
            effects: list[RandomLedEffect] = []
            for effect_raw in layer_raw.pop("effects", []):
                keyframes = {
                    prop: [Keyframe(**frame) for frame in frames]
                    for prop, frames in effect_raw.pop("keyframes", {}).items()
                }
                for frames in keyframes.values():
                    for frame in frames:
                        if frame.bezier is not None:
                            frame.bezier = tuple(frame.bezier)
                if "color" in effect_raw:
                    effect_raw["color"] = tuple(effect_raw["color"])
                effects.append(RandomLedEffect(**effect_raw, keyframes=keyframes))
            layers.append(Layer(**layer_raw, shapes=shapes, effects=effects))
        return cls(**raw, layers=layers or [Layer("Layer 1")])


def _lerp(a: Any, b: Any, amount: float) -> Any:
    if isinstance(a, (list, tuple)):
        values = [int(round(x + (y - x) * amount)) for x, y in zip(a, b)]
        return tuple(values) if isinstance(a, tuple) else values
    if isinstance(a, bool):
        return a
    return round(a + (b - a) * amount, 12)


def _ease(
    amount: float, easing: str,
    bezier: tuple[float, float, float, float] | None = None,
) -> float:
    amount = max(0.0, min(1.0, amount))
    if easing == "bezier" and bezier is not None:
        x1, y1, x2, y2 = bezier
        x1 = max(0.0, min(1.0, float(x1)))
        x2 = max(0.0, min(1.0, float(x2)))
        low, high = 0.0, 1.0
        for _iteration in range(22):
            parameter = (low + high) / 2.0
            x = _cubic_bezier(parameter, x1, x2)
            if x < amount:
                low = parameter
            else:
                high = parameter
        return _cubic_bezier((low + high) / 2.0, float(y1), float(y2))
    if easing == "ease_in":
        return amount * amount
    if easing == "ease_out":
        return 1.0 - (1.0 - amount) ** 2
    if easing == "ease_in_out":
        return amount * amount * (3.0 - 2.0 * amount)
    return amount


def _cubic_bezier(parameter: float, control_1: float, control_2: float) -> float:
    inverse = 1.0 - parameter
    return (
        3.0 * inverse * inverse * parameter * control_1
        + 3.0 * inverse * parameter * parameter * control_2
        + parameter * parameter * parameter
    )
