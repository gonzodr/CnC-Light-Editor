from __future__ import annotations

from math import cos, radians, sin, sqrt
import random
from typing import Iterable

from .model import Color, Project, Shape


def point_inside(shape: Shape, state: dict, point: tuple[float, float]) -> bool:
    x, y = _local_point(state, point)
    half_w = max(float(state["width"]) / 2, 0.0001)
    half_h = max(float(state["height"]) / 2, 0.0001)
    fill_mode = state.get("fill_mode", "fill")
    stroke = max(0.0005, float(state.get("stroke_width", 0.012)))
    if shape.kind == "ellipse":
        outer = (x / half_w) ** 2 + (y / half_h) ** 2 <= 1
        inner_w, inner_h = half_w - stroke, half_h - stroke
        inner = inner_w > 0 and inner_h > 0 and (x / inner_w) ** 2 + (y / inner_h) ** 2 < 1
        return outer and (fill_mode == "fill" or not inner)
    if shape.kind == "rectangle":
        outer = abs(x) <= half_w and abs(y) <= half_h
        inner = abs(x) < half_w - stroke and abs(y) < half_h - stroke
        return outer and (fill_mode == "fill" or not inner)
    if shape.kind == "triangle":
        outer = _in_triangle(x, y, 0.0, -half_h, half_w, half_h, -half_w, half_h)
        inner_w, inner_h = half_w - stroke, half_h - stroke
        inner = inner_w > 0 and inner_h > 0 and _in_triangle(
            x, y, 0.0, -inner_h, inner_w, inner_h, -inner_w, inner_h
        )
        return outer and (fill_mode == "fill" or not inner)
    if shape.kind == "line":
        thickness = stroke if fill_mode == "stroke" else half_h
        return _distance_to_segment(x, y, -half_w, 0.0, half_w, 0.0) <= thickness
    return False


def render_leds(project: Project, led_points: Iterable[tuple[float, float]], time_ms: int) -> list[Color]:
    points = list(led_points)
    result: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0) for _point in points]
    for layer in project.layers:
        if not layer.visible:
            continue
        shape_states = [(shape, shape.state_at(time_ms)) for shape in layer.shapes]
        random_led_overlays = _random_led_overlays(layer, points, time_ms)
        for point_index, point in enumerate(points):
            color = result[point_index]
            for shape, state in shape_states:
                if not state["visible"] or not point_inside(shape, state, point):
                    continue
                alpha = max(0.0, min(1.0, float(state["opacity"])))
                src = gradient_color(state, point)
                color = tuple(src[i] * alpha + color[i] * (1.0 - alpha) for i in range(3))
            for src, alpha in random_led_overlays[point_index]:
                color = tuple(src[i] * alpha + color[i] * (1.0 - alpha) for i in range(3))
            result[point_index] = color
    return [tuple(max(0, min(255, int(round(channel)))) for channel in color) for color in result]


def _random_led_overlays(layer, points, time_ms: int) -> list[list[tuple[Color, float]]]:
    overlays: list[list[tuple[Color, float]]] = [[] for _point in points]
    if not layer.effects:
        return overlays
    # Random LED is intentionally independent from the layer's geometry. The
    # layer owns/times the effect, but every firmware LED slot is eligible.
    eligible = list(range(len(points)))
    if not eligible:
        return overlays

    for effect in layer.effects:
        if not effect.value_at("enabled", time_ms) or effect.born_speed <= 0 or effect.particle_count <= 0:
            continue
        interval = 1000.0 / effect.born_speed
        life_ms = max(1, effect.life_ms)
        last_birth = max(0, int(time_ms // interval))
        possible_alive = max(1, int(life_ms // interval) + 2)
        first_birth = max(0, last_birth - min(effect.particle_count, possible_alive) + 1)
        brightness: dict[int, float] = {}
        for birth_index in range(first_birth, last_birth + 1):
            age = time_ms - birth_index * interval
            if not 0 <= age < life_ms:
                continue
            generator = random.Random(effect.seed + birth_index * 0x9E3779B1)
            led_index = eligible[generator.randrange(len(eligible))]
            brightness[led_index] = max(brightness.get(led_index, 0.0), 1.0 - age / life_ms)
        for led_index, alpha in brightness.items():
            overlays[led_index].append((effect.color, alpha))
    return overlays


def gradient_color(state: dict, point: tuple[float, float]) -> Color:
    gradient_type = state.get("gradient_type", "solid")
    stops = state.get("gradient_stops", [])
    if gradient_type == "solid" or len(stops) < 2:
        return tuple(state["color"])

    x, y = _local_point(state, point)
    half_w = max(float(state["width"]) / 2, 0.0001)
    half_h = max(float(state["height"]) / 2, 0.0001)
    if gradient_type == "radial":
        amount = sqrt((x / half_w) ** 2 + (y / half_h) ** 2)
    else:
        angle = radians(float(state.get("gradient_angle", 0.0)))
        direction_x, direction_y = cos(angle), sin(angle)
        extent = max(0.0001, abs(direction_x) * half_w + abs(direction_y) * half_h)
        amount = 0.5 + (x * direction_x + y * direction_y) / (2 * extent)
    return sample_gradient(stops, amount)


def sample_gradient(stops, amount: float) -> Color:
    ordered = sorted(stops, key=lambda stop: stop.position)
    amount = max(0.0, min(1.0, amount))
    if amount <= ordered[0].position:
        return tuple(ordered[0].color)
    if amount >= ordered[-1].position:
        return tuple(ordered[-1].color)
    for before, after in zip(ordered, ordered[1:]):
        if amount <= after.position:
            span = max(0.000001, after.position - before.position)
            ratio = (amount - before.position) / span
            return tuple(round(a + (b - a) * ratio) for a, b in zip(before.color, after.color))
    return tuple(ordered[-1].color)


def _local_point(state: dict, point: tuple[float, float]) -> tuple[float, float]:
    px, py = point
    dx, dy = px - state["x"], py - state["y"]
    angle = radians(-float(state["rotation"]))
    return dx * cos(angle) - dy * sin(angle), dx * sin(angle) + dy * cos(angle)


def _distance_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return sqrt((px - ax) ** 2 + (py - ay) ** 2)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    qx, qy = ax + t * dx, ay + t * dy
    return sqrt((px - qx) ** 2 + (py - qy) ** 2)


def _in_triangle(px: float, py: float, ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> bool:
    def sign(x1: float, y1: float, x2: float, y2: float, x3: float, y3: float) -> float:
        return (x1 - x3) * (y2 - y3) - (x2 - x3) * (y1 - y3)

    d1 = sign(px, py, ax, ay, bx, by)
    d2 = sign(px, py, bx, by, cx, cy)
    d3 = sign(px, py, cx, cy, ax, ay)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))
