from __future__ import annotations

from dataclasses import asdict, dataclass, field
from copy import deepcopy
import json
import math
from pathlib import Path
import random
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


class KeyframeMixin:
    """Shared add_keyframe/value_at for the layer-effect generators.

    Every generator effect (Random LED and its siblings below) is a flat,
    shape-independent dataclass with an ``id``/``keyframes`` pair - this
    mixin is the one place that walks ``keyframes`` so each new effect kind
    doesn't reimplement the same lookup-and-interpolate loop.
    """

    keyframes: dict[str, list[Keyframe]]

    def add_keyframe(self, prop: str, time_ms: int, value: Any) -> None:
        frames = self.keyframes.setdefault(prop, [])
        frames[:] = [frame for frame in frames if frame.time_ms != time_ms]
        frames.append(Keyframe(int(time_ms), value))
        frames.sort(key=lambda frame: frame.time_ms)

    def value_at(self, prop: str, time_ms: int) -> Any:
        frames = self.keyframes.get(prop, [])
        if not frames:
            return getattr(self, prop)
        if time_ms < frames[0].time_ms:
            return frames[0].value
        before = frames[0]
        after = None
        for frame in frames[1:]:
            if frame.time_ms > time_ms:
                after = frame
                break
            before = frame
        if after is None or prop != "opacity":
            return before.value
        span = after.time_ms - before.time_ms
        amount = 0.0 if span == 0 else (time_ms - before.time_ms) / span
        amount = _ease(amount, after.easing, after.bezier)
        return _lerp(before.value, after.value, amount)


@dataclass
class RandomLedEffect(KeyframeMixin):
    name: str = "Random LED"
    enabled: bool = True
    seed: int = 1
    life_ms: int = 250
    born_speed: float = 8.0
    particle_count: int = 8
    color: Color = (255, 230, 80)
    opacity: float = 1.0
    id: str = field(default_factory=lambda: uuid4().hex[:10])
    keyframes: dict[str, list[Keyframe]] = field(default_factory=dict)


@dataclass
class StrobeEffect(KeyframeMixin):
    name: str = "Strobe"
    enabled: bool = True
    color: Color = (255, 255, 255)
    opacity: float = 1.0
    frequency_hz: float = 4.0
    duty_cycle: float = 0.5
    blackout: bool = False
    id: str = field(default_factory=lambda: uuid4().hex[:10])
    keyframes: dict[str, list[Keyframe]] = field(default_factory=dict)


@dataclass
class ColorCycleEffect(KeyframeMixin):
    name: str = "Color Cycle"
    enabled: bool = True
    opacity: float = 1.0
    speed_hz: float = 0.25
    spread: float = 1.0
    saturation: float = 1.0
    brightness: float = 1.0
    direction: int = 1
    id: str = field(default_factory=lambda: uuid4().hex[:10])
    keyframes: dict[str, list[Keyframe]] = field(default_factory=dict)


@dataclass
class PulseEffect(KeyframeMixin):
    name: str = "Pulse"
    enabled: bool = True
    color: Color = (120, 200, 255)
    opacity: float = 1.0
    period_ms: int = 1200
    depth: float = 0.8
    id: str = field(default_factory=lambda: uuid4().hex[:10])
    keyframes: dict[str, list[Keyframe]] = field(default_factory=dict)


@dataclass
class CometEffect(KeyframeMixin):
    name: str = "Comet"
    enabled: bool = True
    color: Color = (255, 140, 40)
    opacity: float = 1.0
    speed: float = 12.0
    trail_length: float = 8.0
    direction: int = 1
    id: str = field(default_factory=lambda: uuid4().hex[:10])
    keyframes: dict[str, list[Keyframe]] = field(default_factory=dict)


