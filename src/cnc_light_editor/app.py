from __future__ import annotations

import argparse
import os
from pathlib import Path

import pygame

from .engine import point_inside, render_leds
from .exporter import export_arduino_header
from .ledmap import LedMap
from .model import Layer, Project, Shape

ROOT = Path(__file__).resolve().parents[2]
PROJECT_FILE = ROOT / "projects" / "current.cnclight"
EXPORT_FILE = ROOT / "exports" / "cnc_effect.h"
PALETTE = [
    (255, 70, 40), (255, 155, 20), (255, 230, 50), (80, 220, 90),
    (30, 180, 255), (90, 90, 255), (210, 80, 255), (255, 255, 255),
]


class Editor:
    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        self.clock = pygame.time.Clock()
        self.font = pygame.font.Font(None, 22)
        self.small = pygame.font.Font(None, 18)
        self.title = pygame.font.Font(None, 30)
        self.project = Project("First playfield effect")
        self.current_ms = 0
        self.playing = False
        self.stencil = False
        self.calibration = False
        self.selected_led_id = 0
        self.active_layer = 0
        self.selected: Shape | None = None
        self.dragging = False
        self.status = "Ready — 59 playfield LEDs"
        self.buttons: list[tuple[pygame.Rect, str, str]] = []
        self.led_map_path = ROOT / "data" / "led_map.json"
        self.led_map = LedMap.load(self.led_map_path)
        self.led_points = self.led_map.normalized_points()
        self.playfield = pygame.image.load(str(ROOT / "assets" / "playfield.png")).convert_alpha()
        self._add_demo_shape()

    def _add_demo_shape(self) -> None:
        shape = Shape("ellipse", "Pulse", x=0.5, y=0.45, width=0.08, height=0.08, color=(255, 90, 20))
        shape.add_keyframe("width", 0, 0.06)
        shape.add_keyframe("height", 0, 0.06)
        shape.add_keyframe("width", 2200, 0.9)
        shape.add_keyframe("height", 2200, 0.45)
        shape.add_keyframe("opacity", 0, 1.0)
        shape.add_keyframe("opacity", 2600, 0.0)
        self.project.layers[0].shapes.append(shape)
        self.selected = shape

    def run(self, smoke_test: bool = False, screenshot: Path | None = None) -> None:
        running = True
        frames = 0
        while running:
            dt = self.clock.tick(60)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                else:
                    self.handle_event(event)
            if self.playing:
                self.current_ms = (self.current_ms + dt) % max(1, self.project.duration_ms)
            self.draw()
            pygame.display.flip()
            frames += 1
            if smoke_test and frames >= 3:
                if screenshot:
                    pygame.image.save(self.screen, str(screenshot))
                running = False

    def layout(self) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect]:
        width, height = self.screen.get_size()
        workspace = pygame.Rect(18, 66, max(420, width - 370), max(480, height - 190))
        image_ratio = self.playfield.get_width() / self.playfield.get_height()
        draw_h = workspace.height
        draw_w = int(draw_h * image_ratio)
        if draw_w > workspace.width:
            draw_w = workspace.width
            draw_h = int(draw_w / image_ratio)
        canvas = pygame.Rect(workspace.x + (workspace.width - draw_w) // 2, workspace.y, draw_w, draw_h)
        panel = pygame.Rect(width - 335, 66, 317, height - 84)
        timeline = pygame.Rect(18, height - 104, width - 370, 70)
        return canvas, panel, timeline

    def handle_event(self, event: pygame.event.Event) -> None:
        canvas, panel, timeline = self.layout()
        if event.type == pygame.KEYDOWN:
            self._handle_key(event)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for rect, action, _ in self.buttons:
                if rect.collidepoint(event.pos):
                    self._action(action)
                    return
            if timeline.collidepoint(event.pos):
                self.current_ms = int(max(0, min(1, (event.pos[0] - timeline.x) / timeline.width)) * self.project.duration_ms)
                return
            if canvas.collidepoint(event.pos):
                point = self._screen_to_world(event.pos, canvas)
                if self.calibration:
                    self.selected_led_id = self._pick_led(point)
                    self.status = f"Selected LED position {self.selected_led_id}"
                else:
                    self.selected = self._pick(point)
                    self.dragging = self.selected is not None
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging = False
        elif event.type == pygame.MOUSEMOTION and self.dragging and self.selected:
            x, y = self._screen_to_world(event.pos, canvas)
            self._set_animated("x", max(0.0, min(1.0, x)))
            self._set_animated("y", max(0.0, min(1.0, y)))
        elif event.type == pygame.MOUSEWHEEL and self.selected:
            factor = 1.08 if event.y > 0 else 0.92
            state = self.selected.state_at(self.current_ms)
            self._set_animated("width", max(0.01, state["width"] * factor))
            self._set_animated("height", max(0.01, state["height"] * factor))

    def _handle_key(self, event: pygame.event.Event) -> None:
        ctrl = bool(event.mod & pygame.KMOD_CTRL)
        if self.calibration:
            if event.key in (pygame.K_TAB, pygame.K_RIGHT):
                self._action("led_next")
            elif event.key == pygame.K_LEFT:
                self._action("led_prev")
            elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                self._action("led_index:1")
            elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self._action("led_index:-1")
            elif event.key == pygame.K_s and ctrl:
                self._action("save_map")
            return

        if event.key == pygame.K_SPACE:
            self.playing = not self.playing
        elif event.key == pygame.K_s and ctrl:
            self._action("save")
        elif event.key == pygame.K_o and ctrl:
            self._action("load")
        elif event.key in (pygame.K_DELETE, pygame.K_BACKSPACE):
            self._action("delete")
        elif event.key == pygame.K_k:
            self._action("keyframe")
        elif event.key == pygame.K_v and self.selected:
            state = self.selected.state_at(self.current_ms)
            self._set_animated("visible", not state["visible"])
        elif event.key == pygame.K_LEFTBRACKET and self.selected:
            self._set_animated("rotation", self.selected.state_at(self.current_ms)["rotation"] - 5)
        elif event.key == pygame.K_RIGHTBRACKET and self.selected:
            self._set_animated("rotation", self.selected.state_at(self.current_ms)["rotation"] + 5)

    def _action(self, action: str) -> None:
        if action.startswith("add:"):
            kind = action.split(":", 1)[1]
            shape = Shape(kind, f"{kind.title()} {sum(len(layer.shapes) for layer in self.project.layers) + 1}")
            if kind == "line":
                shape.width, shape.height = 0.3, 0.025
            self.project.layers[self.active_layer].shapes.append(shape)
            self.selected = shape
            self.status = f"Added {kind}"
        elif action == "layer":
            self.project.layers.append(Layer(f"Layer {len(self.project.layers) + 1}"))
            self.active_layer = len(self.project.layers) - 1
            self.selected = None
        elif action == "delete" and self.selected:
            for layer in self.project.layers:
                if self.selected in layer.shapes:
                    layer.shapes.remove(self.selected)
                    break
            self.selected = None
        elif action == "keyframe" and self.selected:
            state = self.selected.state_at(self.current_ms)
            for prop in ("x", "y", "width", "height", "rotation", "color", "opacity", "visible"):
                self.selected.add_keyframe(prop, self.current_ms, state[prop])
            self.status = f"Keyframe at {self.current_ms} ms"
        elif action == "stencil":
            self.stencil = not self.stencil
            if self.stencil:
                self.calibration = False
        elif action == "calibration":
            self.calibration = not self.calibration
            if self.calibration:
                self.stencil = False
            self.playing = False
            self.selected = None
        elif action == "play":
            self.playing = not self.playing
        elif action == "save":
            self.project.save(PROJECT_FILE)
            self.status = f"Saved: {PROJECT_FILE.name}"
        elif action == "load":
            if PROJECT_FILE.exists():
                self.project = Project.load(PROJECT_FILE)
                self.selected = None
                self.active_layer = 0
                self.status = f"Loaded: {PROJECT_FILE.name}"
        elif action == "export":
            errors = self.led_map.validate()
            if errors:
                self.status = "Export blocked: " + "; ".join(errors)
            else:
                points = self.led_map.normalized_points(firmware_order=True)
                export_arduino_header(self.project, points, EXPORT_FILE)
                self.status = f"Exported in firmware order: {EXPORT_FILE.name}"
        elif action == "led_prev":
            self.selected_led_id = (self.selected_led_id - 1) % len(self.led_map.leds)
        elif action == "led_next":
            self.selected_led_id = (self.selected_led_id + 1) % len(self.led_map.leds)
        elif action.startswith("led_index:"):
            led = next(item for item in self.led_map.leds if item.id == self.selected_led_id)
            delta = int(action.split(":")[1])
            target = max(0, min(len(self.led_map.leds) - 1, led.firmware_index + delta))
            self.led_map.set_firmware_index(led.id, target, swap=True)
            self.led_map.mapping_status = "calibration_in_progress"
            self.status = f"LED position {led.id} → firmware index {target}"
        elif action == "save_map":
            self.led_map.save(self.led_map_path)
            self.status = "LED map saved"
        elif action == "map_verified":
            errors = self.led_map.validate()
            if errors:
                self.status = "Cannot verify: " + "; ".join(errors)
            else:
                self.led_map.mapping_status = "hardware_verified"
                self.led_map.save(self.led_map_path)
                self.status = "LED map marked as hardware verified"
        elif action.startswith("color:") and self.selected:
            self._set_animated("color", PALETTE[int(action.split(":")[1])])
        elif action.startswith("select_layer:"):
            self.active_layer = int(action.split(":")[1])
            self.selected = None
        elif action.startswith("toggle_layer:"):
            index = int(action.split(":")[1])
            self.project.layers[index].visible = not self.project.layers[index].visible

    def _set_animated(self, prop: str, value) -> None:
        if not self.selected:
            return
        if self.current_ms == 0 and not self.selected.keyframes.get(prop):
            setattr(self.selected, prop, value)
        else:
            self.selected.add_keyframe(prop, self.current_ms, value)

    def _pick(self, point: tuple[float, float]) -> Shape | None:
        for layer in reversed(self.project.layers):
            if not layer.visible:
                continue
            for shape in reversed(layer.shapes):
                state = shape.state_at(self.current_ms)
                if state["visible"] and point_inside(shape, state, point):
                    return shape
        return None

    @staticmethod
    def _screen_to_world(pos: tuple[int, int], canvas: pygame.Rect) -> tuple[float, float]:
        return ((pos[0] - canvas.x) / canvas.width, (pos[1] - canvas.y) / canvas.height)

    def _pick_led(self, point: tuple[float, float]) -> int:
        best = min(
            zip(self.led_map.leds, self.led_points),
            key=lambda item: (item[1][0] - point[0]) ** 2 + (item[1][1] - point[1]) ** 2,
        )
        return best[0].id

    def draw(self) -> None:
        self.screen.fill((22, 24, 31))

        canvas, panel, timeline = self.layout()
        self.buttons.clear()
        self._draw_toolbar()
        scaled = pygame.transform.smoothscale(self.playfield, canvas.size)
        if self.stencil:
            dim = pygame.Surface(canvas.size, pygame.SRCALPHA)
            dim.fill((5, 7, 12, 205))
            scaled.blit(dim, (0, 0))
        self.screen.blit(scaled, canvas)
        if not self.stencil:
            self._draw_shapes(canvas)
        self._draw_leds(canvas)
        pygame.draw.rect(self.screen, (115, 121, 140), canvas, 1)
        self._draw_timeline(timeline)
        self._draw_panel(panel)

    def _draw_toolbar(self) -> None:
        items = [
            ("add:ellipse", "Circle"), ("add:rectangle", "Rectangle"),
            ("add:triangle", "Triangle"), ("add:line", "Line"),
            ("layer", "+ Layer"), ("delete", "Delete"),
            ("keyframe", "Keyframe"), ("play", "Pause" if self.playing else "Play"),
            ("stencil", "Edit view" if self.stencil else "Stencil"),
            ("calibration", "Edit view" if self.calibration else "Calibrate"),
            ("save", "Save"), ("load", "Load"), ("export", "Arduino export"),
        ]
        x = 18
        for action, label in items:
            width = max(64, self.small.size(label)[0] + 18)
            rect = pygame.Rect(x, 18, width, 32)
            active = (action == "stencil" and self.stencil) or (action == "calibration" and self.calibration)
            self._button(rect, action, label, active)
            x += width + 7

    def _draw_shapes(self, canvas: pygame.Rect) -> None:
        for layer in self.project.layers:
            if not layer.visible:
                continue
            for shape in layer.shapes:
                state = shape.state_at(self.current_ms)
                if not state["visible"]:
                    continue
                cx = canvas.x + int(state["x"] * canvas.width)
                cy = canvas.y + int(state["y"] * canvas.height)
                w = max(4, int(state["width"] * canvas.width))
                h = max(4, int(state["height"] * canvas.height))
                surface = pygame.Surface((w + 8, h + 8), pygame.SRCALPHA)
                color = (*state["color"], int(170 * state["opacity"]))
                local = surface.get_rect().inflate(-6, -6)
                if shape.kind == "ellipse":
                    pygame.draw.ellipse(surface, color, local)
                elif shape.kind == "rectangle":
                    pygame.draw.rect(surface, color, local, border_radius=3)
                elif shape.kind == "triangle":
                    pygame.draw.polygon(surface, color, [(surface.get_width() // 2, 3), (surface.get_width() - 3, surface.get_height() - 3), (3, surface.get_height() - 3)])
                else:
                    pygame.draw.line(surface, color, (4, surface.get_height() // 2), (surface.get_width() - 4, surface.get_height() // 2), max(2, h - 6))
                rotated = pygame.transform.rotate(surface, -state["rotation"])
                rect = rotated.get_rect(center=(cx, cy))
                self.screen.blit(rotated, rect)
                if shape is self.selected:
                    pygame.draw.rect(self.screen, (255, 235, 90), rect, 2)

    def _draw_leds(self, canvas: pygame.Rect) -> None:
        colors = render_leds(self.project, self.led_points, self.current_ms)
        radius = max(3, min(9, canvas.width // 90))
        for led, (x, y), color in zip(self.led_map.leds, self.led_points, colors):
            pos = (canvas.x + int(x * canvas.width), canvas.y + int(y * canvas.height))
            if self.stencil:
                glow = tuple(max(20, c) for c in color) if any(color) else (22, 24, 30)
                pygame.draw.circle(self.screen, glow, pos, radius + 3)
                pygame.draw.circle(self.screen, color, pos, radius)
            elif self.calibration:
                selected = led.id == self.selected_led_id
                marker = (255, 225, 55) if selected else (70, 205, 255)
                pygame.draw.circle(self.screen, marker, pos, radius + (4 if selected else 0), 0 if selected else 2)
            else:
                pygame.draw.circle(self.screen, (240, 70, 55), pos, radius, 2)
            if canvas.width > 620 or self.calibration:
                label = str(led.firmware_index)
                self.screen.blit(self.small.render(label, True, (245, 245, 245)), (pos[0] + radius, pos[1] - radius))

    def _draw_timeline(self, rect: pygame.Rect) -> None:
        pygame.draw.rect(self.screen, (32, 35, 45), rect, border_radius=5)
        pygame.draw.rect(self.screen, (70, 75, 91), rect, 1, border_radius=5)
        y = rect.centery + 7
        pygame.draw.line(self.screen, (120, 126, 145), (rect.x + 10, y), (rect.right - 10, y), 3)
        if self.selected:
            times = {frame.time_ms for frames in self.selected.keyframes.values() for frame in frames}
            for time_ms in times:
                x = rect.x + int(time_ms / self.project.duration_ms * rect.width)
                pygame.draw.polygon(self.screen, (255, 210, 55), [(x, y - 8), (x + 6, y), (x, y + 8), (x - 6, y)])
        play_x = rect.x + int(self.current_ms / self.project.duration_ms * rect.width)
        pygame.draw.line(self.screen, (255, 75, 55), (play_x, rect.y + 5), (play_x, rect.bottom - 5), 2)
        label = f"{self.current_ms / 1000:.2f}s / {self.project.duration_ms / 1000:.2f}s   {self.project.fps} fps"
        self.screen.blit(self.small.render(label, True, (220, 223, 232)), (rect.x + 8, rect.y + 6))

    def _draw_panel(self, panel: pygame.Rect) -> None:
        pygame.draw.rect(self.screen, (29, 32, 41), panel, border_radius=6)
        x, y = panel.x + 14, panel.y + 14
        self.screen.blit(self.title.render("CnC Light Editor", True, (245, 246, 250)), (x, y)); y += 38
        map_label = f"59 playfield LEDs • {self.led_map.mapping_status}"
        self.screen.blit(self.small.render(map_label, True, (255, 180, 75)), (x, y)); y += 34
        if self.calibration:
            self._draw_calibration_panel(panel, x, y)
            return
        self.screen.blit(self.font.render("Layers", True, (220, 223, 232)), (x, y)); y += 27
        for index, layer in enumerate(self.project.layers):
            eye = pygame.Rect(x, y, 26, 25)
            row = pygame.Rect(x + 32, y, panel.width - 74, 25)
            self._button(eye, f"toggle_layer:{index}", "●" if layer.visible else "○", False)
            self._button(row, f"select_layer:{index}", layer.name, index == self.active_layer)
            y += 31
        y += 12
        self.screen.blit(self.font.render("Selection", True, (220, 223, 232)), (x, y)); y += 27
        if self.selected:
            state = self.selected.state_at(self.current_ms)
            lines = [
                self.selected.name, f"Type: {self.selected.kind}",
                f"Position: {state['x']:.3f}, {state['y']:.3f}",
                f"Size: {state['width']:.3f} × {state['height']:.3f}",
                f"Rotation: {state['rotation']:.0f}°", f"Visible: {state['visible']}",
            ]
            for line in lines:
                self.screen.blit(self.small.render(line, True, (198, 202, 214)), (x, y)); y += 20
            y += 8
            self.screen.blit(self.small.render("Color (keyframed at playhead)", True, (198, 202, 214)), (x, y)); y += 23
            for index, color in enumerate(PALETTE):
                rect = pygame.Rect(x + (index % 4) * 48, y + (index // 4) * 34, 39, 25)
                pygame.draw.rect(self.screen, color, rect, border_radius=3)
                self.buttons.append((rect, f"color:{index}", ""))
            y += 78
        else:
            self.screen.blit(self.small.render("Click a shape to select it.", True, (150, 155, 170)), (x, y)); y += 30
        tips = ["Mouse wheel: scale", "Drag: move / create keyframe", "[ / ]: rotate", "K: keyframe all properties", "V: visibility keyframe", "Space: play / pause", "Ctrl+S / Ctrl+O: save / load"]
        self.screen.blit(self.font.render("Controls", True, (220, 223, 232)), (x, y)); y += 27
        for tip in tips:
            self.screen.blit(self.small.render(tip, True, (168, 173, 188)), (x, y)); y += 19
        self.screen.blit(self.small.render(self.status, True, (95, 215, 160)), (x, panel.bottom - 28))

    def _draw_calibration_panel(self, panel: pygame.Rect, x: int, y: int) -> None:
        led = next(item for item in self.led_map.leds if item.id == self.selected_led_id)
        self.screen.blit(self.font.render("LED calibration", True, (220, 223, 232)), (x, y)); y += 32
        details = [
            f"Graphic position ID: {led.id}",
            f"Firmware index: {led.firmware_index}",
            f"Pixel: {led.x:.0f}, {led.y:.0f}",
        ]
        for line in details:
            self.screen.blit(self.small.render(line, True, (205, 209, 221)), (x, y)); y += 22
        y += 12
        buttons = [
            ("led_prev", "Previous"), ("led_index:-1", "Index -"),
            ("led_index:1", "Index +"), ("led_next", "Next"),
        ]
        for index, (action, label) in enumerate(buttons):
            rect = pygame.Rect(x + (index % 2) * 132, y + (index // 2) * 38, 122, 30)
            self._button(rect, action, label, False)
        y += 88
        self._button(pygame.Rect(x, y, 254, 32), "save_map", "Save LED map", False); y += 40
        self._button(pygame.Rect(x, y, 254, 32), "map_verified", "Mark hardware verified", False); y += 52
        errors = self.led_map.validate()
        validity = "Mapping is contiguous and unique" if not errors else "; ".join(errors)
        validity_color = (95, 215, 160) if not errors else (255, 100, 90)
        self.screen.blit(self.small.render(validity, True, validity_color), (x, y)); y += 34
        tips = [
            "Click the LED position that lit up.",
            "Use Index +/- to swap chain indices.",
            "Tab / arrows: next or previous marker.",
            "Ctrl+S: save the mapping.",
            "Only verify after a hardware test.",
        ]
        for tip in tips:
            self.screen.blit(self.small.render(tip, True, (168, 173, 188)), (x, y)); y += 21
        self.screen.blit(self.small.render(self.status, True, (95, 215, 160)), (x, panel.bottom - 28))

    def _button(self, rect: pygame.Rect, action: str, label: str, active: bool) -> None:
        color = (77, 99, 150) if active else (49, 54, 68)
        pygame.draw.rect(self.screen, color, rect, border_radius=4)
        pygame.draw.rect(self.screen, (91, 98, 117), rect, 1, border_radius=4)
        text = self.small.render(label, True, (245, 246, 250))
        self.screen.blit(text, text.get_rect(center=rect.center))
        self.buttons.append((rect, action, label))


def main() -> None:
    parser = argparse.ArgumentParser(description="CnC Pinball Light Editor")
    parser.add_argument("--smoke-test", action="store_true", help="Render three frames, then exit")
    parser.add_argument("--screenshot", type=Path, help="Write a screenshot during smoke test")
    args = parser.parse_args()
    if args.smoke_test:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.init()
    screen = pygame.display.set_mode((1280, 900), pygame.RESIZABLE)
    pygame.display.set_caption("CnC Pinball — Light Effect Editor")
    try:
        Editor(screen).run(args.smoke_test, args.screenshot)
    finally:
        pygame.quit()


if __name__ == "__main__":
    main()
