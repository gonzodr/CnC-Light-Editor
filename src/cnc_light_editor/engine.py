from __future__ import annotations

from math import cos, radians, sin, sqrt
from typing import Iterable

from .model import Color, Project, Shape


def point_inside(shape: Shape, state: dict, point: tuple[float, float]) -> bool:
    px, py = point
    dx, dy = px - state["x"], py - state["y"]
    angle = radians(-float(state["rotation"]))
    x = dx * cos(angle) - dy * sin(angle)
    y = dx * sin(angle) + dy * cos(angle)
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
    result: list[Color] = []
    for point in led_points:
        color = (0.0, 0.0, 0.0)
        for layer in project.layers:
            if not layer.visible:
                continue
            for shape in layer.shapes:
                state = shape.state_at(time_ms)
                if not state["visible"] or not point_inside(shape, state, point):
                    continue
                alpha = max(0.0, min(1.0, float(state["opacity"])))
                src = state["color"]
                color = tuple(src[i] * alpha + color[i] * (1.0 - alpha) for i in range(3))
        result.append(tuple(max(0, min(255, int(round(channel)))) for channel in color))
    return result


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