@dataclass
class Shape:
    kind: str
    name: str
    x: float = 0.5
    y: float = 0.5
    width: float = 0.2
    height: float = 0.2
    rotation: float = 0.0
    rotation_turns: float = 0.0
    color: Color = (255, 90, 20)
    opacity: float = 1.0
    feather: float = 0.0
    mask_expansion: float = 0.0
    fill_mode: str = "fill"
    stroke_width: float = 0.012
    gradient_type: str = "solid"
    gradient_radial_mode: str = "radius"
    gradient_angle: float = 0.0
    gradient_stops: list[GradientStop] = field(default_factory=list)
    visible: bool = True
    wiggle_enabled: bool = False
    wiggle_amplitude: float = 0.02
    wiggle_speed: float = 2.0
    wiggle_seed: int = field(default_factory=lambda: random.randint(0, 2**31 - 1))
    noise_seed: int = field(default_factory=lambda: random.randint(0, 2**31 - 1))
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
        if not frames:
            return base
        if time_ms < frames[0].time_ms:
            return frames[0].value
        before = frames[0]
        after = None
        for frame in frames[1:]:
            if frame.time_ms > time_ms:
                after = frame
                break
            before = frame
        if after is None or prop in {
            "visible", "fill_mode", "gradient_type", "gradient_radial_mode", "wiggle_enabled",
        }:
            return before.value
        span = after.time_ms - before.time_ms
        amount = 0.0 if span == 0 else (time_ms - before.time_ms) / span
        amount = _ease(amount, after.easing, after.bezier)
        return _lerp(before.value, after.value, amount)

    def state_at(self, time_ms: int) -> dict[str, Any]:
        state = {prop: self.value_at(prop, time_ms) for prop in (
            "x", "y", "width", "height", "rotation", "rotation_turns", "color", "opacity",
            "feather", "mask_expansion",
            "fill_mode", "stroke_width", "gradient_type", "gradient_radial_mode",
            "gradient_angle", "visible", "wiggle_enabled", "wiggle_amplitude", "wiggle_speed",
        )}
        state["rotation_total"] = state["rotation"] + state["rotation_turns"] * 360.0
        state["gradient_stops"] = self.gradient_stops
        state["noise_seed"] = self.noise_seed
        if state["wiggle_enabled"]:
            amplitude = state["wiggle_amplitude"]
            speed = state["wiggle_speed"]
            state["x"] += _wiggle_axis(self.wiggle_seed, 0, speed, time_ms) * amplitude
            state["y"] += _wiggle_axis(self.wiggle_seed, 1, speed, time_ms) * amplitude
            state["rotation_total"] += (
                _wiggle_axis(self.wiggle_seed, 2, speed, time_ms) * amplitude * 180.0
            )
        return state


