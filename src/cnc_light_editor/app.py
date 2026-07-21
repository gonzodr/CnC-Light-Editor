from __future__ import annotations

import argparse
from copy import deepcopy
import math
import os
from pathlib import Path
from uuid import uuid4

import pygame

from .engine import point_inside, render_leds
from .effect_importer import ImportedEffect, load_effect_data
from .exporter import export_arduino_header
from .ledmap import Led, LedMap
from .model import Layer, Project, Shape

ROOT = Path(__file__).resolve().parents[2]
PROJECT_FILE = ROOT / "projects" / "current.cnclight"
EXPORT_FILE = ROOT / "exports" / "cnc_effect.h"
PALETTE = [
    (255, 70, 40), (255, 155, 20), (255, 230, 50), (80, 220, 90),
    (30, 180, 255), (90, 90, 255), (210, 80, 255), (255, 255, 255),
]

TOP_BAR = 54
TOOLBAR_WIDTH = 68
INSPECTOR_WIDTH = 318
TIMELINE_HEIGHT = 218
ACCENT = (77, 148, 255)
PANEL = (31, 34, 42)
PANEL_DARK = (24, 26, 33)


class Editor:
    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        self.clock = pygame.time.Clock()
        self.font = pygame.font.Font(None, 22)
        self.small = pygame.font.Font(None, 18)
        self.title = pygame.font.Font(None, 30)
        self.project = Project("First playfield effect")
        self.project_path: Path | None = None
        self.saved_project_state: dict | None = None
        self.last_window_caption = ""
        self.current_ms = 0
        self.playing = False
        self.stencil = False
        self.calibration = False
        self.selected_led_id = 0
        self.led_id_input = "0"
        self.led_id_editing = False
        self.led_name_input = ""
        self.led_name_editing = False
        self.led_move_ready_id: int | None = None
        self.led_move_origin: tuple[float, float] | None = None
        self.duration_input = "5.0"
        self.duration_editing = False
        self.stroke_input = "1.2"
        self.stroke_editing = False
        self.imported_effects: list[ImportedEffect] = []
        self.active_import_index: int | None = None
        self.effect_data_path: Path | None = None
        self.active_layer = 0
        self.selected: Shape | None = None
        self.drag_mode: str | None = None
        self.drag_origin = (0, 0)
        self.drag_shape_state: dict | None = None
        self.drag_key_time: int | None = None
        self.selected_keyframes: set[tuple[str, str, int]] = set()
        self.context_menu_pos: tuple[int, int] | None = None
        self.keyframe_marquee_current: tuple[int, int] | None = None
        self.keyframe_marquee_additive = False
        self.keyframe_right_click_time: int | None = None
        self.drag_layer_index: int | None = None
        self.tool_drag: str | None = None
        self.scrub_prop: str | None = None
        self.space_down = False
        self.snap = True
        self.zoom = 1.0
        self.pan = pygame.Vector2()
        self.timeline_zoom = 1.0
        self.timeline_scroll_ms = 0.0
        self.undo_stack: list[Project] = []
        self.redo_stack: list[Project] = []
        self.change_snapshot: Project | None = None
        self.status = "Ready — 59 playfield LEDs"
        self.buttons: list[tuple[pygame.Rect, str, str]] = []
        self.led_map_path = ROOT / "data" / "led_map.json"
        self.led_map = LedMap.load(self.led_map_path)
        self.status = f"Ready — {len(self.led_map.leds)} playfield LEDs"
        self.led_points = self.led_map.normalized_points()
        self.playfield = pygame.image.load(str(ROOT / "assets" / "playfield.png")).convert_alpha()
        guide_source = pygame.image.load(str(ROOT / "assets" / "playfield_layout.png")).convert_alpha()
        guide_source = pygame.transform.smoothscale(guide_source, (2048, 990))
        guide_mask = pygame.mask.from_surface(guide_source, 40)
        guide = guide_mask.to_surface(
            setcolor=(225, 229, 238, 215),
            unsetcolor=(0, 0, 0, 0),
        ).convert_alpha()
        self.layout_guide = pygame.transform.rotate(guide, -90)
        self._sync_led_fields()
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
                self.current_ms = (self.current_ms + dt) % max(1, self._playback_duration())
            self.draw()
            pygame.display.flip()
            frames += 1
            if smoke_test and frames >= 3:
                if screenshot:
                    pygame.image.save(self.screen, str(screenshot))
                running = False

    def layout(self) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect]:
        width, height = self.screen.get_size()
        workspace = pygame.Rect(
            TOOLBAR_WIDTH, TOP_BAR,
            max(300, width - TOOLBAR_WIDTH - INSPECTOR_WIDTH),
            max(260, height - TOP_BAR - TIMELINE_HEIGHT),
        )
        panel = pygame.Rect(width - INSPECTOR_WIDTH, TOP_BAR, INSPECTOR_WIDTH, height - TOP_BAR)
        timeline = pygame.Rect(TOOLBAR_WIDTH, height - TIMELINE_HEIGHT, workspace.width, TIMELINE_HEIGHT)
        viewport = pygame.Rect(workspace.x, workspace.y, workspace.width, workspace.height)
        image_ratio = self.playfield.get_width() / self.playfield.get_height()
        draw_h = int(viewport.height * 0.94 * self.zoom)
        draw_w = int(draw_h * image_ratio)
        if draw_w > viewport.width * 0.94 * self.zoom:
            draw_w = int(viewport.width * 0.94 * self.zoom)
            draw_h = int(draw_w / image_ratio)
        canvas = pygame.Rect(0, 0, draw_w, draw_h)
        canvas.center = viewport.center + self.pan
        return canvas, panel, timeline

    def handle_event(self, event: pygame.event.Event) -> None:
        canvas, panel, timeline = self.layout()
        viewport = pygame.Rect(TOOLBAR_WIDTH, TOP_BAR, panel.x - TOOLBAR_WIDTH, timeline.y - TOP_BAR)

        if event.type == pygame.KEYDOWN:
            if self.led_id_editing:
                self._handle_led_id_input(event)
                return
            if self.led_name_editing:
                self._handle_led_name_input(event)
                return
            if self.duration_editing:
                self._handle_duration_input(event)
                return
            if self.stroke_editing:
                self._handle_stroke_input(event)
                return
            if event.key == pygame.K_SPACE:
                self.space_down = True
            self._handle_key(event)
            return
        if event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
            self.space_down = False
            return

        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1 and self.context_menu_pos:
                action = self._context_action_at(event.pos)
                if action:
                    self._apply_keyframe_context(action)
                self.context_menu_pos = None
                return
            if event.button == 3:
                self.context_menu_pos = None
                if timeline.collidepoint(event.pos) and not self._active_imported_effect():
                    self.drag_mode = "keyframe_marquee"
                    self.drag_origin = event.pos
                    self.keyframe_marquee_current = event.pos
                    self.keyframe_marquee_additive = bool(pygame.key.get_mods() & pygame.KMOD_CTRL)
                    self.keyframe_right_click_time = self._pick_keyframe(event.pos, timeline)
                return
            if self.calibration and self.drag_mode == "led_move" and event.button == 1:
                if canvas.collidepoint(event.pos):
                    self._move_selected_led(event.pos, canvas)
                    self._finish_led_move()
                else:
                    self.status = "Click the playfield to place the LED, or Esc to cancel"
                return
            if event.button == 2 or (event.button == 1 and self.space_down):
                self.drag_mode = "pan"
                self.drag_origin = event.pos
                return
            if event.button != 1:
                return
            for rect, action, _ in reversed(self.buttons):
                if rect.collidepoint(event.pos):
                    if action.startswith("add:"):
                        self.tool_drag = action.split(":", 1)[1]
                        self.drag_origin = event.pos
                    elif action.startswith("drag_layer:"):
                        self.drag_layer_index = int(action.split(":")[1])
                        self.active_layer = self.drag_layer_index
                        self.drag_origin = event.pos
                        self._begin_change()
                    elif action == "duration_slider":
                        self.drag_mode = "duration"
                        self.drag_origin = event.pos
                        self._begin_change()
                        self._set_duration_from_x(event.pos[0], self._duration_slider_rect(timeline))
                    elif action.startswith("scrub:") and self.selected:
                        self.scrub_prop = action.split(":", 1)[1]
                        self.drag_mode = "scrub"
                        self.drag_origin = event.pos
                        self.drag_shape_state = dict(self.selected.state_at(self.current_ms))
                        self._begin_change()
                    else:
                        self._action(action)
                    return
            if timeline.collidepoint(event.pos):
                key_time = self._pick_keyframe(event.pos, timeline)
                if key_time is not None:
                    keys = self._keyframe_keys_at(key_time)
                    if pygame.key.get_mods() & pygame.KMOD_CTRL:
                        if self.selected_keyframes.intersection(keys):
                            self.selected_keyframes.difference_update(keys)
                        else:
                            self.selected_keyframes.update(keys)
                    elif not self.selected_keyframes.intersection(keys):
                        self.selected_keyframes = keys
                    self.drag_mode = "keyframe"
                    self.drag_key_time = key_time
                    self._begin_change()
                else:
                    self.selected_keyframes.clear()
                    self.drag_mode = "playhead"
                    self._set_playhead(event.pos[0], timeline)
                return
            if viewport.collidepoint(event.pos):
                point = self._screen_to_world(event.pos, canvas)
                if self.calibration and canvas.collidepoint(event.pos):
                    picked_led_id = self._pick_led_at_screen(event.pos, canvas)
                    if picked_led_id is None:
                        self.status = "Click directly on a LED marker to select it"
                        return
                    if self.led_move_ready_id == picked_led_id and self.selected_led_id == picked_led_id:
                        self._start_led_move()
                    else:
                        self._select_led_id(picked_led_id)
                        self.led_move_ready_id = picked_led_id
                        self.status = (
                            f"Selected LED {self._current_led().firmware_index} — click it again to move"
                        )
                    return
                handle = self._hit_selection_handle(event.pos, canvas)
                picked = self._pick(point) if canvas.collidepoint(event.pos) else None
                if handle or picked:
                    if picked and not handle:
                        if picked is not self.selected:
                            self.selected_keyframes.clear()
                        self.selected = picked
                        self.active_layer = next(
                            index for index, layer in enumerate(self.project.layers)
                            if picked in layer.shapes
                        )
                    self.drag_mode = handle or "move"
                    self.drag_origin = event.pos
                    self.drag_shape_state = dict(self.selected.state_at(self.current_ms)) if self.selected else None
                    self._begin_change()
                else:
                    self.selected = None
                    self.selected_keyframes.clear()
                return

        if event.type == pygame.MOUSEBUTTONUP:
            if event.button == 3 and self.drag_mode == "keyframe_marquee":
                self._finish_keyframe_right_gesture(event.pos, timeline)
                return
            if event.button in (1, 2):
                if self.drag_mode == "led_move":
                    return
                if self.tool_drag:
                    if viewport.collidepoint(event.pos):
                        self._create_shape(self.tool_drag, self._screen_to_world(event.pos, canvas))
                    elif pygame.Vector2(event.pos).distance_to(self.drag_origin) < 5:
                        self._create_shape(self.tool_drag, (0.5, 0.5))
                    self.tool_drag = None
                if self.drag_layer_index is not None:
                    self._drop_layer(event.pos, panel)
                    self.drag_layer_index = None
                open_stroke_input = (
                    self.drag_mode == "scrub"
                    and self.scrub_prop == "stroke_width"
                    and pygame.Vector2(event.pos).distance_to(self.drag_origin) < 4
                )
                if not self.duration_editing:
                    self._commit_change()
                if open_stroke_input and self.selected:
                    self.stroke_editing = True
                    self.stroke_input = f"{self.selected.state_at(self.current_ms).get('stroke_width', 0.012) * 100:.1f}"
                    self.status = "Type stroke width from 0.1 to 5.0, then press Enter"
                self.drag_mode = None
                self.scrub_prop = None
                self.drag_key_time = None
            return

        if event.type == pygame.MOUSEMOTION:
            if self.drag_mode == "pan":
                self.pan += pygame.Vector2(event.rel)
            elif self.drag_mode == "playhead":
                self._set_playhead(event.pos[0], timeline)
            elif self.drag_mode == "keyframe" and self.drag_key_time is not None:
                self._move_keyframe(event.pos[0], timeline)
            elif self.drag_mode == "keyframe_marquee":
                self.keyframe_marquee_current = event.pos
            elif self.drag_mode == "duration":
                self._set_duration_from_x(event.pos[0], self._duration_slider_rect(timeline))
            elif self.drag_mode == "led_move":
                self._move_selected_led(event.pos, canvas)
            elif self.drag_mode == "scrub":
                self._scrub_property(event.pos[0])
            elif self.drag_mode in ("move", "resize_nw", "resize_ne", "resize_sw", "resize_se", "rotate"):
                self._transform_selection(event.pos, canvas)
            return

        if event.type == pygame.MOUSEWHEEL:
            mouse = pygame.mouse.get_pos()
            if timeline.collidepoint(mouse):
                if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                    visible_ms = self._playback_duration() / self.timeline_zoom
                    self.timeline_scroll_ms -= event.y * visible_ms * 0.12
                    self._clamp_timeline_scroll()
                else:
                    self._zoom_timeline(event.y, mouse[0], timeline)
            elif viewport.collidepoint(mouse):
                old_point = self._screen_to_world(mouse, canvas)
                self.zoom = max(0.35, min(5.0, self.zoom * (1.12 ** event.y)))
                new_canvas, _, _ = self.layout()
                new_screen = self._world_to_screen(old_point, new_canvas)
                self.pan += pygame.Vector2(mouse) - pygame.Vector2(new_screen)

    def _handle_key(self, event: pygame.event.Event) -> None:
        ctrl = bool(event.mod & pygame.KMOD_CTRL)
        shift = bool(event.mod & pygame.KMOD_SHIFT)
        if ctrl and event.key == pygame.K_z:
            self._redo() if shift else self._undo()
            return
        if ctrl and event.key == pygame.K_y:
            self._redo()
            return
        if ctrl and event.key == pygame.K_d:
            self._action("duplicate_layer")
            return
        if ctrl and event.key == pygame.K_i:
            self._action("import_effect_data")
            return
        if self.calibration:
            if event.key == pygame.K_ESCAPE and self.drag_mode == "led_move":
                self._cancel_led_move()
            elif event.key in (pygame.K_TAB, pygame.K_RIGHT):
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
            self._action("save_as" if shift else "save")
        elif event.key == pygame.K_o and ctrl:
            self._action("load")
        elif event.key in (pygame.K_DELETE, pygame.K_BACKSPACE):
            if self.selected_keyframes:
                self._delete_selected_keyframes()
            else:
                self._action("delete")
        elif event.key == pygame.K_k:
            self._action("keyframe")
        elif event.key == pygame.K_v and self.selected:
            state = self.selected.state_at(self.current_ms)
            self._begin_change()
            self._set_animated("visible", not state["visible"])
            self._commit_change()
        elif event.key == pygame.K_LEFTBRACKET and self.selected:
            self._begin_change()
            self._set_animated("rotation", self.selected.state_at(self.current_ms)["rotation"] - 5)
            self._commit_change()
        elif event.key == pygame.K_RIGHTBRACKET and self.selected:
            self._begin_change()
            self._set_animated("rotation", self.selected.state_at(self.current_ms)["rotation"] + 5)
            self._commit_change()
        elif event.key == pygame.K_g:
            self.snap = not self.snap
            self.status = f"Snapping {'on' if self.snap else 'off'}"
        elif event.key == pygame.K_f:
            self.zoom = 1.0
            self.pan.update(0, 0)
        elif self.selected and event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
            step = 0.001 if shift else 0.005
            state = self.selected.state_at(self.current_ms)
            dx = (-step if event.key == pygame.K_LEFT else step if event.key == pygame.K_RIGHT else 0)
            dy = (-step if event.key == pygame.K_UP else step if event.key == pygame.K_DOWN else 0)
            self._begin_change()
            self._set_animated("x", max(0.0, min(1.0, state["x"] + dx)))
            self._set_animated("y", max(0.0, min(1.0, state["y"] + dy)))
            self._commit_change()

    def _action(self, action: str) -> None:
        mutating = (
            action.startswith(("add:", "color:", "fill_mode:", "toggle_layer:"))
            or action in {"layer", "duplicate_layer", "delete_layer", "delete", "duplicate", "keyframe"}
        )
        if mutating:
            self._begin_change()
        if action.startswith("add:"):
            self._create_shape(action.split(":", 1)[1], (0.5, 0.5), history=False)
        elif action == "layer":
            self.project.layers.append(Layer(f"Layer {len(self.project.layers) + 1}"))
            self.active_layer = len(self.project.layers) - 1
            self.selected = None
        elif action == "duplicate_layer":
            source = self.project.layers[self.active_layer]
            selected_index = source.shapes.index(self.selected) if self.selected in source.shapes else None
            clone = deepcopy(source)
            clone.id = uuid4().hex[:10]
            clone.name = f"{source.name} copy"
            for shape in clone.shapes:
                shape.id = uuid4().hex[:10]
            self.project.layers.insert(self.active_layer + 1, clone)
            self.active_layer += 1
            self.selected = clone.shapes[selected_index] if selected_index is not None else None
            self.selected_keyframes.clear()
            self.status = f"Duplicated layer: {source.name}"
        elif action == "delete_layer":
            if len(self.project.layers) == 1:
                self.status = "A project must keep at least one layer"
            else:
                removed = self.project.layers.pop(self.active_layer)
                if self.selected in removed.shapes:
                    self.selected = None
                    self.selected_keyframes.clear()
                self.active_layer = min(self.active_layer, len(self.project.layers) - 1)
                self.status = f"Deleted layer: {removed.name}"
        elif action == "delete" and self.selected:
            for layer in self.project.layers:
                if self.selected in layer.shapes:
                    layer.shapes.remove(self.selected)
                    break
            self.selected = None
            self.selected_keyframes.clear()
        elif action == "duplicate" and self.selected:
            for layer in self.project.layers:
                if self.selected in layer.shapes:
                    clone = deepcopy(self.selected)
                    clone.id = uuid4().hex[:10]
                    clone.name = f"{clone.name} copy"
                    clone.x = min(1.0, clone.x + 0.025)
                    clone.y = min(1.0, clone.y + 0.025)
                    layer.shapes.append(clone)
                    self.selected = clone
                    break
        elif action == "keyframe" and self.selected:
            state = self.selected.state_at(self.current_ms)
            for prop in (
                "x", "y", "width", "height", "rotation", "color", "opacity",
                "fill_mode", "stroke_width", "visible",
            ):
                self.selected.add_keyframe(prop, self.current_ms, state[prop])
            self.selected_keyframes = {
                (self.selected.id, prop, self.current_ms) for prop in self.selected.keyframes
                if any(frame.time_ms == self.current_ms for frame in self.selected.keyframes[prop])
            }
            self.status = f"Keyframe at {self.current_ms} ms"
        elif action == "stencil":
            self.stencil = not self.stencil
            if self.stencil:
                self.calibration = False
        elif action == "calibration":
            if self.calibration and self.drag_mode == "led_move":
                self._cancel_led_move()
            self.calibration = not self.calibration
            if self.calibration:
                self.stencil = False
                self.led_move_ready_id = None
                self._sync_led_fields()
            self.playing = False
            self.selected = None
        elif action == "play":
            self.playing = not self.playing
        elif action == "snap":
            self.snap = not self.snap
            self.status = f"Snapping {'on' if self.snap else 'off'}"
        elif action == "edit_duration":
            if self._active_imported_effect():
                self.status = "Imported effect length is defined by its firmware frames"
                return
            self.duration_editing = True
            self.duration_input = ""
            self._begin_change()
            self.status = "Type duration in seconds (0.5–15), then press Enter"
        elif action == "save":
            self._save_current_project()
        elif action == "save_as":
            self._choose_project_save()
        elif action == "load":
            self._choose_project_load()
        elif action == "import_effect_data":
            self._choose_effect_data()
        elif action == "next_imported_effect":
            if self.imported_effects:
                self.active_import_index = ((self.active_import_index or 0) + 1) % len(self.imported_effects)
                self.current_ms = 0
                self.timeline_scroll_ms = 0
                self.status = f"Previewing {self._active_imported_effect().name}"
        elif action == "project_preview":
            self.active_import_index = None
            self.current_ms = min(self.current_ms, self.project.duration_ms)
            self.timeline_scroll_ms = 0
            self.playing = False
            self.status = "Back to project animation"
        elif action == "export":
            errors = self.led_map.validate()
            if errors:
                self.status = "Export blocked: " + "; ".join(errors)
            else:
                export_arduino_header(self.project, self.led_map.export_slots(), EXPORT_FILE)
                self.status = f"Exported in firmware order: {EXPORT_FILE.name}"
        elif action == "edit_led_id":
            self.led_name_editing = False
            self.led_name_input = self._current_led().name
            self.led_id_editing = True
            self.led_id_input = ""
            self.status = f"Assign a firmware LED ID (0–{len(self.led_map.leds) - 1}), then press Enter"
        elif action == "edit_led_name":
            self.led_id_editing = False
            self.led_id_input = str(self._current_led().firmware_index)
            self.led_name_editing = True
            self.led_name_input = ""
            self.status = "Type a name; use NULL to disable this LED during export"
        elif action == "led_prev":
            self._step_led_id(-1)
        elif action == "led_next":
            self._step_led_id(1)
        elif action == "add_led":
            self._add_led()
        elif action == "delete_led":
            self._delete_led()
        elif action.startswith("led_index:"):
            self._step_firmware_index(int(action.split(":")[1]))
        elif action == "save_map":
            if self.led_id_editing and self.led_id_input:
                target = int(self.led_id_input)
                if 0 <= target < len(self.led_map.leds):
                    self.led_map.set_firmware_index(self.selected_led_id, target, swap=True)
                    self.led_id_editing = False
            if self.led_name_editing and self.led_name_input.strip():
                self._current_led().name = self.led_name_input.strip()
                self.led_name_editing = False
            self.led_map.mapping_status = "calibration_in_progress"
            self._sync_led_fields()
            self.led_map.save(self.led_map_path)
            self.status = "LED map IDs and names saved"
        elif action == "map_verified":
            errors = self.led_map.validate()
            if errors:
                self.status = "Cannot verify: " + "; ".join(errors)
            else:
                self.led_map.mapping_status = "hardware_verified"
                self.led_map.save(self.led_map_path)
                self.status = "LED map marked as hardware verified"
        elif action.startswith("color:") and self.selected:
            color = PALETTE[int(action.split(":")[1])]
            selected_times = sorted({
                time_ms for shape_id, _prop, time_ms in self.selected_keyframes
                if shape_id == self.selected.id
            })
            if selected_times:
                for time_ms in selected_times:
                    self.selected.add_keyframe("color", time_ms, color)
                    self.selected_keyframes.add((self.selected.id, "color", time_ms))
                self.status = f"Color applied to {len(selected_times)} selected keyframes"
            else:
                self._set_animated("color", color)
        elif action.startswith("fill_mode:") and self.selected:
            self._set_animated("fill_mode", action.split(":", 1)[1])
        elif action.startswith("select_layer:"):
            self.active_layer = int(action.split(":")[1])
            layer = self.project.layers[self.active_layer]
            if self.selected not in layer.shapes:
                self.selected = layer.shapes[-1] if layer.shapes else None
            self.selected_keyframes.clear()
            self.status = f"Active layer: {layer.name}"
        elif action.startswith("toggle_layer:"):
            index = int(action.split(":")[1])
            layer = self.project.layers[index]
            layer.visible = not layer.visible
            if not layer.visible and self.selected in layer.shapes:
                self.selected = None
                self.selected_keyframes.clear()
                self.status = f"Hidden layer: {layer.name}"
        if mutating:
            self._commit_change()

    def _begin_change(self) -> None:
        if self.change_snapshot is None:
            self.change_snapshot = deepcopy(self.project)

    def _commit_change(self) -> None:
        if self.change_snapshot is None:
            return
        if self.change_snapshot.to_dict() != self.project.to_dict():
            self.undo_stack.append(self.change_snapshot)
            self.undo_stack = self.undo_stack[-100:]
            self.redo_stack.clear()
        self.change_snapshot = None

    def _restore_project(self, project: Project) -> None:
        selected_id = self.selected.id if self.selected else None
        self.project = deepcopy(project)
        self.active_layer = min(self.active_layer, len(self.project.layers) - 1)
        self.selected = next(
            (shape for layer in self.project.layers for shape in layer.shapes if shape.id == selected_id),
            None,
        )
        if self.selected is None and self.project.layers[self.active_layer].shapes:
            self.selected = self.project.layers[self.active_layer].shapes[-1]
        self.selected_keyframes.clear()
        self.context_menu_pos = None

    def _undo(self) -> None:
        if not self.undo_stack:
            self.status = "Nothing to undo"
            return
        self.redo_stack.append(deepcopy(self.project))
        self._restore_project(self.undo_stack.pop())
        self.status = "Undo"

    def _redo(self) -> None:
        if not self.redo_stack:
            self.status = "Nothing to redo"
            return
        self.undo_stack.append(deepcopy(self.project))
        self._restore_project(self.redo_stack.pop())
        self.status = "Redo"

    def _create_shape(
        self, kind: str, point: tuple[float, float], history: bool = True
    ) -> None:
        if history:
            self._begin_change()
        x, y = point
        shape = Shape(
            kind,
            f"{kind.title()} {sum(len(layer.shapes) for layer in self.project.layers) + 1}",
            x=max(0.0, min(1.0, x)),
            y=max(0.0, min(1.0, y)),
            width=0.18,
            height=0.10,
        )
        if kind == "ellipse":
            shape.width = shape.height = 0.14
        elif kind == "line":
            shape.width, shape.height = 0.28, 0.025
        self.project.layers[self.active_layer].shapes.append(shape)
        self.selected = shape
        self.selected_keyframes.clear()
        self.status = f"Added {kind}"
        if history:
            self._commit_change()

    @staticmethod
    def _world_to_screen(
        point: tuple[float, float], canvas: pygame.Rect
    ) -> tuple[int, int]:
        return (
            canvas.x + round(point[0] * canvas.width),
            canvas.y + round(point[1] * canvas.height),
        )

    def _selection_geometry(
        self, canvas: pygame.Rect
    ) -> tuple[list[pygame.Vector2], pygame.Vector2] | None:
        if not self.selected:
            return None
        state = self.selected.state_at(self.current_ms)
        center = pygame.Vector2(self._world_to_screen((state["x"], state["y"]), canvas))
        half_w = state["width"] * canvas.width / 2
        half_h = state["height"] * canvas.height / 2
        angle = math.radians(state["rotation"])
        axis_x = pygame.Vector2(math.cos(angle), math.sin(angle))
        axis_y = pygame.Vector2(-math.sin(angle), math.cos(angle))
        corners = [
            center - axis_x * half_w - axis_y * half_h,
            center + axis_x * half_w - axis_y * half_h,
            center + axis_x * half_w + axis_y * half_h,
            center - axis_x * half_w + axis_y * half_h,
        ]
        rotate_handle = (corners[0] + corners[1]) / 2 - axis_y * 28
        return corners, rotate_handle

    def _hit_selection_handle(
        self, pos: tuple[int, int], canvas: pygame.Rect
    ) -> str | None:
        geometry = self._selection_geometry(canvas)
        if not geometry:
            return None
        corners, rotate_handle = geometry
        if pygame.Vector2(pos).distance_to(rotate_handle) <= 10:
            return "rotate"
        names = ("resize_nw", "resize_ne", "resize_se", "resize_sw")
        for name, corner in zip(names, corners):
            if pygame.Vector2(pos).distance_to(corner) <= 10:
                return name
        return None

    def _transform_selection(
        self, pos: tuple[int, int], canvas: pygame.Rect
    ) -> None:
        if not self.selected or not self.drag_shape_state:
            return
        state = self.drag_shape_state
        if self.drag_mode == "move":
            dx = (pos[0] - self.drag_origin[0]) / canvas.width
            dy = (pos[1] - self.drag_origin[1]) / canvas.height
            x, y = state["x"] + dx, state["y"] + dy
            if self.snap:
                x, y = round(x / 0.01) * 0.01, round(y / 0.01) * 0.01
            self._set_animated("x", max(0.0, min(1.0, x)))
            self._set_animated("y", max(0.0, min(1.0, y)))
            return

        center = pygame.Vector2(self._world_to_screen((state["x"], state["y"]), canvas))
        vector = pygame.Vector2(pos) - center
        if self.drag_mode == "rotate":
            angle = math.degrees(math.atan2(vector.y, vector.x)) + 90
            if self.snap:
                angle = round(angle / 15) * 15
            self._set_animated("rotation", angle % 360)
            return

        angle = math.radians(-state["rotation"])
        local = vector.rotate_rad(angle)
        width = max(0.01, abs(local.x) * 2 / canvas.width)
        height = max(0.01, abs(local.y) * 2 / canvas.height)
        if self.snap:
            width, height = round(width / 0.01) * 0.01, round(height / 0.01) * 0.01
        self._set_animated("width", width)
        self._set_animated("height", height)

    @staticmethod
    def _timeline_track(rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(rect.x + 180, rect.y + 35, max(80, rect.width - 195), rect.height - 48)

    def _duration_slider_rect(self, _timeline: pygame.Rect | None = None) -> pygame.Rect:
        right_edge = self.screen.get_width() - INSPECTOR_WIDTH
        return pygame.Rect(right_edge - 94, 19, 78, 16)

    def _duration_input_rect(self) -> pygame.Rect:
        right_edge = self.screen.get_width() - INSPECTOR_WIDTH
        return pygame.Rect(right_edge - 174, 11, 68, 32)

    def _timeline_window(self) -> tuple[float, float]:
        visible_ms = self._playback_duration() / self.timeline_zoom
        self._clamp_timeline_scroll()
        return self.timeline_scroll_ms, self.timeline_scroll_ms + visible_ms

    def _clamp_timeline_scroll(self) -> None:
        duration_ms = self._playback_duration()
        visible_ms = duration_ms / self.timeline_zoom
        maximum = max(0.0, duration_ms - visible_ms)
        self.timeline_scroll_ms = max(0.0, min(maximum, self.timeline_scroll_ms))

    def _time_to_timeline_x(self, time_ms: float, timeline: pygame.Rect) -> float:
        track = self._timeline_track(timeline)
        start, end = self._timeline_window()
        return track.x + (time_ms - start) / max(1.0, end - start) * track.width

    def _timeline_x_to_time(self, screen_x: int, timeline: pygame.Rect) -> int:
        track = self._timeline_track(timeline)
        start, end = self._timeline_window()
        ratio = max(0.0, min(1.0, (screen_x - track.x) / max(1, track.width)))
        return round(start + ratio * (end - start))

    def _zoom_timeline(self, wheel_y: int, screen_x: int, timeline: pygame.Rect) -> None:
        track = self._timeline_track(timeline)
        anchor_ratio = max(0.0, min(1.0, (screen_x - track.x) / max(1, track.width)))
        anchor_time = self._timeline_x_to_time(screen_x, timeline)
        self.timeline_zoom = max(1.0, min(20.0, self.timeline_zoom * (1.18 ** wheel_y)))
        visible_ms = self._playback_duration() / self.timeline_zoom
        self.timeline_scroll_ms = anchor_time - anchor_ratio * visible_ms
        self._clamp_timeline_scroll()

    def _set_duration_from_x(self, screen_x: int, slider: pygame.Rect) -> None:
        if self._active_imported_effect():
            return
        ratio = max(0.0, min(1.0, (screen_x - slider.x) / max(1, slider.width)))
        self.project.duration_ms = round((500 + ratio * 14500) / 100) * 100
        self.current_ms = min(self.current_ms, self.project.duration_ms)
        self.duration_input = f"{self.project.duration_ms / 1000:.1f}"
        self._clamp_timeline_scroll()
        self.status = f"Duration: {self.duration_input}s"

    def _set_playhead(self, screen_x: int, timeline: pygame.Rect) -> None:
        time_ms = self._timeline_x_to_time(screen_x, timeline)
        start, end = self._timeline_window()
        if self._timeline_uses_frame_ruler(timeline, end - start):
            time_ms = self._snap_time_to_frame(time_ms)
        self.current_ms = time_ms

    def _snap_time_to_frame(self, time_ms: int) -> int:
        frame_ms = self._timeline_frame_ms()
        snapped = round(round(time_ms / frame_ms) * frame_ms)
        return max(0, min(self._playback_duration(), snapped))

    def _keyframe_keys_at(self, time_ms: int) -> set[tuple[str, str, int]]:
        if not self.selected:
            return set()
        return {
            (self.selected.id, prop, time_ms)
            for prop, frames in self.selected.keyframes.items()
            if any(frame.time_ms == time_ms for frame in frames)
        }

    def _pick_keyframe(
        self, pos: tuple[int, int], timeline: pygame.Rect
    ) -> int | None:
        if not self.selected or self._active_imported_effect():
            return None
        row_y = self._selected_timeline_row_y(timeline)
        if row_y is None or abs(pos[1] - row_y) > 12:
            return None
        start, end = self._timeline_window()
        times = {frame.time_ms for frames in self.selected.keyframes.values() for frame in frames}
        for time_ms in times:
            if not start <= time_ms <= end:
                continue
            x = self._time_to_timeline_x(time_ms, timeline)
            if abs(pos[0] - x) <= 9:
                return time_ms
        return None

    def _selected_timeline_row_y(self, timeline: pygame.Rect) -> int | None:
        if not self.selected:
            return None
        layer_index = next(
            (index for index, layer in enumerate(self.project.layers[:5]) if self.selected in layer.shapes),
            None,
        )
        if layer_index is None:
            return None
        return self._timeline_track(timeline).y + 15 + layer_index * 32

    def _keyframes_in_marquee(
        self, start_pos: tuple[int, int], end_pos: tuple[int, int], timeline: pygame.Rect
    ) -> set[tuple[str, str, int]]:
        if not self.selected:
            return set()
        row_y = self._selected_timeline_row_y(timeline)
        if row_y is None:
            return set()
        left, right = sorted((start_pos[0], end_pos[0]))
        top, bottom = sorted((start_pos[1], end_pos[1]))
        marquee = pygame.Rect(left, top, max(1, right - left), max(1, bottom - top))
        start, end = self._timeline_window()
        selected: set[tuple[str, str, int]] = set()
        times = {frame.time_ms for frames in self.selected.keyframes.values() for frame in frames}
        for time_ms in times:
            if not start <= time_ms <= end:
                continue
            x = round(self._time_to_timeline_x(time_ms, timeline))
            if marquee.colliderect(pygame.Rect(x - 8, row_y - 8, 16, 16)):
                selected.update(self._keyframe_keys_at(time_ms))
        return selected

    def _finish_keyframe_right_gesture(
        self, pos: tuple[int, int], timeline: pygame.Rect
    ) -> None:
        moved = pygame.Vector2(pos).distance_to(self.drag_origin) >= 5
        if moved:
            keys = self._keyframes_in_marquee(self.drag_origin, pos, timeline)
            if self.keyframe_marquee_additive:
                self.selected_keyframes.update(keys)
            else:
                self.selected_keyframes = keys
            count = len({time_ms for _shape_id, _prop, time_ms in self.selected_keyframes})
            self.status = f"Selected {count} keyframe positions"
        elif self.keyframe_right_click_time is not None:
            keys = self._keyframe_keys_at(self.keyframe_right_click_time)
            if self.keyframe_marquee_additive:
                if self.selected_keyframes.intersection(keys):
                    self.selected_keyframes.difference_update(keys)
                else:
                    self.selected_keyframes.update(keys)
            else:
                if not self.selected_keyframes.intersection(keys):
                    self.selected_keyframes = keys
                self.context_menu_pos = pos
        elif not self.keyframe_marquee_additive:
            self.selected_keyframes.clear()
        self.drag_mode = None
        self.keyframe_marquee_current = None
        self.keyframe_right_click_time = None
        self.keyframe_marquee_additive = False

    def _move_keyframe(self, screen_x: int, timeline: pygame.Rect) -> None:
        if not self.selected or self.drag_key_time is None:
            return
        new_time = self._timeline_x_to_time(screen_x, timeline)
        start, end = self._timeline_window()
        if self.snap or self._timeline_uses_frame_ruler(timeline, end - start):
            new_time = self._snap_time_to_frame(new_time)
        delta = new_time - self.drag_key_time
        keys = self.selected_keyframes or self._keyframe_keys_at(self.drag_key_time)
        selected_times = [time_ms for shape_id, _prop, time_ms in keys if shape_id == self.selected.id]
        if selected_times:
            delta = max(-min(selected_times), min(self.project.duration_ms - max(selected_times), delta))
        updated: set[tuple[str, str, int]] = set()
        for shape_id, prop, old_time in keys:
            if shape_id != self.selected.id:
                continue
            for frame in self.selected.keyframes.get(prop, []):
                if frame.time_ms == old_time:
                    frame.time_ms = old_time + delta
                    updated.add((shape_id, prop, frame.time_ms))
            self.selected.keyframes.get(prop, []).sort(key=lambda frame: frame.time_ms)
        self.selected_keyframes = updated
        self.drag_key_time += delta
        self.current_ms = self.drag_key_time

    def _delete_selected_keyframes(self) -> None:
        if not self.selected_keyframes:
            return
        self._begin_change()
        for shape_id, prop, time_ms in tuple(self.selected_keyframes):
            shape = next(
                (item for layer in self.project.layers for item in layer.shapes if item.id == shape_id),
                None,
            )
            if not shape or prop not in shape.keyframes:
                continue
            shape.keyframes[prop] = [
                frame for frame in shape.keyframes[prop] if frame.time_ms != time_ms
            ]
            if not shape.keyframes[prop]:
                del shape.keyframes[prop]
        self.selected_keyframes.clear()
        self._commit_change()
        self.status = "Selected keyframe deleted"

    def _apply_keyframe_context(self, action: str) -> None:
        if action == "delete":
            self._delete_selected_keyframes()
            return
        self._begin_change()
        for shape_id, prop, time_ms in self.selected_keyframes:
            shape = next(
                (item for layer in self.project.layers for item in layer.shapes if item.id == shape_id),
                None,
            )
            if not shape:
                continue
            for frame in shape.keyframes.get(prop, []):
                if frame.time_ms == time_ms:
                    frame.easing = action
        self._commit_change()
        self.status = action.replace("_", " ").title()

    def _context_menu_items(self) -> list[tuple[pygame.Rect, str, str]]:
        if not self.context_menu_pos:
            return []
        labels = [
            ("linear", "Linear"), ("ease_in", "Ease In"), ("ease_out", "Ease Out"),
            ("ease_in_out", "Ease In / Out"), ("delete", "Delete keyframe"),
        ]
        width, row_height = 150, 28
        x = min(self.context_menu_pos[0], self.screen.get_width() - width - 6)
        y = min(self.context_menu_pos[1], self.screen.get_height() - row_height * len(labels) - 6)
        return [
            (pygame.Rect(x, y + index * row_height, width, row_height), action, label)
            for index, (action, label) in enumerate(labels)
        ]

    def _context_action_at(self, pos: tuple[int, int]) -> str | None:
        return next((action for rect, action, _ in self._context_menu_items() if rect.collidepoint(pos)), None)

    def _drop_layer(self, pos: tuple[int, int], panel: pygame.Rect) -> None:
        if self.drag_layer_index is None or not panel.collidepoint(pos):
            return
        first_row_y = panel.y + 94
        target = max(0, min(len(self.project.layers) - 1, (pos[1] - first_row_y) // 31))
        source = self.drag_layer_index
        if target == source:
            return
        layer = self.project.layers.pop(source)
        self.project.layers.insert(target, layer)
        self.active_layer = target
        self.status = f"Moved layer to position {target + 1}"

    def _scrub_property(self, screen_x: int) -> None:
        if not self.selected or not self.scrub_prop or not self.drag_shape_state:
            return
        prop = self.scrub_prop
        start = self.drag_shape_state[prop]
        delta = screen_x - self.drag_origin[0]
        sensitivity = 0.002 if prop in {"x", "y", "width", "height", "opacity"} else 0.5
        if prop == "stroke_width":
            sensitivity = 0.00025
        value = start + delta * sensitivity
        if prop in {"x", "y", "opacity"}:
            value = max(0.0, min(1.0, value))
        elif prop in {"width", "height"}:
            value = max(0.01, value)
        elif prop == "stroke_width":
            value = max(0.001, min(0.05, value))
        elif prop == "rotation":
            value %= 360
        if self.snap:
            step = 0.001 if prop == "stroke_width" else 0.01 if prop != "rotation" else 1
            value = round(value / step) * step
        self._set_animated(prop, value)

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

    def _current_led(self):
        return next(led for led in self.led_map.leds if led.id == self.selected_led_id)

    def _active_imported_effect(self) -> ImportedEffect | None:
        if self.active_import_index is None or not self.imported_effects:
            return None
        return self.imported_effects[self.active_import_index]

    def _playback_duration(self) -> int:
        effect = self._active_imported_effect()
        return effect.duration_ms if effect else self.project.duration_ms

    def load_effect_data_file(self, path: str | Path) -> None:
        path = Path(path)
        self.imported_effects = load_effect_data(path)
        self.effect_data_path = path
        self.active_import_index = 0
        self.current_ms = 0
        self.timeline_scroll_ms = 0
        self.timeline_zoom = 1.0
        self.playing = False
        self.stencil = True
        self.calibration = False
        self.selected_keyframes.clear()
        self.status = f"Loaded {len(self.imported_effects)} firmware effects — {self.imported_effects[0].name}"

    def _project_is_dirty(self) -> bool:
        return self.saved_project_state is None or self.project.to_dict() != self.saved_project_state

    def save_project_file(self, path: str | Path) -> None:
        target = Path(path)
        if target.suffix.lower() != ".cnclight":
            target = target.with_suffix(".cnclight")
        self.project.save(target)
        self.project_path = target.resolve()
        self.saved_project_state = deepcopy(self.project.to_dict())
        self.status = f"Saved project: {target.name}"

    def load_project_file(self, path: str | Path) -> None:
        target = Path(path)
        project = Project.load(target)
        self.project = project
        self.project_path = target.resolve()
        self.saved_project_state = deepcopy(project.to_dict())
        self.selected = None
        self.selected_keyframes.clear()
        self.active_layer = 0
        self.current_ms = 0
        self.playing = False
        self.active_import_index = None
        self.duration_input = f"{self.project.duration_ms / 1000:.1f}"
        self.timeline_zoom = 1.0
        self.timeline_scroll_ms = 0.0
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.change_snapshot = None
        self.status = f"Loaded project: {target.name}"

    def _save_current_project(self) -> None:
        if self.project_path is None:
            self._choose_project_save()
            return
        try:
            self.save_project_file(self.project_path)
        except (OSError, TypeError, ValueError) as error:
            self.status = f"Project save failed: {error}"

    def _choose_project_save(self) -> None:
        root = None
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except tk.TclError:
                pass
            selected = filedialog.asksaveasfilename(
                title="Save CnC Light project",
                initialdir=str(PROJECT_FILE.parent),
                initialfile=self.project_path.name if self.project_path else PROJECT_FILE.name,
                defaultextension=".cnclight",
                filetypes=[("CnC Light project", "*.cnclight"), ("All files", "*.*")],
            )
            if selected:
                self.save_project_file(selected)
            else:
                self.status = "Project save cancelled"
        except Exception as error:
            self.status = f"Project save failed: {error}"
        finally:
            if root is not None:
                root.destroy()

    def _choose_project_load(self) -> None:
        root = None
        try:
            import tkinter as tk
            from tkinter import filedialog, messagebox

            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except tk.TclError:
                pass
            if self._project_is_dirty() and not messagebox.askyesno(
                "Unsaved project",
                "The current project has unsaved changes. Open another project anyway?",
                parent=root,
            ):
                self.status = "Project load cancelled — current project kept"
                return
            selected = filedialog.askopenfilename(
                title="Open CnC Light project",
                initialdir=str(self.project_path.parent if self.project_path else PROJECT_FILE.parent),
                filetypes=[("CnC Light project", "*.cnclight"), ("All files", "*.*")],
            )
            if selected:
                self.load_project_file(selected)
            else:
                self.status = "Project load cancelled"
        except Exception as error:
            self.status = f"Project load failed: {error}"
        finally:
            if root is not None:
                root.destroy()

    def _choose_effect_data(self) -> None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            initial = Path(r"F:\Projects\cheech and chong\firmware\CnC_firmware4")
            selected = filedialog.askopenfilename(
                title="Open firmware effect_data.h",
                initialdir=str(initial if initial.exists() else ROOT),
                filetypes=[("Arduino header", "*.h"), ("All files", "*.*")],
            )
            root.destroy()
            if selected:
                self.load_effect_data_file(selected)
        except Exception as error:
            self.status = f"Effect import failed: {error}"

    def _move_selected_led(self, pos: tuple[int, int], canvas: pygame.Rect) -> None:
        x, y = self._screen_to_world(pos, canvas)
        led = self._current_led()
        led.x = round(max(0.0, min(1.0, x)) * self.led_map.width)
        led.y = round(max(0.0, min(1.0, y)) * self.led_map.height)
        self.led_points = self.led_map.normalized_points()
        self.led_map.mapping_status = "calibration_in_progress"

    def _start_led_move(self) -> None:
        led = self._current_led()
        self.led_move_origin = (led.x, led.y)
        self.drag_mode = "led_move"
        self.status = f"Moving LED {led.firmware_index} — click to place, Esc to cancel"

    def _finish_led_move(self) -> None:
        led = self._current_led()
        self.drag_mode = None
        self.led_move_origin = None
        self.led_move_ready_id = led.id
        self.led_map.mapping_status = "calibration_in_progress"
        self.status = f"LED {led.firmware_index} placed — save the LED map"

    def _cancel_led_move(self) -> None:
        led = self._current_led()
        if self.led_move_origin is not None:
            led.x, led.y = self.led_move_origin
            self.led_points = self.led_map.normalized_points()
        self.drag_mode = None
        self.led_move_origin = None
        self.led_move_ready_id = led.id
        self.status = f"LED {led.firmware_index} movement cancelled"

    def _add_led(self) -> None:
        if len(self.led_map.leds) >= 68:
            self.status = "The firmware output is limited to 68 LED slots"
            return
        new_id = max((led.id for led in self.led_map.leds), default=-1) + 1
        new_index = len(self.led_map.leds)
        self.led_map.leds.append(Led(
            new_id, new_index, self.led_map.width / 2, self.led_map.height / 2,
            f"LED {new_index:02d}",
        ))
        self.led_points = self.led_map.normalized_points()
        self.led_map.mapping_status = "calibration_in_progress"
        self._select_led_id(new_id)
        self.led_move_ready_id = new_id
        self.status = f"Added LED {new_index} in the center — click it to move"

    def _delete_led(self) -> None:
        if len(self.led_map.leds) <= 1:
            self.status = "The LED map must keep at least one position"
            return
        removed = self._current_led()
        self.led_map.leds.remove(removed)
        for led in self.led_map.leds:
            if led.firmware_index > removed.firmware_index:
                led.firmware_index -= 1
        self.led_points = self.led_map.normalized_points()
        self.led_map.mapping_status = "calibration_in_progress"
        next_led = min(self.led_map.leds, key=lambda led: abs(led.id - removed.id))
        self._select_led_id(next_led.id)
        self.led_move_ready_id = next_led.id
        self.status = f"Deleted LED {removed.firmware_index} — save the LED map"

    def _sync_led_fields(self) -> None:
        led = self._current_led()
        self.led_id_input = str(led.firmware_index)
        self.led_name_input = led.name or f"LED {led.id:02d}"

    def _select_led_id(self, led_id: int) -> bool:
        if not any(led.id == led_id for led in self.led_map.leds):
            self.status = f"Unknown graphic position: {led_id}"
            return False
        self.selected_led_id = led_id
        self.led_id_editing = False
        self.led_name_editing = False
        self._sync_led_fields()
        return True

    def _step_led_id(self, delta: int) -> None:
        ids = sorted(led.id for led in self.led_map.leds)
        current = ids.index(self.selected_led_id)
        self._select_led_id(ids[(current + delta) % len(ids)])

    def _step_firmware_index(self, delta: int) -> None:
        target = (self._current_led().firmware_index + delta) % len(self.led_map.leds)
        led = next(item for item in self.led_map.leds if item.firmware_index == target)
        self._select_led_id(led.id)
        self.status = f"Selected firmware LED ID {target}"

    def _handle_led_id_input(self, event: pygame.event.Event) -> None:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            if not self.led_id_input:
                self.status = "Enter a firmware LED ID"
                return
            target = int(self.led_id_input)
            if not 0 <= target < len(self.led_map.leds):
                self.status = f"LED ID must be 0–{len(self.led_map.leds) - 1}"
                return
            led = self._current_led()
            self.led_map.set_firmware_index(led.id, target, swap=True)
            self.led_map.mapping_status = "calibration_in_progress"
            self.led_id_editing = False
            self._sync_led_fields()
            self.status = f"Graphic position {led.id} assigned to LED ID {target}"
            return
        if event.key == pygame.K_ESCAPE:
            self.led_id_editing = False
            self._sync_led_fields()
            self.status = "LED ID edit cancelled"
            return
        if event.key == pygame.K_BACKSPACE:
            self.led_id_input = self.led_id_input[:-1]
            return
        character = getattr(event, "unicode", "")
        if character.isdigit() and len(self.led_id_input) < 3:
            self.led_id_input += character

    def _handle_led_name_input(self, event: pygame.event.Event) -> None:
        if event.key == pygame.K_v and getattr(event, "mod", pygame.key.get_mods()) & pygame.KMOD_CTRL:
            pasted = self._clipboard_text()
            if pasted:
                pasted = " ".join(pasted.replace("\x00", "").splitlines()).strip()
                remaining = 28 - len(self.led_name_input)
                self.led_name_input += pasted[:max(0, remaining)]
                self.status = "Pasted LED name — press Enter to apply"
            else:
                self.status = "Clipboard contains no text"
            return
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            name = self.led_name_input.strip()
            if not name:
                self.status = "LED name cannot be empty"
                return
            self._current_led().name = name
            self.led_name_editing = False
            self.led_map.mapping_status = "calibration_in_progress"
            self.status = (
                "LED excluded from export" if name.upper() == "NULL"
                else f"LED name saved: {name}"
            )
            return
        if event.key == pygame.K_ESCAPE:
            self.led_name_editing = False
            self._sync_led_fields()
            self.status = "LED name edit cancelled"
            return
        if event.key == pygame.K_BACKSPACE:
            self.led_name_input = self.led_name_input[:-1]
            return
        character = getattr(event, "unicode", "")
        if character.isprintable() and len(self.led_name_input) < 28:
            self.led_name_input += character

    @staticmethod
    def _clipboard_text() -> str:
        try:
            if not pygame.scrap.get_init():
                pygame.scrap.init()
            raw = pygame.scrap.get(pygame.SCRAP_TEXT)
        except pygame.error:
            return ""
        if not raw:
            return ""
        if isinstance(raw, str):
            return raw
        encodings = ("utf-16-le", "utf-8-sig", "mbcs") if raw.count(b"\x00") > 1 else ("utf-8-sig", "mbcs", "utf-16-le")
        for encoding in encodings:
            try:
                return raw.decode(encoding).rstrip("\x00")
            except (UnicodeDecodeError, LookupError):
                continue
        return ""

    def _handle_duration_input(self, event: pygame.event.Event) -> None:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            try:
                seconds = float(self.duration_input.replace(",", "."))
            except ValueError:
                self.status = "Duration must be a number"
                return
            if not 0.5 <= seconds <= 15.0:
                self.status = "Duration must be between 0.5 and 15 seconds"
                return
            self.project.duration_ms = round(seconds * 10) * 100
            self.current_ms = min(self.current_ms, self.project.duration_ms)
            self.duration_input = f"{self.project.duration_ms / 1000:.1f}"
            self.duration_editing = False
            self._clamp_timeline_scroll()
            self._commit_change()
            self.status = f"Duration: {self.duration_input}s"
            return
        if event.key == pygame.K_ESCAPE:
            self.duration_editing = False
            self.duration_input = f"{self.project.duration_ms / 1000:.1f}"
            self._commit_change()
            self.status = "Duration edit cancelled"
            return
        if event.key == pygame.K_BACKSPACE:
            self.duration_input = self.duration_input[:-1]
            return
        character = getattr(event, "unicode", "")
        if (character.isdigit() or character in ".,") and len(self.duration_input) < 5:
            if character in ".," and any(mark in self.duration_input for mark in ".,"):
                return
            self.duration_input += character

    def _handle_stroke_input(self, event: pygame.event.Event) -> None:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            try:
                percent = float(self.stroke_input.replace(",", "."))
            except ValueError:
                self.status = "Stroke width must be a number"
                return
            if not 0.1 <= percent <= 5.0:
                self.status = "Stroke width must be between 0.1 and 5.0"
                return
            self._begin_change()
            self._set_animated("stroke_width", percent / 100.0)
            self._commit_change()
            self.stroke_editing = False
            self.status = f"Stroke width: {percent:.1f} / 5.0"
            return
        if event.key == pygame.K_ESCAPE:
            self.stroke_editing = False
            self.status = "Stroke width edit cancelled"
            return
        if event.key == pygame.K_BACKSPACE:
            self.stroke_input = self.stroke_input[:-1]
            return
        character = getattr(event, "unicode", "")
        if (character.isdigit() or character in ".,") and len(self.stroke_input) < 5:
            if character in ".," and any(mark in self.stroke_input for mark in ".,"):
                return
            self.stroke_input += character

    def _pick_led(self, point: tuple[float, float]) -> int:
        best = min(
            zip(self.led_map.leds, self.led_points),
            key=lambda item: (item[1][0] - point[0]) ** 2 + (item[1][1] - point[1]) ** 2,
        )
        return best[0].id

    def _pick_led_at_screen(self, pos: tuple[int, int], canvas: pygame.Rect) -> int | None:
        candidates = [
            (led.id, pygame.Vector2(pos).distance_to(self._world_to_screen(point, canvas)))
            for led, point in zip(self.led_map.leds, self.led_points)
        ]
        led_id, distance = min(candidates, key=lambda item: item[1])
        hit_radius = max(12, min(22, canvas.width // 45))
        return led_id if distance <= hit_radius else None

    def draw(self) -> None:
        self._update_window_caption()
        self.screen.fill((20, 22, 28))
        canvas, panel, timeline = self.layout()
        viewport = pygame.Rect(TOOLBAR_WIDTH, TOP_BAR, panel.x - TOOLBAR_WIDTH, timeline.y - TOP_BAR)
        self.buttons.clear()
        pygame.draw.rect(self.screen, PANEL_DARK, viewport)
        self._draw_toolbar()

        old_clip = self.screen.get_clip()
        self.screen.set_clip(viewport)
        if self.stencil:
            self._draw_leds(canvas, stencil_back=True)
            artwork = pygame.transform.smoothscale(self.playfield, canvas.size)
            self.screen.blit(artwork, canvas)
        else:
            guide = pygame.transform.smoothscale(self.layout_guide, canvas.size)
            self.screen.blit(guide, canvas)
            self._draw_shapes(canvas)
            self._draw_selection(canvas)
            self._draw_leds(canvas)
        pygame.draw.rect(self.screen, (89, 96, 112), canvas, 1)
        self.screen.set_clip(old_clip)

        self._draw_timeline(timeline)
        self._draw_panel(panel)
        if self.tool_drag:
            mouse = pygame.mouse.get_pos()
            pygame.draw.circle(self.screen, ACCENT, mouse, 18, 2)
            label = self.small.render(self.tool_drag.title(), True, (235, 240, 250))
            self.screen.blit(label, (mouse[0] + 22, mouse[1] - 8))
        self._draw_context_menu()

    def _update_window_caption(self) -> None:
        project_label = self.project_path.name if self.project_path else self.project.name
        dirty = " *" if self._project_is_dirty() else ""
        caption = f"CnC Light Editor — {project_label}{dirty}"
        if caption != self.last_window_caption:
            pygame.display.set_caption(caption)
            self.last_window_caption = caption

    def _draw_toolbar(self) -> None:
        width = self.screen.get_width()
        pygame.draw.rect(self.screen, (38, 41, 50), (0, 0, width, TOP_BAR))
        pygame.draw.line(self.screen, (62, 66, 78), (0, TOP_BAR - 1), (width, TOP_BAR - 1))
        self.screen.blit(self.small.render("CnC  LIGHT COMPOSER", True, (235, 238, 245)), (16, 8))
        project_label = self.project_path.name if self.project_path else "Untitled project"
        project_label = project_label if len(project_label) <= 25 else project_label[:22] + "..."
        if self._project_is_dirty():
            project_label += "  •"
        self.screen.blit(self.small.render(project_label, True, (108, 180, 255)), (16, 29))
        items = [
            ("play", "Pause" if self.playing else "Play"),
            ("keyframe", "+ Keyframe"),
            ("stencil", "Stencil"),
            ("calibration", "LED map"),
            ("save", "Save*" if self._project_is_dirty() else "Save"),
            ("load", "Load"),
            ("export", "Export"),
        ]
        x = 238
        for action, label in items:
            button_width = max(58, self.small.size(label)[0] + 20)
            rect = pygame.Rect(x, 11, button_width, 32)
            active = (action == "stencil" and self.stencil) or (action == "calibration" and self.calibration)
            self._button(rect, action, label, active)
            x += button_width + 7

        duration_field = self._duration_input_rect()
        imported = self._active_imported_effect()
        label = self.small.render("Length", True, (154, 162, 180))
        self.screen.blit(label, (duration_field.x - label.get_width() - 7, 20))
        pygame.draw.rect(self.screen, (35, 39, 48), duration_field, border_radius=4)
        pygame.draw.rect(
            self.screen, ACCENT if self.duration_editing else (76, 82, 98),
            duration_field, 1, border_radius=4,
        )
        duration_value = (
            self.duration_input if self.duration_editing
            else f"{self._playback_duration() / 1000:.2f}" if imported
            else f"{self.project.duration_ms / 1000:.1f}"
        )
        if self.duration_editing and (pygame.time.get_ticks() // 500) % 2 == 0:
            duration_value += "|"
        value_surface = self.small.render(f"{duration_value}s", True, (235, 238, 245))
        self.screen.blit(value_surface, value_surface.get_rect(center=duration_field.center))
        if not imported:
            self.buttons.append((duration_field, "edit_duration", "Duration"))

        duration_slider = self._duration_slider_rect()
        pygame.draw.line(
            self.screen, (58, 63, 76) if imported else (91, 98, 115),
            duration_slider.midleft, duration_slider.midright, 4,
        )
        if not imported:
            ratio = (self.project.duration_ms - 500) / 14500
            handle_x = duration_slider.x + round(max(0.0, min(1.0, ratio)) * duration_slider.width)
            pygame.draw.circle(self.screen, ACCENT, (handle_x, duration_slider.centery), 7)
            self.buttons.append((duration_slider.inflate(0, 14), "duration_slider", "Duration"))

        pygame.draw.rect(self.screen, (34, 37, 46), (0, TOP_BAR, TOOLBAR_WIDTH, self.screen.get_height() - TOP_BAR))
        tools = [
            ("add:ellipse", "Circle", "O"),
            ("add:rectangle", "Rect", "R"),
            ("add:triangle", "Tri", "T"),
            ("add:line", "Line", "L"),
        ]
        y = TOP_BAR + 18
        for action, label, icon in tools:
            rect = pygame.Rect(10, y, 48, 48)
            hovered = rect.collidepoint(pygame.mouse.get_pos()) or self.tool_drag == action.split(":")[1]
            pygame.draw.rect(self.screen, (65, 78, 105) if hovered else (45, 49, 61), rect, border_radius=7)
            pygame.draw.rect(self.screen, ACCENT if hovered else (78, 84, 100), rect, 1, border_radius=7)
            glyph = self.title.render(icon, True, (238, 241, 248))
            self.screen.blit(glyph, glyph.get_rect(center=(rect.centerx, rect.centery - 3)))
            tip = self.small.render(label, True, (156, 163, 180))
            self.screen.blit(tip, tip.get_rect(center=(rect.centerx, rect.bottom + 10)))
            self.buttons.append((rect, action, label))
            y += 78

        zoom_text = self.small.render(f"{round(self.zoom * 100)}%", True, (165, 171, 187))
        self.screen.blit(zoom_text, zoom_text.get_rect(center=(TOOLBAR_WIDTH // 2, y + 12)))
        snap_rect = pygame.Rect(10, y + 34, 48, 31)
        self._button(snap_rect, "snap", "Snap", self.snap)
        import_rect = pygame.Rect(10, y + 73, 48, 31)
        self._button(import_rect, "import_effect_data", "Import", False)
        if self.imported_effects:
            self._button(pygame.Rect(10, y + 112, 48, 31), "next_imported_effect", "Next FX", imported is not None)
            self._button(pygame.Rect(10, y + 151, 48, 31), "project_preview", "Project", imported is None)

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
                color = (*state["color"], int(220 * state["opacity"]))
                local = surface.get_rect().inflate(-6, -6)
                stroke_px = max(1, round(state.get("stroke_width", 0.012) * min(canvas.size)))
                stroke_px = min(stroke_px, max(1, min(local.size) // 2))
                draw_width = stroke_px if state.get("fill_mode", "fill") == "stroke" else 0
                if shape.kind == "ellipse":
                    pygame.draw.ellipse(surface, color, local, draw_width)
                elif shape.kind == "rectangle":
                    pygame.draw.rect(surface, color, local, draw_width, border_radius=3)
                elif shape.kind == "triangle":
                    pygame.draw.polygon(
                        surface, color,
                        [(surface.get_width() // 2, 3), (surface.get_width() - 3, surface.get_height() - 3), (3, surface.get_height() - 3)],
                        draw_width,
                    )
                else:
                    line_width = stroke_px if state.get("fill_mode") == "stroke" else max(2, h - 6)
                    pygame.draw.line(surface, color, (4, surface.get_height() // 2), (surface.get_width() - 4, surface.get_height() // 2), line_width)
                rotated = pygame.transform.rotate(surface, -state["rotation"])
                rect = rotated.get_rect(center=(cx, cy))
                self.screen.blit(rotated, rect)

    def _draw_selection(self, canvas: pygame.Rect) -> None:
        geometry = self._selection_geometry(canvas)
        layer = next(
            (item for item in self.project.layers if self.selected in item.shapes),
            None,
        )
        if (
            not geometry or self.calibration or not layer or not layer.visible
            or not self.selected.state_at(self.current_ms)["visible"]
        ):
            return
        corners, rotate_handle = geometry
        points = [(round(point.x), round(point.y)) for point in corners]
        pygame.draw.polygon(self.screen, ACCENT, points, 2)
        top_mid = (corners[0] + corners[1]) / 2
        pygame.draw.line(self.screen, ACCENT, top_mid, rotate_handle, 1)
        for point in corners:
            rect = pygame.Rect(0, 0, 10, 10)
            rect.center = (round(point.x), round(point.y))
            pygame.draw.rect(self.screen, (238, 242, 250), rect)
            pygame.draw.rect(self.screen, ACCENT, rect, 2)
        pygame.draw.circle(
            self.screen, (238, 242, 250),
            (round(rotate_handle.x), round(rotate_handle.y)), 7,
        )
        pygame.draw.circle(
            self.screen, ACCENT,
            (round(rotate_handle.x), round(rotate_handle.y)), 7, 2,
        )

    def _preview_led_colors(self) -> list[tuple[int, int, int]]:
        effect = self._active_imported_effect()
        if not effect:
            return render_leds(self.project, self.led_points, self.current_ms)
        firmware_colors = effect.colors_at(self.current_ms)
        return [
            firmware_colors[led.firmware_index]
            if 0 <= led.firmware_index < len(firmware_colors) else (0, 0, 0)
            for led in self.led_map.leds
        ]

    def _timeline_frame_ms(self) -> float:
        imported = self._active_imported_effect()
        return float(imported.frame_ms) if imported else 1000.0 / max(1, self.project.fps)

    def _timeline_uses_frame_ruler(self, timeline: pygame.Rect, visible_ms: float) -> bool:
        track = self._timeline_track(timeline)
        pixels_per_frame = track.width * self._timeline_frame_ms() / max(1.0, visible_ms)
        return self._active_imported_effect() is not None or pixels_per_frame >= 18

    def _draw_leds(self, canvas: pygame.Rect, stencil_back: bool = False) -> None:
        colors = self._preview_led_colors()
        radius = max(3, min(9, canvas.width // 90))
        if stencil_back:
            glow_layer = pygame.Surface(canvas.size, pygame.SRCALPHA)
            light_radius = max(20, min(46, canvas.width // 10))
            for led, (x, y), rendered_color in zip(self.led_map.leds, self.led_points, colors):
                color = (0, 0, 0) if led.name.strip().upper() == "NULL" else rendered_color
                if not any(color):
                    continue
                diameter = light_radius * 2 + 2
                light = pygame.Surface((diameter, diameter), pygame.SRCALPHA)
                center = (diameter // 2, diameter // 2)
                rings = (
                    (light_radius, 0.10),
                    (round(light_radius * 0.78), 0.18),
                    (round(light_radius * 0.56), 0.34),
                    (round(light_radius * 0.34), 0.68),
                    (max(3, round(light_radius * 0.16)), 1.0),
                )
                for ring_radius, strength in rings:
                    pygame.draw.circle(light, tuple(round(channel * strength) for channel in color), center, ring_radius)
                local = (round(x * canvas.width - center[0]), round(y * canvas.height - center[1]))
                glow_layer.blit(light, local, special_flags=pygame.BLEND_RGB_ADD)
            self.screen.blit(glow_layer, canvas.topleft, special_flags=pygame.BLEND_RGB_ADD)
            return
        for led, (x, y), rendered_color in zip(self.led_map.leds, self.led_points, colors):
            color = (0, 0, 0) if led.name.strip().upper() == "NULL" else rendered_color
            pos = (canvas.x + int(x * canvas.width), canvas.y + int(y * canvas.height))
            if self.calibration:
                selected = led.id == self.selected_led_id
                disabled = led.name.strip().upper() == "NULL"
                marker = (255, 95, 90) if disabled else (255, 225, 55) if selected else (70, 205, 255)
                pygame.draw.circle(self.screen, marker, pos, radius + (4 if selected else 0), 0 if selected else 2)
            else:
                pygame.draw.circle(self.screen, (240, 70, 55), pos, radius, 2)
            if canvas.width > 620 or self.calibration:
                label = str(led.firmware_index)
                self.screen.blit(self.small.render(label, True, (245, 245, 245)), (pos[0] + radius, pos[1] - radius))

    def _draw_timeline(self, rect: pygame.Rect) -> None:
        pygame.draw.rect(self.screen, (29, 32, 40), rect)
        pygame.draw.line(self.screen, (67, 72, 86), rect.topleft, rect.topright)
        track = self._timeline_track(rect)
        pygame.draw.rect(self.screen, (22, 24, 30), track)
        pygame.draw.line(self.screen, (68, 73, 87), (track.x, track.y), (track.x, track.bottom))

        start, end = self._timeline_window()
        visible_ms = end - start
        frame_ruler = self._timeline_uses_frame_ruler(rect, visible_ms)
        frame_ms = self._timeline_frame_ms()
        self.screen.blit(self.font.render("TIMELINE", True, (220, 224, 234)), (rect.x + 12, rect.y + 9))
        timecode = f"F{int(self.current_ms // frame_ms):03d}" if frame_ruler else f"{self.current_ms / 1000:05.2f}s"
        self.screen.blit(self.font.render(timecode, True, (108, 180, 255)), (rect.x + 102, rect.y + 9))
        if frame_ruler:
            grid_text = (
                f"FRAME GRID  •  {frame_ms:g} ms  •  {self.timeline_zoom:.1f}x"
                if self._active_imported_effect() else
                f"FRAME GRID  •  {self.project.fps} FPS  •  {self.timeline_zoom:.1f}x"
            )
        else:
            grid_text = f"TIME GRID  •  {self.timeline_zoom:.1f}x"
        zoom_label = self.small.render(grid_text, True, (142, 151, 171))
        zoom_rect = zoom_label.get_rect(bottomright=(track.right - 3, rect.bottom - 7)).inflate(10, 5)

        if frame_ruler:
            pixels_per_frame = track.width * frame_ms / max(1.0, visible_ms)
            minor_step = max(1, math.ceil(5 / max(0.1, pixels_per_frame)))
            label_step = max(1, math.ceil(34 / max(0.1, pixels_per_frame)))
            first_minor = math.ceil(start / frame_ms / minor_step) * minor_step
            last_frame = math.floor(end / frame_ms)
            for frame_index in range(first_minor, last_frame + 1, minor_step):
                tick = frame_index * frame_ms
                x = round(self._time_to_timeline_x(tick, rect))
                pygame.draw.line(self.screen, (58, 63, 75), (x, rect.y + 27), (x, track.bottom), 1)
            first_label = math.ceil(start / frame_ms / label_step) * label_step
            for frame_index in range(first_label, last_frame + 1, label_step):
                tick = frame_index * frame_ms
                x = round(self._time_to_timeline_x(tick, rect))
                pygame.draw.line(self.screen, (82, 88, 103), (x, rect.y + 22), (x, track.bottom), 1)
                self.screen.blit(self.small.render(f"F{frame_index}", True, (145, 151, 166)), (x + 3, rect.y + 8))
        else:
            tick_ms = 1000 if visible_ms > 4000 else 500 if visible_ms > 2000 else 200 if visible_ms > 1000 else 100
            tick = math.ceil(start / tick_ms) * tick_ms
            while tick <= end:
                x = round(self._time_to_timeline_x(tick, rect))
                pygame.draw.line(self.screen, (72, 77, 91), (x, rect.y + 23), (x, track.bottom), 1)
                self.screen.blit(self.small.render(f"{tick / 1000:g}s", True, (145, 151, 166)), (x + 4, rect.y + 8))
                tick += tick_ms

        row_y = track.y + 15
        imported = self._active_imported_effect()
        if imported:
            row = pygame.Rect(rect.x, row_y - 11, rect.width, 31)
            pygame.draw.rect(self.screen, (42, 54, 76), row)
            pygame.draw.rect(self.screen, ACCENT, (row.x, row.y, 3, row.height))
            label = self.small.render(
                f"Firmware  /  {imported.name}  /  {len(imported.frames)} frames",
                True, (226, 230, 239),
            )
            self.screen.blit(label, (rect.x + 13, row_y - 3))
            for frame_index in range(len(imported.frames) + 1):
                frame_time = frame_index * imported.frame_ms
                if start <= frame_time <= end:
                    x = round(self._time_to_timeline_x(frame_time, rect))
                    pygame.draw.line(self.screen, (255, 184, 70), (x, row.y + 3), (x, row.bottom - 3), 1)
            row_y += 32
        for index, layer in enumerate([] if imported else self.project.layers[:5]):
            active = index == self.active_layer
            row = pygame.Rect(rect.x, row_y - 11, rect.width, 31)
            if active:
                pygame.draw.rect(self.screen, (42, 54, 76), row)
                pygame.draw.rect(self.screen, ACCENT, (row.x, row.y, 3, row.height))
            eye = "●" if layer.visible else "○"
            shape_suffix = f"  /  {self.selected.name}" if self.selected in layer.shapes else ""
            label = self.small.render(
                f"{eye}  {layer.name}{shape_suffix}",
                True, (226, 230, 239) if active else (166, 172, 187),
            )
            self.screen.blit(label, (rect.x + 13, row_y - 3))
            self.buttons.append((
                pygame.Rect(rect.x, row.y, 180, row.height),
                f"select_layer:{index}", layer.name,
            ))
            pygame.draw.line(self.screen, (47, 51, 61), (track.x, row.bottom), (track.right, row.bottom))
            if self.selected in layer.shapes:
                times = {frame.time_ms for frames in self.selected.keyframes.values() for frame in frames}
                for time_ms in times:
                    if not start <= time_ms <= end:
                        continue
                    x = round(self._time_to_timeline_x(time_ms, rect))
                    keys = self._keyframe_keys_at(time_ms)
                    selected_key = bool(keys.intersection(self.selected_keyframes))
                    color = (244, 248, 255) if selected_key else (255, 202, 70)
                    size = 9 if selected_key else 7
                    pygame.draw.polygon(
                        self.screen, color,
                        [(x, row_y - size), (x + size, row_y), (x, row_y + size), (x - size, row_y)],
                    )
                    if selected_key:
                        pygame.draw.polygon(
                            self.screen, ACCENT,
                            [(x, row_y - size), (x + size, row_y), (x, row_y + size), (x - size, row_y)], 2,
                        )
            row_y += 32

        play_x = round(self._time_to_timeline_x(self.current_ms, rect))
        if track.x <= play_x <= track.right:
            pygame.draw.polygon(self.screen, (255, 83, 72), [(play_x - 6, rect.y + 20), (play_x + 6, rect.y + 20), (play_x, rect.y + 29)])
            pygame.draw.line(self.screen, (255, 83, 72), (play_x, rect.y + 24), (play_x, track.bottom), 2)

        if self.drag_mode == "keyframe_marquee" and self.keyframe_marquee_current:
            left, right = sorted((self.drag_origin[0], self.keyframe_marquee_current[0]))
            top, bottom = sorted((self.drag_origin[1], self.keyframe_marquee_current[1]))
            marquee = pygame.Rect(left, top, max(1, right - left), max(1, bottom - top)).clip(track)
            if marquee.width and marquee.height:
                fill = pygame.Surface(marquee.size, pygame.SRCALPHA)
                fill.fill((77, 148, 255, 42))
                self.screen.blit(fill, marquee)
                pygame.draw.rect(self.screen, (104, 169, 255), marquee, 1)

        hint = (
            "Imported firmware preview • Space: play/pause • Next FX: cycle effects"
            if imported else
            "Right-drag: select keys • Ctrl: add • drag selected key: offset • right-click: menu"
        )
        self.screen.blit(self.small.render(hint, True, (132, 139, 155)), (track.x, rect.bottom - 22))
        pygame.draw.rect(self.screen, (29, 32, 40), zoom_rect, border_radius=3)
        self.screen.blit(zoom_label, zoom_label.get_rect(center=zoom_rect.center))

    def _draw_panel(self, panel: pygame.Rect) -> None:
        pygame.draw.rect(self.screen, PANEL, panel)
        pygame.draw.line(self.screen, (68, 72, 84), panel.topleft, panel.bottomleft)
        x = panel.x + 14
        self.screen.blit(self.font.render("INSPECTOR", True, (225, 229, 238)), (x, panel.y + 13))
        map_label = f"{len(self.led_map.leds)} LEDs  •  {self.led_map.mapping_status.replace('_', ' ')}"
        self.screen.blit(self.small.render(map_label, True, (255, 178, 77)), (x, panel.y + 43))
        pygame.draw.line(self.screen, (58, 62, 74), (panel.x, panel.y + 66), (panel.right, panel.y + 66))

        if self.calibration:
            self._draw_calibration_panel(panel, x, panel.y + 80)
            return

        y = panel.y + 72
        self.screen.blit(self.font.render("Layers", True, (218, 222, 232)), (x, y))
        self._button(pygame.Rect(panel.right - 110, y - 3, 28, 25), "duplicate_layer", "D", False)
        self._button(pygame.Rect(panel.right - 76, y - 3, 28, 25), "delete_layer", "−", False)
        self._button(pygame.Rect(panel.right - 42, y - 3, 28, 25), "layer", "+", False)
        y = panel.y + 94
        for index, layer in enumerate(self.project.layers[:6]):
            row = pygame.Rect(x, y, panel.width - 28, 27)
            if index == self.active_layer:
                pygame.draw.rect(self.screen, (47, 61, 85), row, border_radius=3)
                pygame.draw.rect(self.screen, ACCENT, (row.x, row.y, 3, row.height))
            eye = pygame.Rect(row.x + 5, row.y + 3, 24, 21)
            self._button(eye, f"toggle_layer:{index}", "●" if layer.visible else "○", False)
            name = self.small.render(f"≡   {layer.name}", True, (228, 232, 241))
            self.screen.blit(name, (row.x + 38, row.y + 6))
            self.buttons.append((pygame.Rect(row.x + 34, row.y, row.width - 34, row.height), f"drag_layer:{index}", layer.name))
            y += 31

        y += 10
        pygame.draw.line(self.screen, (58, 62, 74), (panel.x, y), (panel.right, y))
        y += 13
        self.screen.blit(self.font.render("Transform", True, (218, 222, 232)), (x, y))
        y += 31
        if self.selected:
            state = self.selected.state_at(self.current_ms)
            self.screen.blit(self.small.render(self.selected.name, True, (118, 184, 255)), (x, y))
            kind = self.small.render(self.selected.kind.upper(), True, (135, 141, 157))
            self.screen.blit(kind, (panel.right - kind.get_width() - 14, y))
            y += 27
            properties = [
                ("x", "X", state["x"]), ("y", "Y", state["y"]),
                ("width", "W", state["width"]), ("height", "H", state["height"]),
                ("rotation", "ROT", state["rotation"]), ("opacity", "OPACITY", state["opacity"]),
            ]
            for index, (prop, label, value) in enumerate(properties):
                col = index % 2
                row = index // 2
                field = pygame.Rect(x + col * 143, y + row * 37, 132, 30)
                hovered = field.collidepoint(pygame.mouse.get_pos())
                pygame.draw.rect(self.screen, (49, 54, 67) if hovered else (40, 44, 54), field, border_radius=4)
                pygame.draw.rect(self.screen, ACCENT if hovered else (67, 72, 86), field, 1, border_radius=4)
                value_text = f"{value:.1f}°" if prop == "rotation" else f"{value:.3f}"
                self.screen.blit(self.small.render(label, True, (130, 138, 157)), (field.x + 7, field.y + 7))
                rendered = self.small.render(value_text, True, (231, 234, 242))
                self.screen.blit(rendered, (field.right - rendered.get_width() - 7, field.y + 7))
                self.buttons.append((field, f"scrub:{prop}", label))
            y += 119
            self.screen.blit(self.small.render("Style", True, (172, 178, 193)), (x, y))
            y += 23
            self._button(
                pygame.Rect(x, y, 132, 29), "fill_mode:fill", "Fill",
                state.get("fill_mode", "fill") == "fill",
            )
            self._button(
                pygame.Rect(x + 143, y, 132, 29), "fill_mode:stroke", "Stroke",
                state.get("fill_mode", "fill") == "stroke",
            )
            y += 37
            stroke_field = pygame.Rect(x, y, 275, 30)
            pygame.draw.rect(self.screen, (40, 44, 54), stroke_field, border_radius=4)
            pygame.draw.rect(self.screen, (67, 72, 86), stroke_field, 1, border_radius=4)
            self.screen.blit(self.small.render("Stroke width", True, (148, 155, 173)), (stroke_field.x + 8, stroke_field.y + 7))
            stroke_percent = state.get("stroke_width", 0.012) * 100
            stroke_value = self.stroke_input if self.stroke_editing else f"{stroke_percent:.1f}"
            if self.stroke_editing and (pygame.time.get_ticks() // 500) % 2 == 0:
                stroke_value += "|"
            stroke_text = self.small.render(f"{stroke_value} / 5.0", True, (231, 234, 242))
            self.screen.blit(stroke_text, (stroke_field.right - stroke_text.get_width() - 8, stroke_field.y + 7))
            self.buttons.append((stroke_field, "scrub:stroke_width", "Stroke width"))
            y += 42
            self.screen.blit(self.small.render("Color", True, (172, 178, 193)), (x, y))
            y += 24
            for index, color in enumerate(PALETTE):
                rect = pygame.Rect(x + index * 34, y, 27, 27)
                pygame.draw.rect(self.screen, color, rect, border_radius=4)
                if tuple(state["color"]) == color:
                    pygame.draw.rect(self.screen, (245, 247, 252), rect.inflate(4, 4), 2, border_radius=5)
                self.buttons.append((rect, f"color:{index}", ""))
            y += 43
            self._button(pygame.Rect(x, y, 132, 30), "duplicate", "Duplicate", False)
            self._button(pygame.Rect(x + 143, y, 132, 30), "delete", "Delete", False)
        else:
            self.screen.blit(self.small.render("Select an object on the canvas.", True, (145, 152, 169)), (x, y))
            y += 28
            self.screen.blit(self.small.render("Drag a shape tool onto the playfield.", True, (116, 124, 142)), (x, y))

        pygame.draw.rect(self.screen, (25, 28, 35), (panel.x, panel.bottom - 47, panel.width, 47))
        self.screen.blit(self.small.render(self.status[:42], True, (100, 216, 162)), (x, panel.bottom - 29))

    def _draw_calibration_panel(self, panel: pygame.Rect, x: int, y: int) -> None:
        led = next(item for item in self.led_map.leds if item.id == self.selected_led_id)
        self.screen.blit(self.font.render("LED calibration", True, (220, 223, 232)), (x, y)); y += 32
        self.screen.blit(
            self.small.render(f"Graphic position: {led.id}", True, (150, 158, 177)),
            (x, y),
        )
        y += 29
        self.screen.blit(self.small.render("Assigned LED ID", True, (205, 209, 221)), (x, y))
        id_field = pygame.Rect(x + 160, y - 5, 94, 29)
        hovered = id_field.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(self.screen, (48, 55, 70) if self.led_id_editing else (39, 43, 53), id_field, border_radius=4)
        pygame.draw.rect(
            self.screen, ACCENT if self.led_id_editing or hovered else (73, 79, 94),
            id_field, 1, border_radius=4,
        )
        value = self.led_id_input if self.led_id_editing else str(led.firmware_index)
        if self.led_id_editing and (pygame.time.get_ticks() // 500) % 2 == 0:
            value += "|"
        value_surface = self.font.render(value or " ", True, (239, 242, 248))
        self.screen.blit(value_surface, (id_field.x + 9, id_field.y + 4))
        self.buttons.append((id_field, "edit_led_id", "Assigned LED ID"))
        y += 36

        self.screen.blit(self.small.render("Name", True, (205, 209, 221)), (x, y))
        name_field = pygame.Rect(x + 70, y - 5, 184, 29)
        name_hovered = name_field.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(self.screen, (48, 55, 70) if self.led_name_editing else (39, 43, 53), name_field, border_radius=4)
        pygame.draw.rect(
            self.screen, ACCENT if self.led_name_editing or name_hovered else (73, 79, 94),
            name_field, 1, border_radius=4,
        )
        name_value = self.led_name_input if self.led_name_editing else (led.name or f"LED {led.id:02d}")
        if self.led_name_editing and (pygame.time.get_ticks() // 500) % 2 == 0:
            name_value += "|"
        name_color = (255, 110, 100) if name_value.strip("|").upper() == "NULL" else (239, 242, 248)
        self.screen.blit(self.small.render(name_value or " ", True, name_color), (name_field.x + 8, name_field.y + 6))
        self.buttons.append((name_field, "edit_led_name", "LED name"))
        y += 35
        self.screen.blit(
            self.small.render(f"Pixel: {led.x:.0f}, {led.y:.0f}", True, (150, 158, 177)),
            (x, y),
        )
        y += 31
        buttons = [
            ("led_prev", "Position −"), ("led_next", "Position +"),
            ("led_index:-1", "LED ID −"), ("led_index:1", "LED ID +"),
        ]
        for index, (action, label) in enumerate(buttons):
            rect = pygame.Rect(x + (index % 2) * 132, y + (index // 2) * 38, 122, 30)
            self._button(rect, action, label, False)
        y += 82
        self._button(pygame.Rect(x, y, 122, 30), "add_led", "+ Add LED", False)
        self._button(pygame.Rect(x + 132, y, 122, 30), "delete_led", "− Delete LED", False)
        y += 38
        self._button(pygame.Rect(x, y, 254, 32), "save_map", "Save LED map", False); y += 40
        self._button(pygame.Rect(x, y, 254, 32), "map_verified", "Mark hardware verified", False); y += 52
        errors = self.led_map.validate()
        validity = "Mapping is contiguous and unique" if not errors else "; ".join(errors)
        validity_color = (95, 215, 160) if not errors else (255, 100, 90)
        self.screen.blit(self.small.render(validity, True, validity_color), (x, y)); y += 34
        tips = [
            "Click and drag a LED to move its position.",
            "Add creates a new LED in the center (max 68).",
            "LED ID −/+ walks through the physical chain.",
            "Name NULL excludes the slot from export.",
            "Save LED map writes IDs and names to JSON.",
            "Only verify after a hardware test.",
        ]
        for tip in tips:
            self.screen.blit(self.small.render(tip, True, (168, 173, 188)), (x, y)); y += 21
        self.screen.blit(self.small.render(self.status, True, (95, 215, 160)), (x, panel.bottom - 28))

    def _draw_context_menu(self) -> None:
        items = self._context_menu_items()
        if not items:
            return
        outer = items[0][0].unionall([item[0] for item in items[1:]]).inflate(4, 4)
        pygame.draw.rect(self.screen, (18, 20, 26), outer, border_radius=5)
        pygame.draw.rect(self.screen, (90, 97, 114), outer, 1, border_radius=5)
        mouse = pygame.mouse.get_pos()
        for rect, action, label in items:
            hovered = rect.collidepoint(mouse)
            destructive = action == "delete"
            if hovered:
                pygame.draw.rect(self.screen, (59, 76, 106), rect)
            color = (255, 118, 105) if destructive else (231, 234, 242)
            self.screen.blit(self.small.render(label, True, color), (rect.x + 10, rect.y + 7))

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
    parser.add_argument("--effect-data", type=Path, help="Load and preview an Arduino effect_data.h")
    args = parser.parse_args()
    if args.smoke_test:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.init()
    screen = pygame.display.set_mode((1280, 900), pygame.RESIZABLE)
    pygame.display.set_caption("CnC Pinball — Light Effect Editor")
    try:
        editor = Editor(screen)
        if args.effect_data:
            editor.load_effect_data_file(args.effect_data)
        editor.run(args.smoke_test, args.screenshot)
    finally:
        pygame.quit()


if __name__ == "__main__":
    main()