@dataclass
class Layer:
    name: str
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0
    shapes: list[Shape] = field(default_factory=list)
    effects: list[RandomLedEffect] = field(default_factory=list)
    strobe_effects: list[StrobeEffect] = field(default_factory=list)
    color_cycle_effects: list[ColorCycleEffect] = field(default_factory=list)
    pulse_effects: list[PulseEffect] = field(default_factory=list)
    comet_effects: list[CometEffect] = field(default_factory=list)
    is_canvas: bool = False
    canvas_enabled: bool = True
    id: str = field(default_factory=lambda: uuid4().hex[:10])
    keyframes: dict[str, list[Keyframe]] = field(default_factory=dict)

    def add_keyframe(self, prop: str, time_ms: int, value: Any) -> None:
        frames = self.keyframes.setdefault(prop, [])
        frames[:] = [frame for frame in frames if frame.time_ms != time_ms]
        frames.append(Keyframe(int(time_ms), value))
        frames.sort(key=lambda frame: frame.time_ms)

    def value_at(self, prop: str, time_ms: int) -> Any:
        frames = self.keyframes.get(prop, [])
        if not frames:
            return getattr(self, prop)
        value = frames[0].value
        for frame in frames:
            if frame.time_ms > time_ms:
                break
            value = frame.value
        return value


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
    intro_frames: int = 0
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
    def normalized_intro_frames(self) -> int:
        # Clamped so the intro never extends past where the loop region ends
        # (matches the firmware's own defensive clamp in bakedCurrentFrame()).
        return max(0, min(self.intro_frames, self.normalized_loop_frames))

    @property
    def flash_bytes(self) -> int:
        return self.stored_frame_count * 68 * 3

    @property
    def firmware_playback_ms(self) -> int:
        intro_frames = self.normalized_intro_frames
        loop_end = self.normalized_loop_frames
        loop_len = loop_end - intro_frames
        steps = intro_frames + loop_len * max(1, self.loops) + (self.stored_frame_count - loop_end)
        return steps * max(1, self.frame_ms or 1)

    def canvas_transparency_at(self, time_ms: int) -> bool:
        if not self.overlay:
            return False
        canvas_layers = [layer for layer in self.layers if layer.is_canvas]
        if not canvas_layers:
            return True
        return any(
            layer.visible and bool(layer.value_at("canvas_enabled", time_ms))
            for layer in canvas_layers
        )

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
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        raw = deepcopy(data)
        layers: list[Layer] = []
        for layer_raw in raw.pop("layers", []):
            layer_keyframes = {
                prop: [Keyframe(**frame) for frame in frames]
                for prop, frames in layer_raw.pop("keyframes", {}).items()
            }
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
            effects = _load_keyframed_effects(layer_raw.pop("effects", []), RandomLedEffect)
            strobe_effects = _load_keyframed_effects(layer_raw.pop("strobe_effects", []), StrobeEffect)
            color_cycle_effects = _load_keyframed_effects(
                layer_raw.pop("color_cycle_effects", []), ColorCycleEffect,
            )
            pulse_effects = _load_keyframed_effects(layer_raw.pop("pulse_effects", []), PulseEffect)
            comet_effects = _load_keyframed_effects(layer_raw.pop("comet_effects", []), CometEffect)
            layers.append(Layer(
                **layer_raw,
                shapes=shapes,
                effects=effects,
                strobe_effects=strobe_effects,
                color_cycle_effects=color_cycle_effects,
                pulse_effects=pulse_effects,
                comet_effects=comet_effects,
                keyframes=layer_keyframes,
            ))
        return cls(**raw, layers=layers or [Layer("Layer 1")])


def _load_keyframed_effects(raw_list: list[dict[str, Any]], cls: type) -> list[Any]:
    """Reconstruct a list of KeyframeMixin effects (Random LED and siblings).

    They all share the same on-disk shape: flat fields plus a per-property
    ``keyframes`` map and an optional ``color`` tuple - this is the one place
    that knows how to rebuild that shape, so each new effect kind in
    Project.from_dict is a single call instead of a repeated parsing block.
    """
    effects = []
    for effect_raw in raw_list:
        effect_raw = dict(effect_raw)
        keyframes = {
            prop: [Keyframe(**frame) for frame in frames]
            for prop, frames in effect_raw.pop("keyframes", {}).items()
        }
        for frames in keyframes.values():
            for frame in frames:
                if frame.bezier is not None:
                    frame.bezier = tuple(frame.bezier)
        for frame in keyframes.get("color", []):
            frame.value = tuple(frame.value)
        if "color" in effect_raw:
            effect_raw["color"] = tuple(effect_raw["color"])
        effects.append(cls(**effect_raw, keyframes=keyframes))
    return effects


def _wiggle_phase(seed: int, salt: int) -> float:
    # Deterministic per-(shape, axis, harmonic) phase - a hash rather than a
    # random.Random instance so state_at() (called once per shape per frame)
    # stays allocation-free.
    mixed = (seed * 2654435761 + salt * 40503 + 12345) & 0xFFFFFFFF
    return (mixed / 0xFFFFFFFF) * 2 * math.pi


def _wiggle_axis(seed: int, axis: int, speed_hz: float, time_ms: int) -> float:
    # Three summed sine harmonics (non-integer frequency ratios) read as
    # smooth, organic jitter rather than a single robotic oscillation.
    t = time_ms / 1000.0 * max(0.01, speed_hz)
    harmonics = ((1.0, 1.0), (2.17, 0.5), (4.33, 0.25))
    total_weight = sum(weight for _freq, weight in harmonics)
    value = sum(
        weight * math.sin(t * freq + _wiggle_phase(seed, axis * 10 + index))
        for index, (freq, weight) in enumerate(harmonics)
    )
    return value / total_weight


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
