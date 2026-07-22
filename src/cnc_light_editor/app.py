from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass, replace
import math
import os
from pathlib import Path
from uuid import uuid4

import pygame

from .engine import point_inside, render_leds, sample_gradient
from .effect_importer import ImportedEffect, load_effect_data
from .exporter import (
    EFFECT_BANK_CAPACITY,
    TRANSPARENT_SENTINEL,
    export_effect_bank,
    project_to_imported_effect,
)
from .ledmap import Led, LedMap
from .model import GradientStop, Keyframe, Layer, Project, RandomLedEffect, Shape

ROOT = Path(__file__).resolve().parents[2]
PROJECT_FILE = ROOT / "projects" / "current.cnclight"
EXPORT_FILE = ROOT / "exports" / "effect_data.h"
WINDOW_ICON = ROOT / "assets" / "CnC_LightE_ico.png"
PALETTE = [
    (255, 70, 40), (255, 155, 20), (255, 230, 50), (80, 220, 90),
    (30, 180, 255), (90, 90, 255), (210, 80, 255), (255, 255, 255),
]
BANK_COLORS = [
    (74, 151, 255), (118, 92, 246), (224, 82, 151), (255, 126, 64),
    (244, 190, 55), (74, 198, 126), (48, 188, 202), (150, 104, 218),
]
HELP_COLUMNS = (
    (
        ("PROJECT", (
            ("Ctrl + N / New", "Start a blank project"),
            ("Ctrl + S", "Save project"),
            ("Ctrl + Shift + S", "Save project as"),
            ("Ctrl + O", "Load project"),
            ("Ctrl + I", "Import effect_data.h"),
            ("Ctrl + Z", "Undo"),
            ("Ctrl + Shift + Z", "Redo"),
        )),
        ("TIMELINE / KEYFRAMES", (
            ("Space", "Play / pause"),
            ("K", "Add keyframe at playhead"),
            ("V", "Toggle shape or Canvas state"),
            ("Ctrl + C / V", "Copy / paste selected keyframes"),
            ("Delete", "Delete selected keyframes or shape"),
            ("G", "Toggle snapping"),
            ("Mouse wheel", "Zoom timeline"),
            ("Shift + wheel", "Scroll timeline horizontally"),
            ("Right-drag", "Marquee-select keyframes"),
        )),
    ),
    (
        ("LAYERS / CANVAS", (
            ("Ctrl + D", "Duplicate active layer"),
            ("C+ button", "Add the keyframeable Canvas layer"),
            ("Layer ON/OFF", "Toggle layer; Canvas creates a keyframe"),
            ("Enable / Disable", "Write an explicit Canvas state key"),
            ("Delete", "Delete a layer selected in the timeline"),
        )),
        ("VIEWPORT / SHAPES", (
            ("Arrow keys", "Move selected shape"),
            ("Shift + arrows", "Move selected shape precisely"),
            ("[  /  ]", "Rotate selected shape by 5 degrees"),
            ("F", "Fit / reset viewport"),
            ("Mouse wheel", "Zoom playfield"),
            ("Middle-drag", "Pan playfield"),
            ("Space + drag", "Pan playfield"),
        )),
        ("LED MAP", (
            ("Tab / Right", "Next LED position"),
            ("Left", "Previous LED position"),
            ("+  /  -", "Step firmware LED ID"),
            ("Ctrl + S", "Save LED map while LED map is open"),
            ("Esc", "Cancel LED movement"),
        )),
    ),
)

TOP_BAR = 54
TOOLBAR_WIDTH = 68
INSPECTOR_WIDTH = 318
TIMELINE_HEIGHT = 218
ACCENT = (77, 148, 255)
PANEL = (31, 34, 42)
PANEL_DARK = (24, 26, 33)

TIMELINE_PROPERTY_LABELS = {
    "x": "Position X", "y": "Position Y",
    "width": "Scale X", "height": "Scale Y",
    "rotation": "Rotation", "rotation_turns": "Turns", "color": "Color", "opacity": "Opacity",
    "feather": "Feather", "mask_expansion": "Mask expansion",
    "fill_mode": "Fill mode", "stroke_width": "Stroke width",
    "gradient_type": "Gradient type", "gradient_radial_mode": "Radial mode",
    "gradient_angle": "Gradient angle", "visible": "Visibility",
    "enabled": "Enabled",
    "canvas_enabled": "Canvas enabled",
}
GRAPH_NUMERIC_PROPERTIES = {
    "x", "y", "width", "height", "rotation", "rotation_turns", "opacity",
    "feather", "mask_expansion",
    "stroke_width", "gradient_angle",
}
DEFAULT_BEZIER = (0.25, 0.10, 0.25, 1.0)


@dataclass(frozen=True)
class TimelineRow:
    kind: str
    layer_index: int
    target_id: str | None = None
    prop: str | None = None


class Editor:
    _cached_playfield: pygame.Surface | None = None
    _cached_layout_guide: pygame.Surface | None = None

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
        self.property_editing: str | None = None
        self.property_input = ""
        self.property_input_select_all = False
        self.gradient_editor_open = False
        self.selected_gradient_stop_id: str | None = None
        self.random_led_editor_open = False
        self.random_effect_editing: str | None = None
        self.random_effect_input = ""
        self.random_effect_input_select_all = False
        self.random_effect_drag_start: float | int | None = None
        self.imported_effects: list[ImportedEffect] = []
        self.active_import_index: int | None = None
        self.effect_data_path: Path | None = None
        self.export_bank_open = False
        self.help_open = False
        self.export_bank_effects: list[ImportedEffect] = []
        self.export_bank_path: Path | None = None
        self.export_bank_selected = -1
        self.export_bank_scroll = 0
        self.export_bank_name_editing = False
        self.export_bank_name_input = ""
        self.export_bank_id_editing = False
        self.export_bank_id_input = ""
        self.export_bank_input_select_all = False
        self.export_bank_status = "Map an effect_data.h or export the current project"
        self.active_layer = 0
        self.selected: Shape | None = None
        self.drag_mode: str | None = None
        self.drag_origin = (0, 0)
        self.drag_shape_state: dict | None = None
        self.drag_key_time: int | None = None
        self.selected_keyframes: set[tuple[str, str, int]] = set()
        self.keyframe_clipboard: list[tuple[str, str, int, Keyframe]] = []
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
        self.timeline_layer_scroll = 0
        self.timeline_expanded_layers: set[str] = set()
        self.timeline_selected_layer_id: str | None = None
        self.timeline_mode = "dope"
        self.graph_target_id: str | None = None
        self.graph_prop: str | None = None
        self.graph_drag_records: list[tuple[Shape, str, Keyframe, int, float]] = []
        self.graph_drag_value_range: tuple[float, float] | None = None
        self.graph_drag_handle: tuple[str, str, int, int] | None = None
        self.undo_stack: list[Project] = []
        self.redo_stack: list[Project] = []
        self.change_snapshot: Project | None = None
        self.status = "Ready — 59 playfield LEDs"
        self.buttons: list[tuple[pygame.Rect, str, str]] = []
        self.led_map_path = ROOT / "data" / "led_map.json"
        self.led_map = LedMap.load(self.led_map_path)
        self.status = f"Ready — {len(self.led_map.leds)} playfield LEDs"
        self.led_points = self.led_map.normalized_points()
        if Editor._cached_playfield is None or Editor._cached_layout_guide is None:
            Editor._cached_playfield = pygame.image.load(
                str(ROOT / "assets" / "playfield.png")
            ).convert_alpha()
            guide_source = pygame.image.load(
                str(ROOT / "assets" / "playfield_layout.png")
            ).convert_alpha()
            guide_source = pygame.transform.smoothscale(guide_source, (2048, 990))
            guide_mask = pygame.mask.from_surface(guide_source, 40)
            guide = guide_mask.to_surface(
                setcolor=(225, 229, 238, 215),
                unsetcolor=(0, 0, 0, 0),
            ).convert_alpha()
            Editor._cached_layout_guide = pygame.transform.rotate(guide, -90)
        self.playfield = Editor._cached_playfield
        self.layout_guide = Editor._cached_layout_guide
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

        if event.type == pygame.KEYDOWN and event.key == pygame.K_F1:
            self.help_open = not self.help_open
            if self.help_open:
                self.export_bank_open = False
                self.playing = False
                self.status = "Hotkey reference opened"
            else:
                self.status = "Hotkey reference closed"
            return

        if self.help_open:
            self._handle_help_event(event)
            return

        if self.export_bank_open:
            self._handle_export_bank_event(event)
            return

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
            if self.random_effect_editing:
                self._handle_random_effect_input(event)
                return
            if self.property_editing:
                self._handle_property_input(event)
                return
            if event.key == pygame.K_ESCAPE and self.gradient_editor_open:
                self.gradient_editor_open = False
                self.drag_mode = None
                self.status = "Closed gradient editor"
                return
            if event.key == pygame.K_ESCAPE and self.random_led_editor_open:
                self.random_led_editor_open = False
                self.status = "Closed Random LED editor"
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
            if event.button == 1 and self.property_editing:
                self.property_editing = None
                self.property_input_select_all = False
            if event.button == 1 and self.random_effect_editing:
                self.random_effect_editing = None
                self.random_effect_input_select_all = False
            if event.button == 3:
                self.context_menu_pos = None
                if timeline.collidepoint(event.pos) and not self._active_imported_effect():
                    if self.timeline_mode == "graph":
                        keys = self._pick_graph_key(event.pos, timeline)
                        if keys:
                            self.timeline_selected_layer_id = None
                            if pygame.key.get_mods() & pygame.KMOD_CTRL:
                                self.selected_keyframes.symmetric_difference_update(keys)
                            elif not self.selected_keyframes.intersection(keys):
                                self.selected_keyframes = keys
                            if self.selected_keyframes:
                                self.context_menu_pos = event.pos
                        return
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
                        self.timeline_selected_layer_id = None
                        self.drag_origin = event.pos
                        self._begin_change()
                    elif action == "duration_slider":
                        self.drag_mode = "duration"
                        self.drag_origin = event.pos
                        self._begin_change()
                        self._set_duration_from_x(event.pos[0], self._duration_slider_rect(timeline))
                    elif action.startswith("scrub:") and self.selected:
                        self.scrub_prop = action.split(":", 1)[1]
                        self.property_editing = None
                        self.drag_mode = "scrub"
                        self.drag_origin = event.pos
                        self.drag_shape_state = dict(self.selected.state_at(self.current_ms))
                        self._begin_change()
                    elif action.startswith("random_led_field:"):
                        effect = self._active_random_led_effect()
                        if effect:
                            prop = action.split(":", 1)[1]
                            self.random_effect_editing = None
                            self.random_effect_input_select_all = False
                            self.scrub_prop = prop
                            self.drag_mode = "random_led_scrub"
                            self.drag_origin = event.pos
                            self.random_effect_drag_start = self._random_effect_parameter_value(
                                effect, prop, self.current_ms,
                            )
                            self._begin_change()
                    elif action.startswith("gradient_stop:") and self.selected:
                        self.selected_gradient_stop_id = action.split(":", 1)[1]
                        self.drag_mode = "gradient_stop"
                        self.drag_origin = event.pos
                        self._begin_change()
                    elif action == "gradient_bar" and self.selected:
                        self._begin_change()
                        self._add_gradient_stop_at(event.pos[0])
                        self.drag_mode = "gradient_stop"
                        self.drag_origin = event.pos
                    elif action == "gradient_angle" and self.selected:
                        self.drag_mode = "gradient_angle"
                        self.drag_origin = event.pos
                        self._begin_change()
                        self._set_gradient_angle_from_x(event.pos[0])
                    else:
                        self._action(action)
                    return
            if timeline.collidepoint(event.pos):
                if self.timeline_mode == "graph" and not self._active_imported_effect():
                    handle = self._pick_bezier_handle(event.pos, timeline)
                    if handle:
                        self._start_bezier_handle_drag(handle)
                        return
                    keys = self._pick_graph_key(event.pos, timeline)
                    if keys:
                        self.timeline_selected_layer_id = None
                        if pygame.key.get_mods() & pygame.KMOD_CTRL:
                            if self.selected_keyframes.intersection(keys):
                                self.selected_keyframes.difference_update(keys)
                                return
                            self.selected_keyframes.update(keys)
                        elif not self.selected_keyframes.intersection(keys):
                            self.selected_keyframes = keys
                        self._start_graph_drag(keys, event.pos)
                    else:
                        self.selected_keyframes.clear()
                        self.drag_mode = "playhead"
                        self._set_playhead(event.pos[0], timeline)
                    return
                keys = self._pick_keyframe_keys(event.pos, timeline)
                if keys:
                    self.timeline_selected_layer_id = None
                    key_time = next(iter(keys))[2]
                    self._remember_graph_channel(keys)
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
                self.timeline_selected_layer_id = None
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
                if self.drag_mode == "graph_keyframe":
                    self._finish_graph_drag()
                elif self.drag_mode == "bezier_handle":
                    self._finish_bezier_handle_drag()
                open_stroke_input = (
                    self.drag_mode == "scrub"
                    and self.scrub_prop == "stroke_width"
                    and pygame.Vector2(event.pos).distance_to(self.drag_origin) < 4
                )
                open_property_input = (
                    self.drag_mode == "scrub"
                    and self.scrub_prop in {
                        "x", "y", "width", "height", "rotation", "rotation_turns", "opacity",
                        "feather", "mask_expansion",
                    }
                    and pygame.Vector2(event.pos).distance_to(self.drag_origin) < 4
                )
                open_random_effect_input = (
                    self.drag_mode == "random_led_scrub"
                    and self.scrub_prop is not None
                    and pygame.Vector2(event.pos).distance_to(self.drag_origin) < 4
                )
                if not self.duration_editing:
                    self._commit_change()
                if open_stroke_input and self.selected:
                    self.stroke_editing = True
                    self.stroke_input = f"{self.selected.state_at(self.current_ms).get('stroke_width', 0.012) * 100:.1f}"
                    self.status = "Type stroke width from 0.1 to 5.0, then press Enter"
                elif open_property_input and self.selected and self.scrub_prop:
                    prop = self.scrub_prop
                    value = float(self.selected.state_at(self.current_ms)[prop])
                    self.property_editing = prop
                    self.property_input = (
                        f"{value:.1f}" if prop == "rotation"
                        else f"{value:.0f}" if prop == "rotation_turns"
                        else f"{value * 100:.1f}" if prop in {"feather", "mask_expansion"}
                        else f"{value:.3f}"
                    )
                    self.property_input_select_all = True
                    self.status = f"Type {TIMELINE_PROPERTY_LABELS[prop]}, then press Enter"
                elif open_random_effect_input and self.scrub_prop:
                    self._open_random_effect_input(self.scrub_prop)
                self.drag_mode = None
                self.scrub_prop = None
                self.drag_key_time = None
                self.random_effect_drag_start = None
            return

        if event.type == pygame.MOUSEMOTION:
            if self.drag_mode == "pan":
                self.pan += pygame.Vector2(event.rel)
            elif self.drag_mode == "playhead":
                self._set_playhead(event.pos[0], timeline)
            elif self.drag_mode == "keyframe" and self.drag_key_time is not None:
                self._move_keyframe(event.pos[0], timeline)
            elif self.drag_mode == "graph_keyframe":
                self._move_graph_keyframes(event.pos, timeline)
            elif self.drag_mode == "bezier_handle":
                self._move_bezier_handle(event.pos, timeline)
            elif self.drag_mode == "keyframe_marquee":
                self.keyframe_marquee_current = event.pos
            elif self.drag_mode == "duration":
                self._set_duration_from_x(event.pos[0], self._duration_slider_rect(timeline))
            elif self.drag_mode == "led_move":
                self._move_selected_led(event.pos, canvas)
            elif self.drag_mode == "scrub":
                self._scrub_property(event.pos[0])
            elif self.drag_mode == "random_led_scrub":
                self._scrub_random_effect_parameter(event.pos[0])
            elif self.drag_mode == "gradient_stop":
                self._move_gradient_stop(event.pos[0])
            elif self.drag_mode == "gradient_angle":
                self._set_gradient_angle_from_x(event.pos[0])
            elif self.drag_mode in ("move", "resize_nw", "resize_ne", "resize_sw", "resize_se", "rotate"):
                self._transform_selection(event.pos, canvas)
            return

        if event.type == pygame.MOUSEWHEEL:
            mouse = getattr(event, "pos", pygame.mouse.get_pos())
            if timeline.collidepoint(mouse):
                layer_column = pygame.Rect(
                    timeline.x, self._timeline_track(timeline).y,
                    180, self._timeline_track(timeline).height,
                )
                if (
                    self.timeline_mode == "dope" and layer_column.collidepoint(mouse)
                    and not self._active_imported_effect()
                ):
                    self._scroll_timeline_layers(-event.y, timeline)
                elif pygame.key.get_mods() & pygame.KMOD_SHIFT:
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
        if ctrl and event.key == pygame.K_c:
            self._copy_selected_keyframes()
            return
        if ctrl and event.key == pygame.K_v:
            self._paste_keyframes_at_playhead()
            return
        if ctrl and event.key == pygame.K_i:
            self._action("import_effect_data")
            return
        if ctrl and event.key == pygame.K_n:
            self._action("new")
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
            elif self.timeline_selected_layer_id:
                layer_index = next(
                    (
                        index for index, layer in enumerate(self.project.layers)
                        if layer.id == self.timeline_selected_layer_id
                    ),
                    None,
                )
                if layer_index is not None:
                    self.active_layer = layer_index
                    self._action("delete_layer")
                else:
                    self.timeline_selected_layer_id = None
            else:
                self._action("delete")
        elif event.key == pygame.K_k:
            self._action("keyframe")
        elif event.key == pygame.K_v:
            layer = self.project.layers[self.active_layer]
            if layer.is_canvas:
                self._action(f"toggle_layer:{self.active_layer}")
            elif self.selected:
                state = self.selected.state_at(self.current_ms)
                self._begin_change()
                self._set_animated("visible", not state["visible"])
                self._commit_change()
        elif event.key == pygame.K_LEFTBRACKET and self.selected:
            self._begin_change()
            self._set_rotation_total(self.selected.state_at(self.current_ms)["rotation_total"] - 5)
            self._commit_change()
        elif event.key == pygame.K_RIGHTBRACKET and self.selected:
            self._begin_change()
            self._set_rotation_total(self.selected.state_at(self.current_ms)["rotation_total"] + 5)
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
            action.startswith((
                "add:", "color:", "fill_mode:", "gradient_type:",
                "gradient_radial_mode:", "toggle_layer:", "canvas_state:",
            ))
            or action in {
                "layer", "canvas_layer", "duplicate_layer", "delete_layer", "delete", "duplicate", "keyframe",
                "effect_id:-1", "effect_id:1", "cycle_fps", "loops:-1", "loops:1",
                "set_loop_end", "clear_loop_end", "toggle_overlay",
                "gradient_add_stop", "gradient_delete_stop",
                "random_led_toggle", "random_led_keyframe", "random_led_opacity_keyframe",
                "random_led_seed:-1", "random_led_seed:1",
                "random_led_life:-50", "random_led_life:50", "random_led_birth:-1", "random_led_birth:1",
                "random_led_count:-1", "random_led_count:1", "random_led_delete",
            }
        )
        if mutating:
            self._begin_change()
        if action.startswith("add:"):
            if self.project.layers[self.active_layer].is_canvas:
                self.status = "Select a regular layer before adding a shape"
            else:
                self._create_shape(action.split(":", 1)[1], (0.5, 0.5), history=False)
        elif action == "layer":
            self.project.layers.append(Layer(f"Layer {len(self.project.layers) + 1}"))
            self.active_layer = len(self.project.layers) - 1
            self.timeline_selected_layer_id = None
            self.selected = None
            self._ensure_active_layer_visible()
        elif action == "canvas_layer":
            existing = next(
                (index for index, layer in enumerate(self.project.layers) if layer.is_canvas),
                None,
            )
            if existing is not None:
                self.active_layer = existing
                self.timeline_selected_layer_id = None
                self.selected = None
                self.random_led_editor_open = False
                self.timeline_expanded_layers.add(self.project.layers[existing].id)
                self.status = "The project already has a Canvas layer"
            else:
                canvas_layer = Layer("Canvas", is_canvas=True, canvas_enabled=True)
                canvas_layer.add_keyframe("canvas_enabled", 0, True)
                self.project.layers.append(canvas_layer)
                self.project.overlay = True
                self.active_layer = len(self.project.layers) - 1
                self.timeline_selected_layer_id = None
                self.selected = None
                self.random_led_editor_open = False
                self.selected_keyframes = {(canvas_layer.id, "canvas_enabled", 0)}
                self.timeline_expanded_layers.add(canvas_layer.id)
                self._ensure_active_layer_visible()
                self.status = "Canvas layer added — ON means empty LEDs stay transparent"
        elif action == "duplicate_layer":
            source = self.project.layers[self.active_layer]
            if source.is_canvas:
                self.status = "A project can only have one Canvas layer"
            else:
                selected_index = source.shapes.index(self.selected) if self.selected in source.shapes else None
                clone = deepcopy(source)
                clone.id = uuid4().hex[:10]
                clone.name = f"{source.name} copy"
                for shape in clone.shapes:
                    shape.id = uuid4().hex[:10]
                for effect in clone.effects:
                    effect.id = uuid4().hex[:10]
                self.project.layers.insert(self.active_layer + 1, clone)
                self.active_layer += 1
                self.selected = clone.shapes[selected_index] if selected_index is not None else None
                self.selected_keyframes.clear()
                self.status = f"Duplicated layer: {source.name}"
                self._ensure_active_layer_visible()
        elif action == "delete_layer":
            if len(self.project.layers) == 1:
                self.status = "A project must keep at least one layer"
            else:
                removed = self.project.layers.pop(self.active_layer)
                self.timeline_selected_layer_id = None
                self.timeline_expanded_layers.discard(removed.id)
                if self.selected in removed.shapes:
                    self.selected = None
                    self.selected_keyframes.clear()
                self.active_layer = min(self.active_layer, len(self.project.layers) - 1)
                self.status = f"Deleted layer: {removed.name}"
                self._ensure_active_layer_visible()
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
        elif action == "keyframe":
            layer = self.project.layers[self.active_layer]
            if layer.is_canvas:
                value = bool(layer.value_at("canvas_enabled", self.current_ms))
                layer.add_keyframe("canvas_enabled", self.current_ms, value)
                self.selected_keyframes = {(layer.id, "canvas_enabled", self.current_ms)}
                self.timeline_expanded_layers.add(layer.id)
                self.status = f"Canvas keyframe at {self.current_ms} ms"
            elif self.selected:
                state = self.selected.state_at(self.current_ms)
                for prop in (
                    "x", "y", "width", "height", "rotation", "rotation_turns", "color", "opacity",
                    "feather", "mask_expansion",
                    "fill_mode", "stroke_width", "gradient_type", "gradient_radial_mode",
                    "gradient_angle", "visible",
                ):
                    self.selected.add_keyframe(prop, self.current_ms, state[prop])
                self.selected_keyframes = {
                    (self.selected.id, prop, self.current_ms) for prop in self.selected.keyframes
                    if any(frame.time_ms == self.current_ms for frame in self.selected.keyframes[prop])
                }
                self.status = f"Keyframe at {self.current_ms} ms"
        elif action.startswith("canvas_state:"):
            layer = self.project.layers[self.active_layer]
            if layer.is_canvas:
                self._set_canvas_enabled_key(layer, bool(int(action.split(":", 1)[1])))
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
        elif action == "toggle_timeline_mode":
            if self._active_imported_effect():
                self.status = "Graph Editor is available for project keyframes"
            elif self.timeline_mode == "graph":
                self.timeline_mode = "dope"
                self.status = "Dope Sheet"
            elif self._choose_graph_channel():
                self.timeline_mode = "graph"
                self.status = f"Graph Editor: {TIMELINE_PROPERTY_LABELS[self.graph_prop]}"
            else:
                self.status = "Select an animated numeric property for Graph Editor"
        elif action == "gradient_editor" and self.selected:
            if len(self.selected.gradient_stops) < 2:
                self._begin_change()
                base = tuple(self.selected.state_at(self.current_ms)["color"])
                self.selected.gradient_stops = [
                    GradientStop(0.0, base),
                    GradientStop(1.0, tuple(min(255, channel + 100) for channel in base)),
                ]
                self._commit_change()
            self.gradient_editor_open = True
            self.random_led_editor_open = False
            self.selected_gradient_stop_id = self.selected.gradient_stops[0].id
            self.status = "Gradient editor — click the bar to add a color stop"
        elif action == "gradient_back":
            self.gradient_editor_open = False
            self.drag_mode = None
            self.status = "Gradient changes applied"
        elif action == "random_led_editor":
            if self.project.layers[self.active_layer].is_canvas:
                self.status = "Select a regular layer before adding a Random LED effect"
                if mutating:
                    self._commit_change()
                return
            effect = self._active_random_led_effect()
            if effect is None:
                self._begin_change()
                effect = RandomLedEffect()
                self.project.layers[self.active_layer].effects.append(effect)
                self._commit_change()
            self.random_led_editor_open = True
            self.random_effect_editing = None
            self.random_effect_input_select_all = False
            self.gradient_editor_open = False
            self.selected_keyframes.clear()
            self.status = "Random LED — deterministic flashes independent from layer shapes"
        elif action == "random_led_back":
            self.random_led_editor_open = False
            self.random_effect_editing = None
            self.random_effect_input_select_all = False
            self.status = "Random LED changes applied"
        elif action == "random_led_delete":
            effect = self._active_random_led_effect()
            if effect:
                self.project.layers[self.active_layer].effects.remove(effect)
                self.selected_keyframes = {
                    key for key in self.selected_keyframes if key[0] != effect.id
                }
            self.random_led_editor_open = False
            self.random_effect_editing = None
            self.random_effect_input_select_all = False
            self.status = "Random LED effect removed from layer"
        elif action == "random_led_toggle":
            effect = self._active_random_led_effect()
            if effect:
                value = not bool(effect.value_at("enabled", self.current_ms))
                if self.current_ms == 0 and not effect.keyframes.get("enabled"):
                    effect.enabled = value
                else:
                    effect.add_keyframe("enabled", self.current_ms, value)
                    self.selected_keyframes = {(effect.id, "enabled", self.current_ms)}
                self.status = f"Random LED {'enabled' if value else 'disabled'} at {self.current_ms} ms"
        elif action == "random_led_keyframe":
            effect = self._active_random_led_effect()
            if effect:
                value = bool(effect.value_at("enabled", self.current_ms))
                effect.add_keyframe("enabled", self.current_ms, value)
                self.selected_keyframes = {(effect.id, "enabled", self.current_ms)}
                self.status = f"Random LED enabled keyframe at {self.current_ms} ms"
        elif action == "random_led_opacity_keyframe":
            effect = self._active_random_led_effect()
            if effect:
                value = float(effect.value_at("opacity", self.current_ms))
                effect.add_keyframe("opacity", self.current_ms, value)
                self.selected_keyframes = {(effect.id, "opacity", self.current_ms)}
                self.timeline_expanded_layers.add(self.project.layers[self.active_layer].id)
                self.status = f"Random LED opacity keyframe at {self.current_ms} ms"
        elif action.startswith("random_led_seed:"):
            effect = self._active_random_led_effect()
            if effect:
                effect.seed = max(0, min(2147483647, effect.seed + int(action.split(":", 1)[1])))
        elif action.startswith("random_led_life:"):
            effect = self._active_random_led_effect()
            if effect:
                effect.life_ms = max(50, min(10000, effect.life_ms + int(action.split(":", 1)[1])))
        elif action.startswith("random_led_birth:"):
            effect = self._active_random_led_effect()
            if effect:
                effect.born_speed = round(max(0.5, min(100.0, effect.born_speed + float(action.split(":", 1)[1]))), 1)
        elif action.startswith("random_led_count:"):
            effect = self._active_random_led_effect()
            if effect:
                effect.particle_count = max(1, min(68, effect.particle_count + int(action.split(":", 1)[1])))
        elif action.startswith("gradient_type:") and self.selected:
            gradient_type = action.split(":", 1)[1]
            self._set_animated("gradient_type", gradient_type)
            self.status = f"Gradient fill: {gradient_type}"
        elif action.startswith("gradient_radial_mode:") and self.selected:
            radial_mode = action.split(":", 1)[1]
            self._set_animated("gradient_radial_mode", radial_mode)
            self.status = f"Radial gradient: {radial_mode}"
        elif action == "gradient_add_stop" and self.selected:
            self._add_gradient_stop()
        elif action == "gradient_delete_stop" and self.selected:
            if len(self.selected.gradient_stops) <= 2:
                self.status = "A gradient needs at least two color stops"
            else:
                self.selected.gradient_stops = [
                    stop for stop in self.selected.gradient_stops
                    if stop.id != self.selected_gradient_stop_id
                ]
                self.selected_gradient_stop_id = self.selected.gradient_stops[0].id
                self.status = "Gradient stop deleted"
        elif action.startswith("effect_id:"):
            delta = int(action.split(":", 1)[1])
            self.project.effect_id = max(1, min(255, self.project.effect_id + delta))
            self.status = f"Firmware effect ID: {self.project.effect_id}"
        elif action == "cycle_fps":
            presets = (50, 40, 33)
            current = int(self.project.frame_ms or 50)
            self.project.frame_ms = presets[(presets.index(current) + 1) % len(presets)] if current in presets else 50
            self.project.fps = round(self.project.actual_fps)
            self.current_ms = self._snap_time_to_frame(self.current_ms)
            self.status = f"frameMs {self.project.frame_ms} — actual {self.project.actual_fps:.2f} FPS"
        elif action == "toggle_overlay":
            self.project.overlay = not self.project.overlay
            self.status = (
                "Export mode: CANVAS/OVERLAY — empty cells use the transparent FF00FF sentinel"
                if self.project.overlay else
                "Export mode: FULL — black cells overwrite the playfield"
            )
        elif action.startswith("loops:"):
            delta = int(action.split(":", 1)[1])
            self.project.loops = max(1, min(255, self.project.loops + delta))
            self.status = f"Loop count: {self.project.loops}"
        elif action == "set_loop_end":
            boundary = min(
                self.project.stored_frame_count,
                max(1, math.floor(self.current_ms / max(1, self.project.frame_ms or 1)) + 1),
            )
            self.project.loop_frames = 0 if boundary >= self.project.stored_frame_count else boundary
            self.status = (
                "Loop uses the full effect"
                if self.project.loop_frames == 0 else f"Loop ends after frame {self.project.loop_frames - 1}"
            )
        elif action == "clear_loop_end":
            self.project.loop_frames = 0
            self.status = "Loop uses the full effect — no separate outro"
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
        elif action == "new":
            self._new_project()
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
            self._open_export_bank()
        elif action == "help":
            self.help_open = True
            self.export_bank_open = False
            self.playing = False
            self.status = "Hotkey reference opened"
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
        elif action.startswith("color:") and (self.selected or self.random_led_editor_open):
            color = PALETTE[int(action.split(":")[1])]
            if self.random_led_editor_open:
                effect = self._active_random_led_effect()
                if effect:
                    effect.color = color
                    self.status = f"Random LED color: RGB {color}"
                if mutating:
                    self._commit_change()
                return
            assert self.selected is not None
            if self.gradient_editor_open and self.selected_gradient_stop_id:
                stop = self._selected_gradient_stop()
                if stop:
                    stop.color = color
                    self.status = f"Gradient stop color: RGB {color}"
                if mutating:
                    self._commit_change()
                return
            selected_shape_times: dict[str, set[int]] = {}
            for target_id, _prop, time_ms in self.selected_keyframes:
                target = self._find_keyframe_target(target_id)
                if isinstance(target, Shape):
                    selected_shape_times.setdefault(target_id, set()).add(time_ms)
            if selected_shape_times:
                changed = 0
                for target_id, times in selected_shape_times.items():
                    target = self._find_keyframe_target(target_id)
                    if not isinstance(target, Shape):
                        continue
                    for time_ms in sorted(times):
                        target.add_keyframe("color", time_ms, color)
                        self.selected_keyframes.add((target.id, "color", time_ms))
                        changed += 1
                self.status = f"Color applied to {changed} selected keyframes"
            else:
                self._set_animated("color", color)
        elif action.startswith("fill_mode:") and self.selected:
            self._set_animated("fill_mode", action.split(":", 1)[1])
        elif action.startswith("toggle_timeline_layer:"):
            index = int(action.split(":")[1])
            layer = self.project.layers[index]
            if layer.id in self.timeline_expanded_layers:
                self.timeline_expanded_layers.remove(layer.id)
                state = "collapsed"
            else:
                self.timeline_expanded_layers.add(layer.id)
                state = "expanded"
            self.active_layer = index
            self.timeline_selected_layer_id = None
            self._ensure_active_layer_visible()
            self.status = f"Timeline layer {layer.name}: {state}"
        elif action.startswith("select_timeline_channel:"):
            _prefix, target_id, prop = action.split(":", 2)
            target = self._find_keyframe_target(target_id)
            layer_index = next(
                (
                    index for index, layer in enumerate(self.project.layers)
                    if target is layer or target in [*layer.shapes, *layer.effects]
                ),
                None,
            )
            if target and layer_index is not None:
                self.active_layer = layer_index
                self.timeline_selected_layer_id = None
                self.gradient_editor_open = False
                if isinstance(target, Shape):
                    self.selected = target
                    self.random_led_editor_open = False
                elif isinstance(target, Layer):
                    self.selected = None
                    self.random_led_editor_open = False
                else:
                    self.selected = None
                    self.random_led_editor_open = True
                self.selected_keyframes.clear()
                if isinstance(target, Shape) and prop in GRAPH_NUMERIC_PROPERTIES:
                    self.graph_target_id = target.id
                    self.graph_prop = prop
                self.status = f"Timeline channel: {target.name} · {TIMELINE_PROPERTY_LABELS.get(prop, prop)}"
        elif action.startswith("select_timeline_target:"):
            target_id = action.split(":", 1)[1]
            target = self._find_keyframe_target(target_id)
            if target:
                self.status = f"Timeline target: {target.name}"
        elif action.startswith("select_layer:"):
            self.active_layer = int(action.split(":")[1])
            layer = self.project.layers[self.active_layer]
            self.timeline_selected_layer_id = layer.id
            self.selected = None
            self.random_led_editor_open = False
            self.selected_keyframes.clear()
            self.status = f"Selected layer: {layer.name} — Delete removes the layer"
            self._ensure_active_layer_visible()
        elif action.startswith("toggle_layer:"):
            index = int(action.split(":")[1])
            layer = self.project.layers[index]
            self.timeline_selected_layer_id = None
            if layer.is_canvas:
                self.active_layer = index
                enabled = not bool(layer.value_at("canvas_enabled", self.current_ms))
                self._set_canvas_enabled_key(layer, enabled)
            else:
                layer.visible = not layer.visible
                if not layer.visible and self.selected in layer.shapes:
                    self.selected = None
                    self.selected_keyframes.clear()
                self.status = f"Layer {layer.name}: {'ON' if layer.visible else 'OFF'}"
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
        valid_layer_ids = {layer.id for layer in self.project.layers}
        self.timeline_expanded_layers.intersection_update(valid_layer_ids)
        self.active_layer = min(self.active_layer, len(self.project.layers) - 1)
        self._ensure_active_layer_visible()
        self.selected = next(
            (shape for layer in self.project.layers for shape in layer.shapes if shape.id == selected_id),
            None,
        )
        if self.selected is None and self.project.layers[self.active_layer].shapes:
            self.selected = self.project.layers[self.active_layer].shapes[-1]
        self.selected_keyframes.clear()
        self.timeline_selected_layer_id = None
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
        if self.project.layers[self.active_layer].is_canvas:
            self.status = "Select a regular layer before adding a shape"
            return
        if history:
            self._begin_change()
        self.timeline_selected_layer_id = None
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
        angle = math.radians(state["rotation_total"])
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

        angle = math.radians(-state["rotation_total"])
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

    def _timeline_layer_capacity(self, timeline: pygame.Rect) -> int:
        # Keep one text line free below the rows for timeline help and the
        # scroll-range indicator.
        return max(1, (self._timeline_track(timeline).height - 24) // 32)

    def _timeline_rows(self) -> list[TimelineRow]:
        rows: list[TimelineRow] = []
        property_order = {
            prop: index for index, prop in enumerate(TIMELINE_PROPERTY_LABELS)
        }
        for layer_index, layer in enumerate(self.project.layers):
            rows.append(TimelineRow("layer", layer_index))
            if layer.id not in self.timeline_expanded_layers:
                continue
            for target in self._layer_keyframe_targets(layer):
                props = sorted(
                    (prop for prop, frames in target.keyframes.items() if frames),
                    key=lambda prop: (property_order.get(prop, len(property_order)), prop),
                )
                rows.extend(
                    TimelineRow("property", layer_index, target.id, prop)
                    for prop in props
                )
        return rows

    @staticmethod
    def _layer_keyframe_targets(layer: Layer) -> list[Layer | Shape | RandomLedEffect]:
        return [layer] if layer.is_canvas else [*layer.shapes, *layer.effects]

    def _timeline_row_target(self, row: TimelineRow) -> Layer | Shape | RandomLedEffect | None:
        return self._find_keyframe_target(row.target_id) if row.target_id else None

    def _choose_graph_channel(self) -> bool:
        candidates: list[tuple[str, str]] = []
        if self.graph_target_id and self.graph_prop:
            candidates.append((self.graph_target_id, self.graph_prop))
        candidates.extend(
            (target_id, prop)
            for target_id, prop, _time_ms in sorted(self.selected_keyframes)
        )
        if self.selected:
            candidates.extend((self.selected.id, prop) for prop in self.selected.keyframes)
        candidates.extend(
            (shape.id, prop)
            for layer in self.project.layers
            for shape in layer.shapes
            for prop in shape.keyframes
        )
        for target_id, prop in candidates:
            target = self._find_keyframe_target(target_id)
            frames = target.keyframes.get(prop, []) if isinstance(target, Shape) else []
            if (
                prop in GRAPH_NUMERIC_PROPERTIES and frames
                and all(isinstance(frame.value, (int, float)) and not isinstance(frame.value, bool) for frame in frames)
            ):
                self.graph_target_id = target_id
                self.graph_prop = prop
                return True
        self.graph_target_id = None
        self.graph_prop = None
        return False

    def _remember_graph_channel(self, keys: set[tuple[str, str, int]]) -> None:
        channels = {(target_id, prop) for target_id, prop, _time_ms in keys}
        if len(channels) != 1:
            return
        target_id, prop = next(iter(channels))
        target = self._find_keyframe_target(target_id)
        if isinstance(target, Shape) and prop in GRAPH_NUMERIC_PROPERTIES:
            self.graph_target_id = target_id
            self.graph_prop = prop

    def _graph_channel(self) -> tuple[Shape, str] | None:
        target = self._find_keyframe_target(self.graph_target_id) if self.graph_target_id else None
        if (
            isinstance(target, Shape) and self.graph_prop in GRAPH_NUMERIC_PROPERTIES
            and target.keyframes.get(self.graph_prop)
        ):
            return target, self.graph_prop
        return None

    def _graph_area(self, timeline: pygame.Rect) -> pygame.Rect:
        track = self._timeline_track(timeline)
        return pygame.Rect(track.x + 1, track.y + 8, track.width - 2, max(40, track.height - 38))

    @staticmethod
    def _graph_value_range(target: Shape, prop: str) -> tuple[float, float]:
        fixed = {
            "x": (0.0, 1.0), "y": (0.0, 1.0), "opacity": (0.0, 1.0),
            "rotation": (0.0, 360.0), "gradient_angle": (0.0, 360.0),
            "stroke_width": (0.0, 0.05),
        }
        frames = target.keyframes.get(prop, [])
        values = (
            [float(frame.value) for frame in frames]
            if frames else [float(getattr(target, prop))]
        )
        for before, after in zip(frames, frames[1:]):
            if after.easing != "bezier" or after.bezier is None:
                continue
            delta = float(after.value) - float(before.value)
            values.extend([
                float(before.value) + delta * float(after.bezier[1]),
                float(before.value) + delta * float(after.bezier[3]),
            ])
        if prop in fixed:
            base_low, base_high = fixed[prop]
            if min(values) >= base_low and max(values) <= base_high:
                return base_low, base_high
            values.extend((base_low, base_high))
        low, high = min(values), max(values)
        if math.isclose(low, high):
            padding = max(0.05, abs(low) * 0.35)
        else:
            padding = (high - low) * 0.16
        minimum = low - padding
        if prop not in {"rotation_turns", "mask_expansion"}:
            minimum = max(0.0, minimum)
        return minimum, high + padding

    def _graph_point(
        self, time_ms: int, value: float, timeline: pygame.Rect,
        value_range: tuple[float, float] | None = None,
    ) -> tuple[int, int]:
        channel = self._graph_channel()
        if not channel:
            return self._timeline_track(timeline).topleft
        low, high = value_range or self._graph_value_range(*channel)
        area = self._graph_area(timeline)
        ratio = (float(value) - low) / max(0.000001, high - low)
        return (
            round(self._time_to_timeline_x(time_ms, timeline)),
            round(area.bottom - max(0.0, min(1.0, ratio)) * area.height),
        )

    def _pick_graph_key(
        self, pos: tuple[int, int], timeline: pygame.Rect
    ) -> set[tuple[str, str, int]]:
        channel = self._graph_channel()
        if not channel:
            return set()
        target, prop = channel
        value_range = self._graph_value_range(target, prop)
        candidates = []
        start, end = self._timeline_window()
        for frame in target.keyframes.get(prop, []):
            if start <= frame.time_ms <= end:
                point = self._graph_point(frame.time_ms, float(frame.value), timeline, value_range)
                candidates.append((pygame.Vector2(pos).distance_to(point), frame.time_ms))
        if not candidates:
            return set()
        distance, time_ms = min(candidates)
        return {(target.id, prop, time_ms)} if distance <= 11 else set()

    def _selected_bezier_segment(
        self,
    ) -> tuple[Shape, str, Keyframe, Keyframe] | None:
        channel = self._graph_channel()
        if not channel:
            return None
        target, prop = channel
        selected_times = {
            time_ms for target_id, selected_prop, time_ms in self.selected_keyframes
            if target_id == target.id and selected_prop == prop
        }
        frames = target.keyframes[prop]
        for index, after in enumerate(frames[1:], start=1):
            if (
                after.time_ms in selected_times and after.easing == "bezier"
                and after.bezier is not None
            ):
                return target, prop, frames[index - 1], after
        return None

    def _bezier_handle_points(
        self, timeline: pygame.Rect,
        segment: tuple[Shape, str, Keyframe, Keyframe] | None = None,
        value_range: tuple[float, float] | None = None,
    ) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]] | None:
        segment = segment or self._selected_bezier_segment()
        if not segment:
            return None
        target, prop, before, after = segment
        x1, y1, x2, y2 = after.bezier or DEFAULT_BEZIER
        span = after.time_ms - before.time_ms
        delta = float(after.value) - float(before.value)
        value_range = value_range or self._graph_value_range(target, prop)
        return (
            self._graph_point(before.time_ms, float(before.value), timeline, value_range),
            self._graph_point(
                round(before.time_ms + span * x1),
                float(before.value) + delta * y1,
                timeline, value_range,
            ),
            self._graph_point(
                round(before.time_ms + span * x2),
                float(before.value) + delta * y2,
                timeline, value_range,
            ),
            self._graph_point(after.time_ms, float(after.value), timeline, value_range),
        )

    def _pick_bezier_handle(
        self, pos: tuple[int, int], timeline: pygame.Rect
    ) -> tuple[str, str, int, int] | None:
        segment = self._selected_bezier_segment()
        points = self._bezier_handle_points(timeline, segment)
        if not segment or not points:
            return None
        target, prop, _before, after = segment
        for handle_index, point in ((1, points[1]), (2, points[2])):
            if pygame.Vector2(pos).distance_to(point) <= 10:
                return target.id, prop, after.time_ms, handle_index
        return None

    def _start_bezier_handle_drag(
        self, handle: tuple[str, str, int, int]
    ) -> None:
        channel = self._graph_channel()
        if not channel:
            return
        self.graph_drag_handle = handle
        self.graph_drag_value_range = self._graph_value_range(*channel)
        self.drag_mode = "bezier_handle"
        self._begin_change()

    def _move_bezier_handle(self, pos: tuple[int, int], timeline: pygame.Rect) -> None:
        if not self.graph_drag_handle:
            return
        target_id, prop, after_time, handle_index = self.graph_drag_handle
        target = self._find_keyframe_target(target_id)
        if not isinstance(target, Shape):
            return
        frames = target.keyframes.get(prop, [])
        after_index = next(
            (index for index, frame in enumerate(frames) if frame.time_ms == after_time),
            None,
        )
        if after_index is None or after_index == 0:
            return
        before, after = frames[after_index - 1], frames[after_index]
        controls = list(after.bezier or DEFAULT_BEZIER)
        span = max(1, after.time_ms - before.time_ms)
        handle_time = self._timeline_x_to_time(pos[0], timeline)
        normalized_x = max(0.0, min(1.0, (handle_time - before.time_ms) / span))

        low, high = self.graph_drag_value_range or self._graph_value_range(target, prop)
        area = self._graph_area(timeline)
        screen_ratio = (area.bottom - pos[1]) / max(1, area.height)
        handle_value = low + screen_ratio * (high - low)
        delta = float(after.value) - float(before.value)
        normalized_y = controls[1 if handle_index == 1 else 3]
        if not math.isclose(delta, 0.0):
            normalized_y = max(-2.0, min(3.0, (handle_value - float(before.value)) / delta))
        offset = 0 if handle_index == 1 else 2
        controls[offset] = round(normalized_x, 4)
        controls[offset + 1] = round(normalized_y, 4)
        after.easing = "bezier"
        after.bezier = tuple(controls)
        self.selected_keyframes = {(target.id, prop, after.time_ms)}
        self.status = (
            f"Bezier H{handle_index}: {controls[offset]:.3f}, {controls[offset + 1]:.3f}"
        )

    def _finish_bezier_handle_drag(self) -> None:
        self.graph_drag_handle = None
        self.graph_drag_value_range = None

    def _start_graph_drag(
        self, keys: set[tuple[str, str, int]], pos: tuple[int, int]
    ) -> None:
        channel = self._graph_channel()
        if not channel or not keys:
            return
        target, prop = channel
        clicked_time = next(iter(keys))[2]
        selected_channel = {
            key for key in self.selected_keyframes
            if key[0] == target.id and key[1] == prop
        }
        self.selected_keyframes = selected_channel if selected_channel.intersection(keys) else keys
        self.graph_drag_records = []
        for _target_id, _prop, time_ms in sorted(self.selected_keyframes, key=lambda item: item[2]):
            frame = next(
                (item for item in target.keyframes[prop] if item.time_ms == time_ms),
                None,
            )
            if frame and isinstance(frame.value, (int, float)) and not isinstance(frame.value, bool):
                self.graph_drag_records.append((target, prop, frame, time_ms, float(frame.value)))
        self.drag_mode = "graph_keyframe"
        self.drag_origin = pos
        self.drag_key_time = clicked_time
        self.graph_drag_value_range = self._graph_value_range(target, prop)
        self._begin_change()

    def _move_graph_keyframes(self, pos: tuple[int, int], timeline: pygame.Rect) -> None:
        channel = self._graph_channel()
        if not channel or not self.graph_drag_records or self.drag_key_time is None:
            return
        target, prop = channel
        new_anchor = self._timeline_x_to_time(pos[0], timeline)
        start, end = self._timeline_window()
        if self.snap or self._timeline_uses_frame_ruler(timeline, end - start):
            new_anchor = self._snap_time_to_frame(new_anchor)
        delta_time = new_anchor - self.drag_key_time
        original_times = [item[3] for item in self.graph_drag_records]
        delta_time = max(
            -min(original_times),
            min(self.project.duration_ms - max(original_times), delta_time),
        )
        low, high = self.graph_drag_value_range or self._graph_value_range(target, prop)
        area = self._graph_area(timeline)
        delta_value = -(pos[1] - self.drag_origin[1]) / max(1, area.height) * (high - low)
        limits = {
            "x": (0.0, 1.0), "y": (0.0, 1.0), "opacity": (0.0, 1.0),
            "width": (0.01, 5.0), "height": (0.01, 5.0),
            "rotation": (0.0, 360.0), "gradient_angle": (0.0, 360.0),
            "rotation_turns": (-100.0, 100.0),
            "stroke_width": (0.001, 0.05),
            "feather": (0.0, 0.1), "mask_expansion": (-0.1, 0.1),
        }
        minimum, maximum = limits[prop]
        updated: set[tuple[str, str, int]] = set()
        for _target, _prop, frame, original_time, original_value in self.graph_drag_records:
            frame.time_ms = original_time + delta_time
            frame.value = round(max(minimum, min(maximum, original_value + delta_value)), 6)
            updated.add((target.id, prop, frame.time_ms))
        target.keyframes[prop].sort(key=lambda frame: frame.time_ms)
        self.selected_keyframes = updated
        self.current_ms = self.drag_key_time + delta_time

    def _finish_graph_drag(self) -> None:
        channel = self._graph_channel()
        if channel and self.graph_drag_records:
            target, prop = channel
            selected_frames = {id(item[2]) for item in self.graph_drag_records}
            selected_by_time = {
                frame.time_ms: frame for frame in target.keyframes[prop]
                if id(frame) in selected_frames
            }
            unselected_by_time = {
                frame.time_ms: frame for frame in target.keyframes[prop]
                if id(frame) not in selected_frames and frame.time_ms not in selected_by_time
            }
            target.keyframes[prop] = sorted(
                [*unselected_by_time.values(), *selected_by_time.values()],
                key=lambda frame: frame.time_ms,
            )
        self.graph_drag_records.clear()
        self.graph_drag_value_range = None

    def _timeline_visible_rows(
        self, timeline: pygame.Rect
    ) -> list[tuple[TimelineRow, int]]:
        self._clamp_timeline_layer_scroll(timeline)
        rows = self._timeline_rows()
        capacity = self._timeline_layer_capacity(timeline)
        visible = rows[self.timeline_layer_scroll:self.timeline_layer_scroll + capacity]
        first_y = self._timeline_track(timeline).y + 15
        return [(row, first_y + index * 32) for index, row in enumerate(visible)]

    def _clamp_timeline_layer_scroll(self, timeline: pygame.Rect) -> None:
        maximum = max(0, len(self._timeline_rows()) - self._timeline_layer_capacity(timeline))
        self.timeline_layer_scroll = max(0, min(maximum, self.timeline_layer_scroll))

    def _scroll_timeline_layers(self, delta: int, timeline: pygame.Rect) -> None:
        self.timeline_layer_scroll += delta
        self._clamp_timeline_layer_scroll(timeline)
        row_count = len(self._timeline_rows())
        start = self.timeline_layer_scroll + 1
        end = min(row_count, start + self._timeline_layer_capacity(timeline) - 1)
        self.status = f"Timeline rows {start}–{end} / {row_count}"

    def _ensure_active_layer_visible(self, timeline: pygame.Rect | None = None) -> None:
        timeline = timeline or self.layout()[2]
        rows = self._timeline_rows()
        active_row = next(
            (
                index for index, row in enumerate(rows)
                if row.kind == "layer" and row.layer_index == self.active_layer
            ),
            0,
        )
        capacity = self._timeline_layer_capacity(timeline)
        if active_row < self.timeline_layer_scroll:
            self.timeline_layer_scroll = active_row
        elif active_row >= self.timeline_layer_scroll + capacity:
            self.timeline_layer_scroll = active_row - capacity + 1
        self._clamp_timeline_layer_scroll(timeline)

    def _duration_slider_rect(self, _timeline: pygame.Rect | None = None) -> pygame.Rect:
        right_edge = self.screen.get_width() - INSPECTOR_WIDTH
        return pygame.Rect(right_edge - 94, 19, 78, 16)

    def _duration_input_rect(self) -> pygame.Rect:
        right_edge = self.screen.get_width() - INSPECTOR_WIDTH
        return pygame.Rect(right_edge - 174, 11, 68, 32)

    def _gradient_bar_rect(self) -> pygame.Rect:
        panel_x = self.screen.get_width() - INSPECTOR_WIDTH
        return pygame.Rect(panel_x + 22, TOP_BAR + 290, INSPECTOR_WIDTH - 44, 28)

    def _gradient_angle_rect(self) -> pygame.Rect:
        panel_x = self.screen.get_width() - INSPECTOR_WIDTH
        return pygame.Rect(panel_x + 22, TOP_BAR + 447, INSPECTOR_WIDTH - 44, 18)

    def _selected_gradient_stop(self) -> GradientStop | None:
        if not self.selected:
            return None
        return next(
            (stop for stop in self.selected.gradient_stops if stop.id == self.selected_gradient_stop_id),
            None,
        )

    def _add_gradient_stop(self) -> None:
        if not self.selected:
            return
        ordered = sorted(self.selected.gradient_stops, key=lambda stop: stop.position)
        if len(ordered) < 2:
            position = 0.5
        else:
            before, after = max(zip(ordered, ordered[1:]), key=lambda pair: pair[1].position - pair[0].position)
            position = (before.position + after.position) / 2
        stop = GradientStop(position, sample_gradient(ordered, position))
        self.selected.gradient_stops.append(stop)
        self.selected_gradient_stop_id = stop.id
        self.status = f"Added gradient stop at {position * 100:.1f}%"

    def _add_gradient_stop_at(self, screen_x: int) -> None:
        if not self.selected:
            return
        bar = self._gradient_bar_rect()
        position = max(0.0, min(1.0, (screen_x - bar.x) / max(1, bar.width)))
        stop = GradientStop(position, sample_gradient(self.selected.gradient_stops, position))
        self.selected.gradient_stops.append(stop)
        self.selected_gradient_stop_id = stop.id
        self.status = f"Added gradient stop at {position * 100:.1f}%"

    def _move_gradient_stop(self, screen_x: int) -> None:
        stop = self._selected_gradient_stop()
        if not stop:
            return
        bar = self._gradient_bar_rect()
        stop.position = round(max(0.0, min(1.0, (screen_x - bar.x) / max(1, bar.width))), 4)
        self.status = f"Gradient stop: {stop.position * 100:.1f}%"

    def _set_gradient_angle_from_x(self, screen_x: int) -> None:
        if not self.selected:
            return
        slider = self._gradient_angle_rect()
        ratio = max(0.0, min(1.0, (screen_x - slider.x) / max(1, slider.width)))
        self._set_animated("gradient_angle", round(ratio * 360.0, 1))
        state = self.selected.state_at(self.current_ms)
        label = (
            "Angular gradient phase"
            if state.get("gradient_type") == "radial"
            and state.get("gradient_radial_mode", "radius") == "angular"
            else "Gradient angle"
        )
        self.status = f"{label}: {round(ratio * 360.0, 1):.1f}°"

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
        target = self._active_keyframe_target()
        if not target:
            return set()
        return {
            (target.id, prop, time_ms)
            for prop, frames in target.keyframes.items()
            if any(frame.time_ms == time_ms for frame in frames)
        }

    def _keys_for_timeline_row(
        self, row: TimelineRow, time_ms: int
    ) -> set[tuple[str, str, int]]:
        if row.kind == "property" and row.target_id and row.prop:
            target = self._find_keyframe_target(row.target_id)
            if target and any(
                frame.time_ms == time_ms for frame in target.keyframes.get(row.prop, [])
            ):
                return {(row.target_id, row.prop, time_ms)}
            return set()
        layer = self.project.layers[row.layer_index]
        return {
            (target.id, prop, time_ms)
            for target in self._layer_keyframe_targets(layer)
            for prop, frames in target.keyframes.items()
            if any(frame.time_ms == time_ms for frame in frames)
        }

    def _times_for_timeline_row(self, row: TimelineRow) -> set[int]:
        if row.kind == "property" and row.target_id and row.prop:
            target = self._find_keyframe_target(row.target_id)
            return {
                frame.time_ms for frame in target.keyframes.get(row.prop, [])
            } if target else set()
        layer = self.project.layers[row.layer_index]
        return {
            frame.time_ms
            for target in self._layer_keyframe_targets(layer)
            for frames in target.keyframes.values()
            for frame in frames
        }

    def _pick_keyframe_keys(
        self, pos: tuple[int, int], timeline: pygame.Rect
    ) -> set[tuple[str, str, int]]:
        if self._active_imported_effect():
            return set()
        row = next(
            (row for row, row_y in self._timeline_visible_rows(timeline) if abs(pos[1] - row_y) <= 12),
            None,
        )
        if row is None:
            return set()
        start, end = self._timeline_window()
        candidates = [
            (abs(pos[0] - self._time_to_timeline_x(time_ms, timeline)), time_ms)
            for time_ms in self._times_for_timeline_row(row)
            if start <= time_ms <= end
        ]
        if not candidates:
            return set()
        distance, time_ms = min(candidates)
        return self._keys_for_timeline_row(row, time_ms) if distance <= 9 else set()

    def _pick_keyframe(
        self, pos: tuple[int, int], timeline: pygame.Rect
    ) -> int | None:
        keys = self._pick_keyframe_keys(pos, timeline)
        return next(iter(keys))[2] if keys else None

    def _selected_timeline_row_y(self, timeline: pygame.Rect) -> int | None:
        if self.timeline_selected_layer_id:
            layer_index = next(
                (
                    index for index, layer in enumerate(self.project.layers)
                    if layer.id == self.timeline_selected_layer_id
                ),
                None,
            )
        elif self.project.layers[self.active_layer].is_canvas:
            layer_index = self.active_layer
        elif self.random_led_editor_open and self._active_random_led_effect():
            layer_index = self.active_layer
        elif self.selected:
            layer_index = next(
                (index for index, layer in enumerate(self.project.layers) if self.selected in layer.shapes),
                None,
            )
        else:
            layer_index = None
        if layer_index is None:
            return None
        return next(
            (
                row_y for row, row_y in self._timeline_visible_rows(timeline)
                if row.kind == "layer" and row.layer_index == layer_index
            ),
            None,
        )

    @staticmethod
    def _inactive_layer_keyframe_times(layer: Layer, active_target) -> set[int]:
        return {
            frame.time_ms
            for item in Editor._layer_keyframe_targets(layer)
            if item is not active_target
            for frames in item.keyframes.values()
            for frame in frames
        }

    def _keyframes_in_marquee(
        self, start_pos: tuple[int, int], end_pos: tuple[int, int], timeline: pygame.Rect
    ) -> set[tuple[str, str, int]]:
        left, right = sorted((start_pos[0], end_pos[0]))
        top, bottom = sorted((start_pos[1], end_pos[1]))
        marquee = pygame.Rect(left, top, max(1, right - left), max(1, bottom - top))
        start, end = self._timeline_window()
        selected: set[tuple[str, str, int]] = set()
        for row, row_y in self._timeline_visible_rows(timeline):
            for time_ms in self._times_for_timeline_row(row):
                if not start <= time_ms <= end:
                    continue
                x = round(self._time_to_timeline_x(time_ms, timeline))
                if marquee.colliderect(pygame.Rect(x - 8, row_y - 8, 16, 16)):
                    selected.update(self._keys_for_timeline_row(row, time_ms))
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
            self.status = f"Selected {len(self.selected_keyframes)} keyframes"
        elif self.keyframe_right_click_time is not None:
            keys = self._pick_keyframe_keys(self.drag_origin, timeline)
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
        if self.selected_keyframes:
            self.timeline_selected_layer_id = None
        self.drag_mode = None
        self.keyframe_marquee_current = None
        self.keyframe_right_click_time = None
        self.keyframe_marquee_additive = False

    def _move_keyframe(self, screen_x: int, timeline: pygame.Rect) -> None:
        if self.drag_key_time is None:
            return
        new_time = self._timeline_x_to_time(screen_x, timeline)
        start, end = self._timeline_window()
        if self.snap or self._timeline_uses_frame_ruler(timeline, end - start):
            new_time = self._snap_time_to_frame(new_time)
        delta = new_time - self.drag_key_time
        keys = set(self.selected_keyframes)
        selected_times = [time_ms for _target_id, _prop, time_ms in keys]
        if not selected_times:
            return
        delta = max(-min(selected_times), min(self.project.duration_ms - max(selected_times), delta))
        records: list[tuple[str, str, int, Layer | Shape | RandomLedEffect, object]] = []
        for target_id, prop, old_time in keys:
            target = self._find_keyframe_target(target_id)
            if not target:
                continue
            frame = next(
                (frame for frame in target.keyframes.get(prop, []) if frame.time_ms == old_time),
                None,
            )
            if frame:
                records.append((target_id, prop, old_time, target, frame))

        for target_id, prop in {(item[0], item[1]) for item in records}:
            group = [item for item in records if item[0] == target_id and item[1] == prop]
            target = group[0][3]
            selected_frames = {id(item[4]) for item in group}
            destinations = {item[2] + delta for item in group}
            target.keyframes[prop] = [
                frame for frame in target.keyframes.get(prop, [])
                if id(frame) in selected_frames or frame.time_ms not in destinations
            ]

        updated: set[tuple[str, str, int]] = set()
        touched: set[tuple[str, str]] = set()
        for target_id, prop, old_time, target, frame in records:
            frame.time_ms = old_time + delta
            updated.add((target_id, prop, frame.time_ms))
            touched.add((target_id, prop))
        for target_id, prop in touched:
            target = self._find_keyframe_target(target_id)
            if target:
                target.keyframes.get(prop, []).sort(key=lambda frame: frame.time_ms)
        self.selected_keyframes = updated
        self.drag_key_time += delta
        self.current_ms = self.drag_key_time

    def _delete_selected_keyframes(self) -> None:
        if not self.selected_keyframes:
            return
        self._begin_change()
        grouped: dict[tuple[str, str], set[int]] = {}
        for target_id, prop, time_ms in self.selected_keyframes:
            grouped.setdefault((target_id, prop), set()).add(time_ms)
        for (target_id, prop), deleted_times in grouped.items():
            target = self._find_keyframe_target(target_id)
            if not target or prop not in target.keyframes:
                continue
            original = target.keyframes[prop]
            deleted_first = bool(original) and original[0].time_ms in deleted_times
            remaining = [
                frame for frame in original if frame.time_ms not in deleted_times
            ]
            if remaining:
                target.keyframes[prop] = remaining
                if deleted_first:
                    setattr(target, prop, deepcopy(remaining[0].value))
            else:
                del target.keyframes[prop]
        self.selected_keyframes.clear()
        self._commit_change()
        self.status = "Selected keyframe deleted"

    def _copy_selected_keyframes(self) -> None:
        records: list[tuple[str, str, int, Keyframe]] = []
        for target_id, prop, time_ms in sorted(
            self.selected_keyframes, key=lambda key: (key[2], key[0], key[1]),
        ):
            target = self._find_keyframe_target(target_id)
            if not target:
                continue
            frame = next(
                (item for item in target.keyframes.get(prop, []) if item.time_ms == time_ms),
                None,
            )
            if frame:
                records.append((target_id, prop, time_ms, deepcopy(frame)))
        if not records:
            self.status = "Select one or more keyframes to copy"
            return
        origin = min(record[2] for record in records)
        self.keyframe_clipboard = [
            (target_id, prop, time_ms - origin, frame)
            for target_id, prop, time_ms, frame in records
        ]
        self.status = f"Copied {len(records)} keyframe{'s' if len(records) != 1 else ''}"

    def _paste_keyframes_at_playhead(self) -> None:
        if not self.keyframe_clipboard:
            self.status = "Keyframe clipboard is empty"
            return
        records = [
            (target, target_id, prop, offset, frame)
            for target_id, prop, offset, frame in self.keyframe_clipboard
            if (target := self._find_keyframe_target(target_id)) is not None
        ]
        if not records:
            self.status = "Copied keyframe targets no longer exist"
            return
        last_time = self.current_ms + max(record[3] for record in records)
        if last_time > 15000:
            self.status = "Paste would exceed the 15 second timeline limit"
            return

        self._begin_change()
        if last_time > self.project.duration_ms:
            self.project.duration_ms = math.ceil(last_time / 100.0) * 100
            self.duration_input = f"{self.project.duration_ms / 1000:.1f}"
            self._clamp_timeline_scroll()
        pasted: set[tuple[str, str, int]] = set()
        for target, target_id, prop, offset, source in records:
            time_ms = self.current_ms + offset
            frame = deepcopy(source)
            frame.time_ms = time_ms
            frames = [
                existing for existing in target.keyframes.get(prop, [])
                if existing.time_ms != time_ms
            ]
            frames.append(frame)
            frames.sort(key=lambda item: item.time_ms)
            target.keyframes[prop] = frames
            pasted.add((target_id, prop, time_ms))
        self.selected_keyframes = pasted
        self._remember_graph_channel(pasted)
        self._commit_change()
        self.status = f"Pasted {len(pasted)} keyframe{'s' if len(pasted) != 1 else ''} at playhead"

    def _apply_keyframe_context(self, action: str) -> None:
        if action == "delete":
            self._delete_selected_keyframes()
            return
        self._begin_change()
        for target_id, prop, time_ms in self.selected_keyframes:
            target = self._find_keyframe_target(target_id)
            if not target:
                continue
            for frame in target.keyframes.get(prop, []):
                if frame.time_ms == time_ms:
                    if action == "custom_bezier":
                        frame.easing = "bezier"
                        frame.bezier = frame.bezier or DEFAULT_BEZIER
                    else:
                        frame.easing = action
        self._commit_change()
        self.status = (
            "Custom Bezier — edit handles in Graph Editor"
            if action == "custom_bezier" else action.replace("_", " ").title()
        )

    def _context_menu_items(self) -> list[tuple[pygame.Rect, str, str]]:
        if not self.context_menu_pos:
            return []
        labels = [
            ("linear", "Linear"), ("ease_in", "Ease In"), ("ease_out", "Ease Out"),
            ("ease_in_out", "Ease In / Out"), ("custom_bezier", "Custom Bezier"),
            ("delete", "Delete keyframe"),
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
        self.timeline_selected_layer_id = None
        self.status = f"Moved layer to position {target + 1}"

    def _scrub_property(self, screen_x: int) -> None:
        if not self.selected or not self.scrub_prop or not self.drag_shape_state:
            return
        prop = self.scrub_prop
        start = self.drag_shape_state[prop]
        delta = screen_x - self.drag_origin[0]
        if prop == "rotation":
            total = start + self.drag_shape_state.get("rotation_turns", 0.0) * 360.0 + delta * 0.5
            if self.snap:
                total = round(total)
            self._set_rotation_total(total)
            return
        sensitivity = 0.002 if prop in {"x", "y", "width", "height", "opacity"} else 0.5
        if prop == "stroke_width":
            sensitivity = 0.00025
        elif prop in {"feather", "mask_expansion"}:
            sensitivity = 0.00025
        elif prop == "rotation_turns":
            sensitivity = 0.02
        value = start + delta * sensitivity
        if prop in {"x", "y", "opacity"}:
            value = max(0.0, min(1.0, value))
        elif prop in {"width", "height"}:
            value = max(0.01, value)
        elif prop == "stroke_width":
            value = max(0.001, min(0.05, value))
        elif prop == "feather":
            value = max(0.0, min(0.1, value))
        elif prop == "mask_expansion":
            value = max(-0.1, min(0.1, value))
        elif prop == "rotation_turns":
            value = max(-100.0, min(100.0, round(value)))
        if self.snap:
            step = 0.001 if prop in {"stroke_width", "feather", "mask_expansion"} else 0.01
            value = round(value / step) * step
        self._set_animated(prop, value)

    @staticmethod
    def _random_effect_parameter_value(
        effect: RandomLedEffect, prop: str, time_ms: int | None = None,
    ) -> float | int:
        if prop == "opacity" and time_ms is not None:
            return float(effect.value_at(prop, time_ms))
        return getattr(effect, prop)

    @staticmethod
    def _normalize_random_effect_parameter(prop: str, value: float) -> float | int:
        if prop == "seed":
            return max(0, min(2147483647, int(round(value))))
        if prop == "life_ms":
            return max(50, min(10000, int(round(value))))
        if prop == "born_speed":
            return round(max(0.5, min(100.0, float(value))), 1)
        if prop == "particle_count":
            return max(1, min(68, int(round(value))))
        if prop == "opacity":
            return round(max(0.0, min(1.0, float(value))), 4)
        raise ValueError(f"Unknown Random LED parameter: {prop}")

    def _set_random_effect_parameter(
        self, effect: RandomLedEffect, prop: str, value: float,
    ) -> float | int:
        normalized = self._normalize_random_effect_parameter(prop, value)
        if prop == "opacity":
            if self.current_ms == 0 and not effect.keyframes.get(prop):
                effect.opacity = float(normalized)
                self.selected_keyframes.clear()
            else:
                effect.add_keyframe(prop, self.current_ms, float(normalized))
                self.selected_keyframes = {(effect.id, prop, self.current_ms)}
                self.timeline_expanded_layers.add(self.project.layers[self.active_layer].id)
        else:
            setattr(effect, prop, normalized)
        return normalized

    def _scrub_random_effect_parameter(self, screen_x: int) -> None:
        effect = self._active_random_led_effect()
        if not effect or not self.scrub_prop or self.random_effect_drag_start is None:
            return
        prop = self.scrub_prop
        sensitivities = {
            "seed": 1.0,
            "life_ms": 5.0,
            "born_speed": 0.1,
            "particle_count": 0.2,
            "opacity": 0.005,
        }
        value = float(self.random_effect_drag_start) + (
            screen_x - self.drag_origin[0]
        ) * sensitivities[prop]
        normalized = self._set_random_effect_parameter(effect, prop, value)
        self.status = self._random_effect_parameter_status(prop, normalized)

    def _open_random_effect_input(self, prop: str) -> None:
        effect = self._active_random_led_effect()
        if not effect:
            return
        value = self._random_effect_parameter_value(effect, prop, self.current_ms)
        self.random_effect_editing = prop
        self.random_effect_input = (
            f"{float(value) * 100:.1f}" if prop == "opacity"
            else f"{float(value):g}" if prop == "born_speed"
            else str(int(value))
        )
        self.random_effect_input_select_all = True
        self.status = f"Type {self._random_effect_parameter_label(prop)}, then press Enter"

    @staticmethod
    def _random_effect_parameter_label(prop: str) -> str:
        return {
            "seed": "Random seed",
            "life_ms": "Life",
            "born_speed": "Born speed",
            "particle_count": "Max active",
            "opacity": "Opacity",
        }[prop]

    @staticmethod
    def _random_effect_parameter_status(prop: str, value: float | int) -> str:
        label = Editor._random_effect_parameter_label(prop)
        if prop == "life_ms":
            return f"{label}: {int(value)} ms"
        if prop == "born_speed":
            return f"{label}: {float(value):g} / sec"
        if prop == "opacity":
            return f"{label}: {float(value) * 100:.1f}%"
        return f"{label}: {int(value)}"

    def _set_animated(self, prop: str, value) -> None:
        if not self.selected:
            return
        if self.current_ms == 0 and not self.selected.keyframes.get(prop):
            setattr(self.selected, prop, value)
        else:
            self.selected.add_keyframe(prop, self.current_ms, value)

    def _set_canvas_enabled_key(self, layer: Layer, enabled: bool) -> None:
        layer.add_keyframe("canvas_enabled", self.current_ms, enabled)
        self.selected = None
        self.random_led_editor_open = False
        self.selected_keyframes = {(layer.id, "canvas_enabled", self.current_ms)}
        self.timeline_expanded_layers.add(layer.id)
        mode = "transparent empty LEDs" if enabled else "blackout empty LEDs"
        self.status = f"Canvas {'enabled' if enabled else 'disabled'} at {self.current_ms} ms — {mode}"

    def _set_rotation_total(self, total_degrees: float) -> None:
        turns = math.floor(total_degrees / 360.0)
        angle = total_degrees - turns * 360.0
        self._set_animated("rotation", round(angle, 6))
        self._set_animated("rotation_turns", float(turns))

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

    def _active_random_led_effect(self) -> RandomLedEffect | None:
        if not 0 <= self.active_layer < len(self.project.layers):
            return None
        effects = self.project.layers[self.active_layer].effects
        return effects[0] if effects else None

    def _active_keyframe_target(self) -> Layer | Shape | RandomLedEffect | None:
        layer = self.project.layers[self.active_layer]
        if layer.is_canvas:
            return layer
        if self.random_led_editor_open:
            return self._active_random_led_effect()
        return self.selected

    def _find_keyframe_target(self, target_id: str) -> Layer | Shape | RandomLedEffect | None:
        for layer in self.project.layers:
            for target in [layer, *layer.shapes, *layer.effects]:
                if target.id == target_id:
                    return target
        return None

    def _playback_duration(self) -> int:
        effect = self._active_imported_effect()
        return effect.duration_ms if effect else self.project.duration_ms

    def load_effect_data_file(self, path: str | Path) -> None:
        path = Path(path)
        self.imported_effects = load_effect_data(path)
        self.effect_data_path = path
        self.active_import_index = 0
        self.timeline_mode = "dope"
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
        self._install_project(
            project, target.resolve(), saved=True, status=f"Loaded project: {target.name}",
        )

    def _install_project(
        self,
        project: Project,
        project_path: Path | None,
        *,
        saved: bool,
        status: str,
    ) -> None:
        self.project = project
        self.project_path = project_path
        self.saved_project_state = deepcopy(project.to_dict()) if saved else None
        self.selected = None
        self.selected_keyframes.clear()
        self.timeline_selected_layer_id = None
        self.active_layer = 0
        self.current_ms = 0
        self.playing = False
        self.active_import_index = None
        self.stencil = False
        self.calibration = False
        self.gradient_editor_open = False
        self.random_led_editor_open = False
        self.random_effect_editing = None
        self.random_effect_input_select_all = False
        self.random_effect_drag_start = None
        self.export_bank_open = False
        self.help_open = False
        self.property_editing = None
        self.duration_input = f"{self.project.duration_ms / 1000:.1f}"
        self.timeline_zoom = 1.0
        self.timeline_scroll_ms = 0.0
        self.timeline_layer_scroll = 0
        self.timeline_expanded_layers.clear()
        self.timeline_mode = "dope"
        self.graph_target_id = None
        self.graph_prop = None
        self.zoom = 1.0
        self.pan.update(0, 0)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.change_snapshot = None
        self.status = status

    def _confirm_replace_project(self, title: str, message: str) -> bool:
        if not self._project_is_dirty():
            return True
        root = None
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except tk.TclError:
                pass
            return bool(messagebox.askyesno(title, message, parent=root))
        except Exception as error:
            self.status = f"Confirmation failed: {error}"
            return False
        finally:
            if root is not None:
                root.destroy()

    def _new_project(self, confirm: bool = True) -> bool:
        if confirm and not self._confirm_replace_project(
            "New project",
            "The current project has unsaved changes. Discard them and start a new project?",
        ):
            self.status = "New project cancelled — current project kept"
            return False
        self._install_project(
            Project("Untitled effect"), None, saved=False, status="New blank project",
        )
        return True

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

    def _open_export_bank(self) -> None:
        self.export_bank_open = True
        self.help_open = False
        self.playing = False
        self.export_bank_selected = -1
        self.export_bank_scroll = 0
        self.export_bank_name_editing = False
        self.export_bank_id_editing = False
        if not self.export_bank_effects and self.imported_effects and self.effect_data_path:
            self.export_bank_effects = deepcopy(self.imported_effects)
            self.export_bank_path = self.effect_data_path
            self.export_bank_status = f"Mapped {len(self.export_bank_effects)} effects from {self.effect_data_path.name}"
        else:
            self.export_bank_status = "Effect Bank ready — current project is included"

    def map_effect_bank_file(self, path: str | Path) -> None:
        target = Path(path)
        effects = load_effect_data(target)
        self.export_bank_effects = deepcopy(effects)
        self.export_bank_path = target.resolve()
        self.export_bank_selected = 0 if effects else -1
        self.export_bank_scroll = 0
        self.export_bank_status = f"Mapped {len(effects)} effects from {target.name}"

    def export_effect_bank_file(self, path: str | Path) -> Path:
        led_errors = self.led_map.validate()
        if led_errors:
            raise ValueError("LED map: " + "; ".join(led_errors))
        current = project_to_imported_effect(self.project, self.led_map.export_slots())
        destination = export_effect_bank(
            [*deepcopy(self.export_bank_effects), current],
            path,
            max_bytes=EFFECT_BANK_CAPACITY,
        )
        self.export_bank_path = destination.resolve()
        self.export_bank_status = (
            f"Exported {len(self.export_bank_effects) + 1} effects — "
            f"{self._export_bank_used_bytes() / 1024:.1f} KiB"
        )
        self.status = f"Effect bank exported: {destination.name}"
        return destination

    def _choose_export_bank_map(self) -> None:
        root = None
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            selected = filedialog.askopenfilename(
                title="Map firmware effect_data.h",
                initialdir=str(self.export_bank_path.parent if self.export_bank_path else ROOT),
                filetypes=[("Arduino header", "*.h"), ("All files", "*.*")],
            )
            if selected:
                self.map_effect_bank_file(selected)
            else:
                self.export_bank_status = "Header mapping cancelled"
        except Exception as error:
            self.export_bank_status = f"Mapping failed: {error}"
        finally:
            if root is not None:
                root.destroy()

    def _choose_export_bank_save(self) -> None:
        root = None
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            default = self.export_bank_path or EXPORT_FILE
            selected = filedialog.asksaveasfilename(
                title="Export V4 effect bank",
                initialdir=str(default.parent),
                initialfile=default.name,
                defaultextension=".h",
                filetypes=[("Arduino header", "*.h"), ("All files", "*.*")],
            )
            if selected:
                self.export_effect_bank_file(selected)
            else:
                self.export_bank_status = "Effect bank export cancelled"
        except (OSError, ValueError) as error:
            self.export_bank_status = f"Export blocked: {error}"
        except Exception as error:
            self.export_bank_status = f"Export failed: {error}"
        finally:
            if root is not None:
                root.destroy()

    def _export_bank_used_bytes(self) -> int:
        return self.project.flash_bytes + sum(effect.flash_bytes for effect in self.export_bank_effects)

    def _export_bank_validation_errors(self) -> list[str]:
        ids = [self.project.effect_id, *(effect.effect_id for effect in self.export_bank_effects)]
        duplicates = sorted({effect_id for effect_id in ids if ids.count(effect_id) > 1})
        errors = [f"Duplicate ID {effect_id}" for effect_id in duplicates]
        used = self._export_bank_used_bytes()
        if used > EFFECT_BANK_CAPACITY:
            errors.append(f"Over capacity by {(used - EFFECT_BANK_CAPACITY) / 1024:.1f} KiB")
        if not self.project.name.strip() or any(not effect.name.strip() for effect in self.export_bank_effects):
            errors.append("Every effect needs a name")
        errors.extend(self.led_map.validate())
        return errors

    def _export_bank_selected_effect(self) -> ImportedEffect | None:
        if 0 <= self.export_bank_selected < len(self.export_bank_effects):
            return self.export_bank_effects[self.export_bank_selected]
        return None

    def _load_export_bank_project(self, confirm: bool = True) -> bool:
        effect = self._export_bank_selected_effect()
        if effect is None or effect.project_data is None:
            self.export_bank_status = "Selected effect has no embedded CnC Light project"
            return False
        if confirm and not self._confirm_replace_project(
            "Load embedded project",
            f"Discard unsaved changes and load the embedded project for {effect.name}?",
        ):
            self.export_bank_status = "Embedded project load cancelled"
            return False
        try:
            project = Project.from_dict(effect.project_data)
            project.name = effect.name
            project.effect_id = effect.effect_id
        except (TypeError, ValueError, KeyError) as error:
            self.export_bank_status = f"Embedded project is invalid: {error}"
            return False
        self._install_project(
            project,
            None,
            saved=False,
            status=f"Loaded embedded project: {effect.name} · ID {effect.effect_id}",
        )
        return True

    def _handle_export_bank_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if self.export_bank_name_editing or self.export_bank_id_editing:
                self._handle_export_bank_input(event)
            elif event.key == pygame.K_ESCAPE:
                self.export_bank_open = False
                self.status = "Closed Effect Bank"
            return
        if event.type == pygame.MOUSEWHEEL:
            panel = self._export_bank_panel_rect()
            list_rect = pygame.Rect(panel.x + 26, panel.y + 224, panel.width - 398, panel.height - 294)
            mouse = getattr(event, "pos", pygame.mouse.get_pos())
            if list_rect.collidepoint(mouse):
                visible = max(1, list_rect.height // 53)
                maximum = max(0, len(self.export_bank_effects) + 1 - visible)
                self.export_bank_scroll = max(0, min(maximum, self.export_bank_scroll - event.y))
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.export_bank_name_editing or self.export_bank_id_editing:
            if not self._commit_export_bank_input():
                return
        action = next(
            (
                action for rect, action, _label in reversed(self.buttons)
                if action.startswith("export_bank_") and rect.collidepoint(event.pos)
            ),
            None,
        )
        if action:
            self._export_bank_action(action)

    def _export_bank_action(self, action: str) -> None:
        if action == "export_bank_close":
            self.export_bank_open = False
            self.status = "Closed Effect Bank"
        elif action == "export_bank_map":
            self._choose_export_bank_map()
        elif action == "export_bank_write":
            errors = self._export_bank_validation_errors()
            if errors:
                self.export_bank_status = "Export blocked: " + " · ".join(errors)
            else:
                self._choose_export_bank_save()
        elif action == "export_bank_load_project":
            self._load_export_bank_project()
        elif action == "export_bank_edit_name":
            effect = self._export_bank_selected_effect()
            self.export_bank_name_input = effect.name if effect else self.project.name
            self.export_bank_name_editing = True
            self.export_bank_id_editing = False
            self.export_bank_input_select_all = True
        elif action == "export_bank_edit_id":
            effect = self._export_bank_selected_effect()
            self.export_bank_id_input = str(effect.effect_id if effect else self.project.effect_id)
            self.export_bank_id_editing = True
            self.export_bank_name_editing = False
            self.export_bank_input_select_all = True
        elif action == "export_bank_remove" and self.export_bank_selected >= 0:
            removed = self.export_bank_effects.pop(self.export_bank_selected)
            self.export_bank_selected = -1
            self.export_bank_status = f"Removed {removed.name} from export bank"
        elif action.startswith("export_bank_select:"):
            self.export_bank_selected = int(action.split(":", 1)[1])
            self.export_bank_name_editing = False
            self.export_bank_id_editing = False

    def _handle_export_bank_input(self, event: pygame.event.Event) -> None:
        ctrl = bool(getattr(event, "mod", pygame.key.get_mods()) & pygame.KMOD_CTRL)
        if ctrl and event.key == pygame.K_a:
            self.export_bank_input_select_all = True
            return
        if ctrl and event.key == pygame.K_v:
            value = self._clipboard_text().strip()
            if self.export_bank_id_editing:
                value = "".join(character for character in value if character.isdigit())
            if self.export_bank_input_select_all:
                if self.export_bank_name_editing:
                    self.export_bank_name_input = value[:40]
                else:
                    self.export_bank_id_input = value[:3]
            elif self.export_bank_name_editing:
                self.export_bank_name_input = (self.export_bank_name_input + value)[:40]
            else:
                self.export_bank_id_input = (self.export_bank_id_input + value)[:3]
            self.export_bank_input_select_all = False
            return
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            self._commit_export_bank_input()
            return
        if event.key == pygame.K_ESCAPE:
            self.export_bank_name_editing = False
            self.export_bank_id_editing = False
            return
        if event.key == pygame.K_BACKSPACE:
            if self.export_bank_input_select_all:
                if self.export_bank_name_editing:
                    self.export_bank_name_input = ""
                else:
                    self.export_bank_id_input = ""
                self.export_bank_input_select_all = False
            elif self.export_bank_name_editing:
                self.export_bank_name_input = self.export_bank_name_input[:-1]
            else:
                self.export_bank_id_input = self.export_bank_id_input[:-1]
            return
        character = getattr(event, "unicode", "")
        if self.export_bank_name_editing and character.isprintable():
            if self.export_bank_input_select_all:
                self.export_bank_name_input = ""
                self.export_bank_input_select_all = False
            self.export_bank_name_input = (self.export_bank_name_input + character)[:40]
        elif self.export_bank_id_editing and character.isdigit():
            if self.export_bank_input_select_all:
                self.export_bank_id_input = ""
                self.export_bank_input_select_all = False
            self.export_bank_id_input = (self.export_bank_id_input + character)[:3]

    def _commit_export_bank_input(self) -> bool:
        effect = self._export_bank_selected_effect()
        if self.export_bank_name_editing:
            name = self.export_bank_name_input.strip()
            if not name:
                self.export_bank_status = "Effect name cannot be empty"
                return False
            if effect:
                project_data = deepcopy(effect.project_data)
                if project_data is not None:
                    project_data["name"] = name
                self.export_bank_effects[self.export_bank_selected] = replace(
                    effect, name=name, project_data=project_data,
                )
            else:
                self._begin_change()
                self.project.name = name
                self._commit_change()
            self.export_bank_name_editing = False
            self.export_bank_status = f"Effect renamed to {name}"
            return True
        if self.export_bank_id_editing:
            if not self.export_bank_id_input:
                self.export_bank_status = "Effect ID must be between 1 and 255"
                return False
            effect_id = int(self.export_bank_id_input)
            if not 1 <= effect_id <= 255:
                self.export_bank_status = "Effect ID must be between 1 and 255"
                return False
            used = {self.project.effect_id}
            used.update(
                item.effect_id for index, item in enumerate(self.export_bank_effects)
                if index != self.export_bank_selected
            )
            if effect_id in used and (effect is not None or effect_id != self.project.effect_id):
                self.export_bank_status = f"Effect ID {effect_id} is already used"
                return False
            if effect:
                project_data = deepcopy(effect.project_data)
                if project_data is not None:
                    project_data["effect_id"] = effect_id
                self.export_bank_effects[self.export_bank_selected] = replace(
                    effect, effect_id=effect_id, project_data=project_data,
                )
            else:
                self._begin_change()
                self.project.effect_id = effect_id
                self._commit_change()
            self.export_bank_id_editing = False
            self.export_bank_status = f"Firmware effect ID: {effect_id}"
            return True
        return True

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

    def _handle_random_effect_input(self, event: pygame.event.Event) -> None:
        prop = self.random_effect_editing
        effect = self._active_random_led_effect()
        if not prop or not effect:
            self.random_effect_editing = None
            return
        ctrl = bool(getattr(event, "mod", pygame.key.get_mods()) & pygame.KMOD_CTRL)
        if ctrl and event.key == pygame.K_a:
            self.random_effect_input_select_all = True
            self.status = f"{self._random_effect_parameter_label(prop)} value selected"
            return
        if ctrl and event.key == pygame.K_v:
            pasted = self._clipboard_text().strip().replace(",", ".")
            try:
                float(pasted)
            except ValueError:
                self.status = "Clipboard does not contain a valid number"
                return
            self.random_effect_input = pasted[:12] if self.random_effect_input_select_all else (
                self.random_effect_input + pasted
            )[:12]
            self.random_effect_input_select_all = False
            self.status = f"Pasted {self._random_effect_parameter_label(prop)} — press Enter"
            return
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            try:
                entered = float(self.random_effect_input.replace(",", "."))
            except ValueError:
                self.status = f"{self._random_effect_parameter_label(prop)} must be a number"
                return
            input_limits = {
                "seed": (0.0, 2147483647.0),
                "life_ms": (50.0, 10000.0),
                "born_speed": (0.5, 100.0),
                "particle_count": (1.0, 68.0),
                "opacity": (0.0, 100.0),
            }
            low, high = input_limits[prop]
            if not low <= entered <= high:
                self.status = (
                    f"{self._random_effect_parameter_label(prop)} must be {low:g}–{high:g}"
                )
                return
            value = entered / 100.0 if prop == "opacity" else entered
            self._begin_change()
            normalized = self._set_random_effect_parameter(effect, prop, value)
            self._commit_change()
            self.random_effect_editing = None
            self.random_effect_input_select_all = False
            self.status = self._random_effect_parameter_status(prop, normalized)
            return
        if event.key == pygame.K_ESCAPE:
            self.random_effect_editing = None
            self.random_effect_input_select_all = False
            self.status = f"{self._random_effect_parameter_label(prop)} edit cancelled"
            return
        if event.key == pygame.K_BACKSPACE:
            if self.random_effect_input_select_all:
                self.random_effect_input = ""
                self.random_effect_input_select_all = False
            else:
                self.random_effect_input = self.random_effect_input[:-1]
            return
        character = getattr(event, "unicode", "")
        if character.isdigit() or character in ".,":
            if self.random_effect_input_select_all:
                self.random_effect_input = ""
                self.random_effect_input_select_all = False
            normalized = "." if character == "," else character
            if normalized == "." and "." in self.random_effect_input:
                return
            if len(self.random_effect_input) < 12:
                self.random_effect_input += normalized

    def _handle_property_input(self, event: pygame.event.Event) -> None:
        prop = self.property_editing
        if not prop or not self.selected:
            self.property_editing = None
            return
        ctrl = bool(getattr(event, "mod", pygame.key.get_mods()) & pygame.KMOD_CTRL)
        if ctrl and event.key == pygame.K_a:
            self.property_input_select_all = True
            self.status = f"{TIMELINE_PROPERTY_LABELS[prop]} value selected"
            return
        if ctrl and event.key == pygame.K_v:
            pasted = self._clipboard_text().strip().replace(",", ".")
            try:
                float(pasted)
            except ValueError:
                self.status = "Clipboard does not contain a valid number"
                return
            self.property_input = pasted[:12] if self.property_input_select_all else (
                self.property_input + pasted
            )[:12]
            self.property_input_select_all = False
            self.status = f"Pasted {TIMELINE_PROPERTY_LABELS[prop]} — press Enter"
            return
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            try:
                value = float(self.property_input.replace(",", "."))
            except ValueError:
                self.status = f"{TIMELINE_PROPERTY_LABELS[prop]} must be a number"
                return
            if prop in {"feather", "mask_expansion"}:
                value /= 100.0
            limits = {
                "x": (0.0, 1.0), "y": (0.0, 1.0),
                "width": (0.01, 5.0), "height": (0.01, 5.0),
                "opacity": (0.0, 1.0),
                "rotation_turns": (-100.0, 100.0),
                "feather": (0.0, 0.1), "mask_expansion": (-0.1, 0.1),
            }
            if prop in limits and not limits[prop][0] <= value <= limits[prop][1]:
                low, high = limits[prop]
                if prop in {"feather", "mask_expansion"}:
                    low, high = low * 100.0, high * 100.0
                self.status = f"{TIMELINE_PROPERTY_LABELS[prop]} must be {low:g}–{high:g}"
                return
            self._begin_change()
            if prop == "rotation":
                state = self.selected.state_at(self.current_ms)
                added_turns = math.floor(value / 360.0)
                angle = value - added_turns * 360.0
                self._set_animated("rotation", round(angle, 6))
                self._set_animated(
                    "rotation_turns", state.get("rotation_turns", 0.0) + added_turns,
                )
                value = angle
            elif prop == "rotation_turns":
                value = float(round(value))
                self._set_animated(prop, value)
            else:
                self._set_animated(prop, value)
            self._commit_change()
            self.property_editing = None
            self.property_input_select_all = False
            if prop in {"rotation", "rotation_turns"}:
                state = self.selected.state_at(self.current_ms)
                self.status = f"Rotation: {state['rotation']:.1f}° ×{state['rotation_turns']:.0f}"
            else:
                display_value = value * 100.0 if prop in {"feather", "mask_expansion"} else value
                suffix = "%" if prop in {"feather", "mask_expansion"} else ""
                self.status = f"{TIMELINE_PROPERTY_LABELS[prop]}: {display_value:.3f}{suffix}"
            return
        if event.key == pygame.K_ESCAPE:
            self.property_editing = None
            self.property_input_select_all = False
            self.status = f"{TIMELINE_PROPERTY_LABELS[prop]} edit cancelled"
            return
        if event.key == pygame.K_BACKSPACE:
            if self.property_input_select_all:
                self.property_input = ""
                self.property_input_select_all = False
            else:
                self.property_input = self.property_input[:-1]
            return
        character = getattr(event, "unicode", "")
        if character.isdigit() or character in "-.,":
            if self.property_input_select_all:
                self.property_input = ""
                self.property_input_select_all = False
            normalized = "." if character == "," else character
            if normalized == "-" and self.property_input:
                return
            if normalized == "." and "." in self.property_input:
                return
            if len(self.property_input) < 12:
                self.property_input += normalized

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
        if self.export_bank_open:
            self._draw_export_bank()
        if self.help_open:
            self._draw_help()

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

        self._button(pygame.Rect(width - 118, 11, 58, 32), "new", "New", False)
        self._button(pygame.Rect(width - 52, 11, 36, 32), "help", "?", False)

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
                canvas_scale = min(canvas.size)
                expansion_px = float(state.get("mask_expansion", 0.0)) * canvas_scale
                feather_px = max(0.0, float(state.get("feather", 0.0)) * canvas_scale)
                pad = max(4, math.ceil(max(0.0, expansion_px) + feather_px / 2.0) + 3)
                surface = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)
                local = pygame.Rect(pad, pad, w, h)
                mask = self._shape_mask_surface(
                    surface.get_size(), shape.kind, local, state, canvas_scale,
                )
                gradient = (
                    state.get("fill_mode", "fill") == "fill"
                    and state.get("gradient_type", "solid") in {"linear", "radial"}
                    and len(state.get("gradient_stops", [])) >= 2
                )
                if gradient:
                    paint = self._gradient_surface(
                        surface.get_size(), state["gradient_stops"], state["gradient_type"],
                        state.get("gradient_radial_mode", "radius"),
                        float(state.get("gradient_angle", 0.0)), int(220 * state["opacity"]),
                    )
                else:
                    paint = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
                    paint.fill((*state["color"], int(220 * state["opacity"])))
                paint.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                surface.blit(paint, (0, 0))
                rotated = pygame.transform.rotate(surface, -state["rotation_total"])
                rect = rotated.get_rect(center=(cx, cy))
                self.screen.blit(rotated, rect)

    def _shape_mask_surface(
        self, size: tuple[int, int], kind: str, local: pygame.Rect,
        state: dict, canvas_scale: int,
    ) -> pygame.Surface:
        mask = pygame.Surface(size, pygame.SRCALPHA)
        expansion = float(state.get("mask_expansion", 0.0)) * canvas_scale
        feather = max(0.0, float(state.get("feather", 0.0)) * canvas_scale)
        if feather <= 0.01:
            samples = [(expansion, 255)]
        else:
            steps = max(6, min(24, math.ceil(feather)))
            samples = [
                (expansion + feather * (0.5 - index / steps), round(255 * index / steps))
                for index in range(steps + 1)
            ]
        for offset, alpha in samples:
            self._draw_mask_geometry(mask, kind, local, state, offset, alpha, canvas_scale)
        return mask

    @staticmethod
    def _draw_mask_geometry(
        mask: pygame.Surface, kind: str, local: pygame.Rect, state: dict,
        offset: float, alpha: int, canvas_scale: int,
    ) -> None:
        rect = local.inflate(round(offset * 2), round(offset * 2))
        if rect.width <= 0 or rect.height <= 0 or alpha <= 0:
            return
        color = (255, 255, 255, max(0, min(255, alpha)))
        fill_mode = state.get("fill_mode", "fill")
        stroke = float(state.get("stroke_width", 0.012)) * canvas_scale
        draw_width = 0
        if fill_mode == "stroke":
            draw_width = max(1, round(stroke + offset * 2))
            draw_width = min(draw_width, max(1, min(rect.size) // 2))
        if kind == "ellipse":
            pygame.draw.ellipse(mask, color, rect, draw_width)
        elif kind == "rectangle":
            pygame.draw.rect(mask, color, rect, draw_width, border_radius=3)
        elif kind == "triangle":
            pygame.draw.polygon(mask, color, [
                (rect.centerx, rect.top), (rect.right, rect.bottom), (rect.left, rect.bottom),
            ], draw_width)
        else:
            line_width = draw_width if fill_mode == "stroke" else max(1, rect.height)
            pygame.draw.line(mask, color, (rect.left, rect.centery), (rect.right, rect.centery), line_width)

    @staticmethod
    def _gradient_surface(
        size: tuple[int, int], stops: list[GradientStop], gradient_type: str,
        radial_mode: str, angle: float, alpha: int,
    ) -> pygame.Surface:
        width, height = size
        if gradient_type == "radial":
            if radial_mode == "angular":
                side = max(2, max(width, height))
                square = pygame.Surface((side, side), pygame.SRCALPHA)
                center = (side // 2, side // 2)
                radius = math.hypot(side, side)
                steps = 240
                for index in range(steps):
                    amount = index / steps
                    start = math.radians(angle + index * 360.0 / steps)
                    end = math.radians(angle + (index + 1) * 360.0 / steps)
                    points = [
                        center,
                        (center[0] + math.cos(start) * radius, center[1] + math.sin(start) * radius),
                        (center[0] + math.cos(end) * radius, center[1] + math.sin(end) * radius),
                    ]
                    pygame.draw.polygon(square, (*sample_gradient(stops, amount), alpha), points)
                return pygame.transform.smoothscale(square, size)

            result = pygame.Surface(size, pygame.SRCALPHA)
            result.fill((*sample_gradient(stops, 1.0), alpha))
            steps = max(32, min(160, max(width, height)))
            for index in range(steps - 1, -1, -1):
                amount = index / max(1, steps - 1)
                rect = pygame.Rect(0, 0, max(1, round(width * amount)), max(1, round(height * amount)))
                rect.center = (width // 2, height // 2)
                pygame.draw.ellipse(result, (*sample_gradient(stops, amount), alpha), rect)
            return result

        side = max(2, math.ceil(math.hypot(width, height)))
        strip = pygame.Surface((side, side), pygame.SRCALPHA)
        for x in range(side):
            amount = x / max(1, side - 1)
            pygame.draw.line(strip, (*sample_gradient(stops, amount), alpha), (x, 0), (x, side))
        rotated = pygame.transform.rotate(strip, -angle)
        result = pygame.Surface(size, pygame.SRCALPHA)
        result.blit(rotated, rotated.get_rect(center=(width // 2, height // 2)))
        return result

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
        if effect:
            firmware_colors = [
                (0, 0, 0) if effect.overlay and color == TRANSPARENT_SENTINEL else color
                for color in effect.colors_at(self.current_ms)
            ]
        else:
            slots = self.led_map.export_slots()
            active_colors = iter(render_leds(
                self.project, [point for point in slots if point is not None], self.current_ms,
            ))
            firmware_colors = [
                next(active_colors) if point is not None else (0, 0, 0)
                for point in slots
            ]
        return [
            firmware_colors[led.firmware_index]
            if 0 <= led.firmware_index < len(firmware_colors) else (0, 0, 0)
            for led in self.led_map.leds
        ]

    def _timeline_frame_ms(self) -> float:
        imported = self._active_imported_effect()
        return float(imported.frame_ms) if imported else float(self.project.frame_ms or 50)

    def _timeline_uses_frame_ruler(self, timeline: pygame.Rect, visible_ms: float) -> bool:
        track = self._timeline_track(timeline)
        pixels_per_frame = track.width * self._timeline_frame_ms() / max(1.0, visible_ms)
        return self._active_imported_effect() is not None or pixels_per_frame >= 18

    def _draw_graph_editor(
        self, timeline: pygame.Rect, start: float, end: float
    ) -> None:
        track = self._timeline_track(timeline)
        area = self._graph_area(timeline)
        channel = self._graph_channel()
        if not channel and self._choose_graph_channel():
            channel = self._graph_channel()
        if not channel:
            message = self.font.render(
                "Select an animated numeric property in the Dope Sheet",
                True, (153, 161, 180),
            )
            self.screen.blit(message, message.get_rect(center=area.center))
            return

        target, prop = channel
        low, high = self._graph_value_range(target, prop)
        pygame.draw.rect(self.screen, (19, 22, 29), area)
        for index in range(5):
            ratio = index / 4
            y = round(area.bottom - ratio * area.height)
            value = low + ratio * (high - low)
            pygame.draw.line(self.screen, (49, 55, 68), (area.x, y), (area.right, y), 1)
            value_text = f"{value:.3f}" if abs(high - low) <= 10 else f"{value:.1f}"
            rendered = self.small.render(value_text, True, (111, 122, 143))
            self.screen.blit(rendered, (track.x - rendered.get_width() - 8, y - 7))

        current_value = float(target.value_at(prop, self.current_ms))
        current_y = self._graph_point(self.current_ms, current_value, timeline, (low, high))[1]
        pygame.draw.line(
            self.screen, (51, 91, 114), (area.x, current_y), (area.right, current_y), 1,
        )

        sample_count = max(80, min(360, track.width // 3))
        curve_points = []
        for index in range(sample_count + 1):
            ratio = index / sample_count
            time_ms = round(start + ratio * (end - start))
            value = float(target.value_at(prop, time_ms))
            curve_points.append(self._graph_point(time_ms, value, timeline, (low, high)))
        if len(curve_points) >= 2:
            pygame.draw.lines(self.screen, (82, 202, 255), False, curve_points, 2)

        handle_points = self._bezier_handle_points(
            timeline, self._selected_bezier_segment(), (low, high)
        )
        if handle_points:
            p0, p1, p2, p3 = handle_points
            pygame.draw.line(self.screen, (119, 104, 196), p0, p1, 1)
            pygame.draw.line(self.screen, (119, 104, 196), p3, p2, 1)
            for point in (p1, p2):
                pygame.draw.circle(self.screen, (32, 35, 45), point, 6)
                pygame.draw.circle(self.screen, (174, 143, 255), point, 6, 2)

        frames = target.keyframes[prop]
        for before, after in zip(frames, frames[1:]):
            midpoint = (before.time_ms + after.time_ms) // 2
            if not start <= midpoint <= end:
                continue
            label = {
                "linear": "LIN", "ease_in": "IN", "ease_out": "OUT",
                "ease_in_out": "IN/OUT", "bezier": "BEZIER",
            }.get(after.easing, after.easing.upper())
            x = round(self._time_to_timeline_x(midpoint, timeline))
            badge = self.small.render(label, True, (119, 170, 202))
            self.screen.blit(badge, (x - badge.get_width() // 2, area.y + 4))

        for frame in frames:
            if not start <= frame.time_ms <= end:
                continue
            point = self._graph_point(frame.time_ms, float(frame.value), timeline, (low, high))
            key = (target.id, prop, frame.time_ms)
            selected = key in self.selected_keyframes
            pygame.draw.circle(self.screen, (245, 248, 255) if selected else (255, 202, 70), point, 7)
            pygame.draw.circle(self.screen, ACCENT if selected else (96, 72, 25), point, 7, 2)

        channel_name = TIMELINE_PROPERTY_LABELS.get(prop, prop.replace("_", " ").title())
        title = f"{target.name} · {channel_name}"
        while len(title) > 6 and self.small.size(title)[0] > 164:
            title = title[:-4] + "..."
        self.screen.blit(self.small.render(title, True, (218, 226, 239)), (timeline.x + 8, track.y + 7))
        value_label = self.small.render(f"Now {current_value:.3f}", True, (91, 203, 255))
        self.screen.blit(value_label, (timeline.x + 8, track.y + 28))
        self.screen.blit(
            self.small.render("Drag point: time + value", True, (122, 132, 151)),
            (timeline.x + 8, track.y + 51),
        )

        play_x = round(self._time_to_timeline_x(self.current_ms, timeline))
        if track.x <= play_x <= track.right:
            pygame.draw.polygon(
                self.screen, (255, 83, 72),
                [(play_x - 6, timeline.y + 20), (play_x + 6, timeline.y + 20), (play_x, timeline.y + 29)],
            )
            pygame.draw.line(
                self.screen, (255, 83, 72),
                (play_x, timeline.y + 24), (play_x, track.bottom), 2,
            )
        hint = "Graph Editor • drag points in 2D • right-click: easing/delete • wheel: time zoom"
        self.screen.blit(self.small.render(hint, True, (132, 139, 155)), (track.x, timeline.bottom - 22))

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

        imported = self._active_imported_effect()
        start, end = self._timeline_window()
        visible_ms = end - start
        frame_ruler = self._timeline_uses_frame_ruler(rect, visible_ms)
        frame_ms = self._timeline_frame_ms()
        self.screen.blit(self.small.render("TIMELINE", True, (220, 224, 234)), (rect.x + 8, rect.y + 12))
        self._button(
            pygame.Rect(rect.x + 70, rect.y + 5, 50, 25),
            "toggle_timeline_mode", "DOPE" if self.timeline_mode == "graph" else "GRAPH",
            self.timeline_mode == "graph",
        )
        timecode = f"F{int(self.current_ms // frame_ms):03d}" if frame_ruler else f"{self.current_ms / 1000:05.2f}s"
        self.screen.blit(self.small.render(timecode, True, (108, 180, 255)), (rect.x + 126, rect.y + 12))
        if frame_ruler:
            grid_text = (
                f"FRAME GRID  •  {frame_ms:g} ms  •  {self.timeline_zoom:.1f}x"
                if self._active_imported_effect() else
                f"FRAME GRID  •  {self.project.actual_fps:.2f} FPS  •  {self.timeline_zoom:.1f}x"
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

        if self.timeline_mode == "graph" and not imported:
            self._draw_graph_editor(rect, start, end)
            pygame.draw.rect(self.screen, (29, 32, 40), zoom_rect, border_radius=3)
            self.screen.blit(zoom_label, zoom_label.get_rect(center=zoom_rect.center))
            return

        row_y = track.y + 15
        if imported:
            row = pygame.Rect(rect.x, row_y - 11, rect.width, 31)
            pygame.draw.rect(self.screen, (42, 54, 76), row)
            pygame.draw.rect(self.screen, ACCENT, (row.x, row.y, 3, row.height))
            label = self.small.render(
                (
                    f"ID {imported.effect_id}  /  {imported.name}  /  {len(imported.frames)} stored  /  "
                    f"{imported.frame_ms} ms  /  ×{imported.loops}  /  "
                    f"{'CANVAS' if imported.overlay else 'FULL'}"
                ),
                True, (226, 230, 239),
            )
            self.screen.blit(label, (rect.x + 13, row_y - 3))
            total_steps = imported.duration_ms // imported.frame_ms
            outro_start = imported.normalized_loop_frames * max(1, imported.loops)
            for frame_index in range(total_steps + 1):
                frame_time = frame_index * imported.frame_ms
                if start <= frame_time <= end:
                    x = round(self._time_to_timeline_x(frame_time, rect))
                    color = (102, 216, 164) if frame_index == outro_start and outro_start < total_steps else (255, 184, 70)
                    width = 2 if frame_index == outro_start and outro_start < total_steps else 1
                    pygame.draw.line(self.screen, color, (x, row.y + 3), (x, row.bottom - 3), width)
            row_y += 32
        row_capacity = self._timeline_layer_capacity(rect)
        all_rows = self._timeline_rows()
        row_count = len(all_rows)
        row_start = self.timeline_layer_scroll
        visible_rows = [] if imported else self._timeline_visible_rows(rect)
        for timeline_row, row_y in visible_rows:
            index = timeline_row.layer_index
            layer = self.project.layers[index]
            active_layer = index == self.active_layer
            row_rect = pygame.Rect(rect.x, row_y - 11, rect.width, 31)
            if timeline_row.kind == "layer":
                expanded = layer.id in self.timeline_expanded_layers
                layer_selected = layer.id == self.timeline_selected_layer_id
                layer_on = (
                    bool(layer.value_at("canvas_enabled", self.current_ms))
                    if layer.is_canvas else layer.visible
                )
                if active_layer:
                    pygame.draw.rect(self.screen, (42, 54, 76), row_rect)
                    pygame.draw.rect(self.screen, ACCENT, (row_rect.x, row_rect.y, 3, row_rect.height))
                if layer_selected:
                    pygame.draw.rect(self.screen, (255, 202, 70), row_rect, 1)
                self._button(
                    pygame.Rect(rect.x + 7, row_rect.y + 5, 37, 21),
                    f"toggle_layer:{index}", "ON" if layer_on else "OFF", layer_on,
                )
                self._button(
                    pygame.Rect(rect.x + 48, row_rect.y + 5, 22, 21),
                    f"toggle_timeline_layer:{index}", "v" if expanded else ">", expanded,
                )
                layer_text = f"C {layer.name}" if layer.is_canvas else layer.name
                while len(layer_text) > 5 and self.small.size(layer_text)[0] > 102:
                    layer_text = layer_text[:-4] + "..."
                label = self.small.render(
                    layer_text, True, (226, 230, 239) if active_layer else (166, 172, 187),
                )
                self.screen.blit(label, (rect.x + 76, row_y - 3))
                self.buttons.append((
                    pygame.Rect(rect.x + 73, row_rect.y, 107, row_rect.height),
                    f"select_layer:{index}", layer.name,
                ))
                base_color = (120, 139, 172) if expanded else (
                    (255, 202, 70) if active_layer else (91, 112, 145)
                )
                base_size = 4 if expanded else 7
            else:
                target = self._timeline_row_target(timeline_row)
                if not target or not timeline_row.prop:
                    continue
                target_active = target is self.selected or (
                    isinstance(target, RandomLedEffect)
                    and self.random_led_editor_open and index == self.active_layer
                ) or (
                    isinstance(target, Layer) and target.is_canvas and index == self.active_layer
                )
                if target_active:
                    pygame.draw.rect(self.screen, (35, 43, 58), row_rect)
                    pygame.draw.rect(self.screen, (82, 123, 181), (row_rect.x + 4, row_rect.y, 2, row_rect.height))
                pygame.draw.circle(
                    self.screen,
                    (255, 202, 70) if target_active else (105, 127, 161),
                    (rect.x + 17, row_y), 3,
                )
                property_name = TIMELINE_PROPERTY_LABELS.get(
                    timeline_row.prop, timeline_row.prop.replace("_", " ").title()
                )
                property_text = f"{target.name} · {property_name}"
                while len(property_text) > 7 and self.small.size(property_text)[0] > 145:
                    property_text = property_text[:-4] + "..."
                label = self.small.render(
                    property_text, True,
                    (205, 212, 226) if target_active else (139, 149, 169),
                )
                self.screen.blit(label, (rect.x + 26, row_y - 3))
                self.buttons.append((
                    pygame.Rect(rect.x + 8, row_rect.y, 172, row_rect.height),
                    f"select_timeline_channel:{target.id}:{timeline_row.prop}", property_text,
                ))
                base_color = (255, 202, 70) if target_active else (111, 137, 177)
                base_size = 6

            pygame.draw.line(
                self.screen, (47, 51, 61),
                (track.x, row_rect.bottom), (track.right, row_rect.bottom),
            )
            for time_ms in self._times_for_timeline_row(timeline_row):
                if not start <= time_ms <= end:
                    continue
                marker_x = round(self._time_to_timeline_x(time_ms, rect))
                keys = self._keys_for_timeline_row(timeline_row, time_ms)
                selected_key = bool(keys.intersection(self.selected_keyframes))
                color = (244, 248, 255) if selected_key else base_color
                size = base_size + 2 if selected_key else base_size
                points = [
                    (marker_x, row_y - size), (marker_x + size, row_y),
                    (marker_x, row_y + size), (marker_x - size, row_y),
                ]
                pygame.draw.polygon(self.screen, color, points)
                if selected_key:
                    pygame.draw.polygon(self.screen, ACCENT, points, 2)

        if not imported and row_count > row_capacity:
            gutter = pygame.Rect(track.x - 6, track.y, 3, row_capacity * 32)
            pygame.draw.rect(self.screen, (48, 53, 65), gutter, border_radius=2)
            handle_height = max(24, round(gutter.height * row_capacity / row_count))
            maximum_scroll = row_count - row_capacity
            travel = gutter.height - handle_height
            handle_y = gutter.y + round(travel * self.timeline_layer_scroll / max(1, maximum_scroll))
            pygame.draw.rect(
                self.screen, (104, 126, 164),
                pygame.Rect(gutter.x, handle_y, gutter.width, handle_height), border_radius=2,
            )

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
        if not imported and row_count > row_capacity:
            row_end = min(row_count, row_start + row_capacity)
            range_label = f"Rows {row_start + 1}–{row_end}/{row_count}  •  wheel here"
            self.screen.blit(self.small.render(range_label, True, (116, 128, 151)), (rect.x + 7, rect.bottom - 22))
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
        if self.random_led_editor_open:
            if self._active_random_led_effect():
                self._draw_random_led_panel(panel, x)
                return
            self.random_led_editor_open = False
        if self.gradient_editor_open:
            if self.selected and self.selected.state_at(self.current_ms).get("fill_mode") == "fill":
                self._draw_gradient_panel(panel, x)
                return
            self.gradient_editor_open = False

        y = panel.y + 76
        imported = self._active_imported_effect()
        self.screen.blit(self.font.render("Firmware V4", True, (218, 222, 232)), (x, y))
        y += 27
        if imported:
            summary = f"ID {imported.effect_id}  •  {imported.frame_ms} ms  •  {imported.actual_fps:.2f} FPS  •  ×{imported.loops}"
            self.screen.blit(self.small.render(summary, True, (118, 184, 255)), (x, y)); y += 22
            mode = "CANVAS" if imported.overlay else "FULL"
            loop_text = f"Loop {imported.normalized_loop_frames}/{len(imported.frames)}  •  {imported.flash_bytes} B  •  {mode}"
            self.screen.blit(self.small.render(loop_text, True, (154, 162, 180)), (x, y)); y += 31
        else:
            summary = f"ID {self.project.effect_id}  •  {self.project.frame_ms} ms  •  {self.project.actual_fps:.2f} FPS"
            self.screen.blit(self.small.render(summary, True, (118, 184, 255)), (x, y)); y += 21
            self._button(pygame.Rect(x, y, 61, 27), "effect_id:-1", "ID −", False)
            self._button(pygame.Rect(x + 68, y, 61, 27), "effect_id:1", "ID +", False)
            self._button(pygame.Rect(x + 136, y, 139, 27), "cycle_fps", "Cycle FPS", False)
            y += 34
            self._button(pygame.Rect(x, y, 61, 27), "loops:-1", "Loop −", False)
            self._button(pygame.Rect(x + 68, y, 61, 27), "loops:1", "Loop +", False)
            self._button(pygame.Rect(x + 136, y, 82, 27), "set_loop_end", "Set end", False)
            self._button(pygame.Rect(x + 225, y, 50, 27), "clear_loop_end", "Full", False)
            y += 32
            loop_frames = self.project.normalized_loop_frames
            stats = (
                f"×{self.project.loops} • {loop_frames}/{self.project.stored_frame_count} • "
                f"{self.project.flash_bytes} B • {self.project.firmware_playback_ms / 1000:.2f}s"
            )
            self.screen.blit(self.small.render(stats, True, (154, 162, 180)), (x, y))
            self._button(
                pygame.Rect(x + 203, y - 4, 72, 25), "toggle_overlay",
                "CANVAS" if self.project.overlay else "FULL", self.project.overlay,
            )
            y += 24

        pygame.draw.line(self.screen, (58, 62, 74), (panel.x, y), (panel.right, y))
        y += 12
        self.screen.blit(self.font.render("Layers", True, (218, 222, 232)), (x, y))
        self._button(pygame.Rect(panel.right - 190, y - 3, 34, 25), "canvas_layer", "C+", False)
        self._button(pygame.Rect(panel.right - 150, y - 3, 34, 25), "random_led_editor", "FX", False)
        self._button(pygame.Rect(panel.right - 110, y - 3, 28, 25), "duplicate_layer", "D", False)
        self._button(pygame.Rect(panel.right - 76, y - 3, 28, 25), "delete_layer", "−", False)
        self._button(pygame.Rect(panel.right - 42, y - 3, 28, 25), "layer", "+", False)
        y += 24
        for index, layer in enumerate(self.project.layers[:4]):
            row = pygame.Rect(x, y, panel.width - 28, 27)
            if index == self.active_layer:
                pygame.draw.rect(self.screen, (47, 61, 85), row, border_radius=3)
                pygame.draw.rect(self.screen, ACCENT, (row.x, row.y, 3, row.height))
            eye = pygame.Rect(row.x + 5, row.y + 3, 24, 21)
            layer_on = (
                bool(layer.value_at("canvas_enabled", self.current_ms))
                if layer.is_canvas else layer.visible
            )
            self._button(eye, f"toggle_layer:{index}", "●" if layer_on else "○", False)
            prefix = "C" if layer.is_canvas else "≡"
            name_color = (105, 218, 224) if layer.is_canvas else (228, 232, 241)
            name = self.small.render(f"{prefix}   {layer.name}", True, name_color)
            self.screen.blit(name, (row.x + 38, row.y + 6))
            self.buttons.append((pygame.Rect(row.x + 34, row.y, row.width - 34, row.height), f"drag_layer:{index}", layer.name))
            y += 31

        y += 10
        pygame.draw.line(self.screen, (58, 62, 74), (panel.x, y), (panel.right, y))
        y += 13
        active_panel_layer = self.project.layers[self.active_layer]
        section_title = "Canvas control" if active_panel_layer.is_canvas else "Transform"
        self.screen.blit(self.font.render(section_title, True, (218, 222, 232)), (x, y))
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
                ("rotation", "ROT", state["rotation"]),
                ("rotation_turns", "TURNS", state["rotation_turns"]),
                ("opacity", "OPACITY", state["opacity"]),
                ("feather", "FEATHER", state["feather"]),
                ("mask_expansion", "EXPAND", state["mask_expansion"]),
            ]
            for index, (prop, label, value) in enumerate(properties):
                col = index % 2
                row = index // 2
                field = pygame.Rect(x + col * 143, y + row * 37, 132, 30)
                hovered = field.collidepoint(pygame.mouse.get_pos())
                editing = self.property_editing == prop
                pygame.draw.rect(
                    self.screen, (49, 57, 72) if editing or hovered else (40, 44, 54),
                    field, border_radius=4,
                )
                pygame.draw.rect(
                    self.screen, ACCENT if editing or hovered else (67, 72, 86),
                    field, 1, border_radius=4,
                )
                if editing:
                    value_text = self.property_input
                    if (pygame.time.get_ticks() // 500) % 2 == 0:
                        value_text += "|"
                else:
                    value_text = (
                        f"{value:.1f}°" if prop == "rotation"
                        else f"×{value:.0f}" if prop == "rotation_turns"
                        else f"{value * 100:.1f}%" if prop in {"feather", "mask_expansion"}
                        else f"{value:.3f}"
                    )
                self.screen.blit(self.small.render(label, True, (130, 138, 157)), (field.x + 7, field.y + 7))
                rendered = self.small.render(value_text, True, (231, 234, 242))
                self.screen.blit(rendered, (field.right - rendered.get_width() - 7, field.y + 7))
                self.buttons.append((field, f"scrub:{prop}", label))
            y += 193
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
            if state.get("fill_mode", "fill") == "stroke":
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
            else:
                gradient_type = state.get("gradient_type", "solid").title()
                self._button(
                    pygame.Rect(x, y, 275, 30), "gradient_editor",
                    f"Gradient fill…  {gradient_type}", gradient_type != "Solid",
                )
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
        elif active_panel_layer.is_canvas:
            enabled = bool(active_panel_layer.value_at("canvas_enabled", self.current_ms))
            state_label = "ENABLED · empty LEDs transparent" if enabled else "DISABLED · empty LEDs black"
            state_color = (105, 218, 174) if enabled else (255, 171, 83)
            self.screen.blit(self.small.render(state_label, True, state_color), (x, y))
            y += 30
            self._button(
                pygame.Rect(x, y, 132, 32), "canvas_state:1", "Enable key", enabled,
            )
            self._button(
                pygame.Rect(x + 143, y, 132, 32), "canvas_state:0", "Disable key", not enabled,
            )
            y += 45
            has_key = any(
                frame.time_ms == self.current_ms
                for frame in active_panel_layer.keyframes.get("canvas_enabled", [])
            )
            key_status = "Keyframe exists at playhead" if has_key else "No Canvas keyframe at playhead"
            self.screen.blit(self.small.render(key_status, True, (151, 161, 180)), (x, y))
            y += 26
            tips = (
                "Enable: untouched LEDs use FF00FF / transparent.",
                "Disable: untouched LEDs export opaque black.",
                "The timeline ON/OFF button and V toggle the state.",
            )
            for tip in tips:
                self.screen.blit(self.small.render(tip, True, (126, 136, 156)), (x, y))
                y += 22
        else:
            self.screen.blit(self.small.render("Select an object on the canvas.", True, (145, 152, 169)), (x, y))
            y += 28
            self.screen.blit(self.small.render("Drag a shape tool onto the playfield.", True, (116, 124, 142)), (x, y))

        pygame.draw.rect(self.screen, (25, 28, 35), (panel.x, panel.bottom - 47, panel.width, 47))
        self.screen.blit(self.small.render(self.status[:42], True, (100, 216, 162)), (x, panel.bottom - 29))

    def _draw_random_led_panel(self, panel: pygame.Rect, x: int) -> None:
        effect = self._active_random_led_effect()
        assert effect is not None
        layer = self.project.layers[self.active_layer]
        enabled = bool(effect.value_at("enabled", self.current_ms))

        y = panel.y + 78
        self.screen.blit(self.font.render("RANDOM LED / SPARKLE", True, (225, 229, 238)), (x, y))
        self._button(pygame.Rect(panel.right - 82, y - 4, 68, 28), "random_led_back", "< Back", False)
        y += 35
        self.screen.blit(self.small.render(f"Layer: {layer.name}", True, (118, 184, 255)), (x, y)); y += 28
        self.screen.blit(self.small.render("Directly flashes random firmware LEDs.", True, (154, 162, 180)), (x, y)); y += 20
        self.screen.blit(self.small.render("Layer shapes do not mask this effect.", True, (154, 162, 180)), (x, y)); y += 31

        self.screen.blit(self.small.render("Enabled", True, (172, 178, 193)), (x, y))
        state_color = (96, 220, 155) if enabled else (255, 112, 102)
        state = "ON" if enabled else "OFF"
        self.screen.blit(self.font.render(state, True, state_color), (x + 72, y - 3))
        self._button(pygame.Rect(x + 132, y - 6, 68, 30), "random_led_toggle", "Toggle", enabled)
        self._button(pygame.Rect(x + 207, y - 6, 68, 30), "random_led_keyframe", "State key", False)
        y += 42

        def parameter_row(prop: str, label: str, value: float | int) -> None:
            nonlocal y
            row = pygame.Rect(x, y, 275, 34)
            hovered = row.collidepoint(pygame.mouse.get_pos())
            editing = self.random_effect_editing == prop
            pygame.draw.rect(
                self.screen, (49, 57, 72) if editing or hovered else (39, 43, 53),
                row, border_radius=4,
            )
            pygame.draw.rect(
                self.screen, ACCENT if editing or hovered else (67, 72, 86),
                row, 1, border_radius=4,
            )
            self.screen.blit(self.small.render(label, True, (151, 158, 176)), (row.x + 8, row.y + 9))
            if editing:
                value_text = self.random_effect_input
                if (pygame.time.get_ticks() // 500) % 2 == 0:
                    value_text += "|"
            elif prop == "life_ms":
                value_text = f"{int(value)} ms"
            elif prop == "born_speed":
                value_text = f"{float(value):g} / sec"
            elif prop == "opacity":
                value_text = f"{float(value) * 100:.1f}%"
            else:
                value_text = str(int(value))
            has_key = prop == "opacity" and any(
                frame.time_ms == self.current_ms
                for frame in effect.keyframes.get("opacity", [])
            )
            value_right = row.right - (45 if prop == "opacity" else 9)
            rendered = self.small.render(value_text, True, (235, 238, 245))
            self.screen.blit(rendered, (value_right - rendered.get_width(), row.y + 9))
            field = pygame.Rect(row) if prop != "opacity" else pygame.Rect(
                row.x, row.y, row.width - 39, row.height,
            )
            self.buttons.append((field, f"random_led_field:{prop}", f"{label} — drag or click to type"))
            if prop == "opacity":
                self._button(
                    pygame.Rect(row.right - 35, row.y + 3, 31, 28),
                    "random_led_opacity_keyframe", "+K", has_key,
                )
            y += 42

        parameter_row("seed", "Random seed", effect.seed)
        parameter_row("life_ms", "Life", effect.life_ms)
        parameter_row("born_speed", "Born speed", effect.born_speed)
        parameter_row("particle_count", "Max active", effect.particle_count)
        parameter_row("opacity", "Opacity", float(effect.value_at("opacity", self.current_ms)))

        y += 5
        self.screen.blit(self.small.render("Flash color", True, (172, 178, 193)), (x, y)); y += 25
        for index, color in enumerate(PALETTE):
            rect = pygame.Rect(x + index * 34, y, 27, 27)
            pygame.draw.rect(self.screen, color, rect, border_radius=4)
            if effect.color == color:
                pygame.draw.rect(self.screen, (245, 247, 252), rect.inflate(4, 4), 2, border_radius=5)
            self.buttons.append((rect, f"color:{index}", "Random LED color"))

        y += 48
        enabled_keys = len(effect.keyframes.get("enabled", []))
        opacity_keys = len(effect.keyframes.get("opacity", []))
        self.screen.blit(
            self.small.render(f"Keyframes: enabled {enabled_keys} · opacity {opacity_keys}", True, (145, 153, 171)),
            (x, y),
        )
        y += 20
        self.screen.blit(
            self.small.render("Drag fields horizontally · click to type", True, (125, 135, 154)),
            (x, y),
        )
        y += 29
        self._button(pygame.Rect(x, y, 275, 30), "random_led_delete", "Remove Random LED effect", False)

        pygame.draw.rect(self.screen, (25, 28, 35), (panel.x, panel.bottom - 47, panel.width, 47))
        self.screen.blit(self.small.render(self.status[:42], True, (100, 216, 162)), (x, panel.bottom - 29))

    def _draw_gradient_panel(self, panel: pygame.Rect, x: int) -> None:
        assert self.selected is not None
        state = self.selected.state_at(self.current_ms)
        if len(self.selected.gradient_stops) < 2:
            return
        if not self._selected_gradient_stop():
            self.selected_gradient_stop_id = self.selected.gradient_stops[0].id
        selected_stop = self._selected_gradient_stop()

        y = panel.y + 78
        self.screen.blit(self.font.render("GRADIENT FILL", True, (225, 229, 238)), (x, y))
        self._button(pygame.Rect(panel.right - 82, y - 4, 68, 28), "gradient_back", "< Back", False)
        y += 34
        self.screen.blit(self.small.render(self.selected.name, True, (118, 184, 255)), (x, y))
        y += 31
        self.screen.blit(self.small.render("Type", True, (172, 178, 193)), (x, y)); y += 22
        gradient_type = state.get("gradient_type", "solid")
        radial_mode = state.get("gradient_radial_mode", "radius")
        for index, (value, label) in enumerate((("solid", "Solid"), ("linear", "Linear"), ("radial", "Radial"))):
            self._button(
                pygame.Rect(x + index * 93, y, 86, 30), f"gradient_type:{value}", label,
                gradient_type == value,
            )

        radial_label_color = (172, 178, 193) if gradient_type == "radial" else (102, 108, 122)
        self.screen.blit(self.small.render("Radial direction", True, radial_label_color), (x, y + 40))
        for index, (value, label) in enumerate((("radius", "Radius"), ("angular", "Angular"))):
            rect = pygame.Rect(x + index * 143, y + 59, 132, 30)
            if gradient_type == "radial":
                self._button(rect, f"gradient_radial_mode:{value}", label, radial_mode == value)
            else:
                pygame.draw.rect(self.screen, (35, 38, 47), rect, border_radius=5)
                pygame.draw.rect(self.screen, (60, 65, 78), rect, 1, border_radius=5)
                text = self.small.render(label, True, (91, 97, 112))
                self.screen.blit(text, text.get_rect(center=rect.center))

        bar = self._gradient_bar_rect()
        self.screen.blit(self.small.render("Color stops", True, (172, 178, 193)), (x, bar.y - 25))
        for pixel_x in range(bar.width):
            amount = pixel_x / max(1, bar.width - 1)
            pygame.draw.line(
                self.screen, sample_gradient(self.selected.gradient_stops, amount),
                (bar.x + pixel_x, bar.y), (bar.x + pixel_x, bar.bottom - 1),
            )
        pygame.draw.rect(self.screen, (220, 224, 234), bar, 1, border_radius=2)
        self.buttons.append((bar, "gradient_bar", "Add gradient stop"))

        for stop in sorted(self.selected.gradient_stops, key=lambda item: item.position):
            stop_x = round(bar.x + stop.position * bar.width)
            center_y = bar.bottom + 11
            points = [(stop_x, bar.bottom + 2), (stop_x - 7, center_y), (stop_x, center_y + 8), (stop_x + 7, center_y)]
            pygame.draw.polygon(self.screen, stop.color, points)
            selected = stop.id == self.selected_gradient_stop_id
            pygame.draw.polygon(self.screen, ACCENT if selected else (226, 230, 239), points, 2 if selected else 1)
            handle = pygame.Rect(0, 0, 22, 28)
            handle.center = (stop_x, center_y)
            self.buttons.append((handle, f"gradient_stop:{stop.id}", "Gradient stop"))

        info_y = bar.bottom + 34
        if selected_stop:
            info = f"Selected stop  {selected_stop.position * 100:5.1f}%   RGB {selected_stop.color}"
            self.screen.blit(self.small.render(info, True, (214, 219, 230)), (x, info_y))
        button_y = info_y + 27
        self._button(pygame.Rect(x, button_y, 132, 30), "gradient_add_stop", "+ Add stop", False)
        self._button(pygame.Rect(x + 143, button_y, 132, 30), "gradient_delete_stop", "− Delete stop", False)

        angle = float(state.get("gradient_angle", 0.0)) % 360.0
        angle_y = button_y + 49
        angle_enabled = gradient_type == "linear" or (
            gradient_type == "radial" and radial_mode == "angular"
        )
        angle_name = "Phase" if gradient_type == "radial" and radial_mode == "angular" else "Angle"
        angle_color = (172, 178, 193) if angle_enabled else (102, 108, 122)
        self.screen.blit(self.small.render(f"{angle_name}  {angle:.1f}°", True, angle_color), (x, angle_y))
        slider = self._gradient_angle_rect()
        pygame.draw.line(self.screen, (92, 101, 120), slider.midleft, slider.midright, 4)
        handle_x = round(slider.x + angle / 360.0 * slider.width)
        pygame.draw.circle(self.screen, ACCENT if angle_enabled else (75, 81, 96), (handle_x, slider.centery), 8)
        if angle_enabled:
            self.buttons.append((slider.inflate(0, 18), "gradient_angle", f"Gradient {angle_name.lower()}"))
        else:
            note = (
                "Phase is available in Angular mode"
                if gradient_type == "radial" else
                "Angle is available in Linear/Angular mode"
            )
            self.screen.blit(self.small.render(note, True, (105, 112, 129)), (x, slider.bottom + 8))

        palette_y = slider.bottom + 44
        self.screen.blit(self.small.render("Selected stop color", True, (172, 178, 193)), (x, palette_y))
        palette_y += 25
        for index, color in enumerate(PALETTE):
            rect = pygame.Rect(x + index * 34, palette_y, 27, 27)
            pygame.draw.rect(self.screen, color, rect, border_radius=4)
            if selected_stop and selected_stop.color == color:
                pygame.draw.rect(self.screen, (245, 247, 252), rect.inflate(4, 4), 2, border_radius=5)
            self.buttons.append((rect, f"color:{index}", "Gradient stop color"))

        tips_y = palette_y + 55
        tips = [
            "Click the color bar to add a stop.",
            "Drag any stop freely along the bar.",
            "Select a palette color for the active stop.",
            "Esc closes this gradient editor.",
        ]
        for tip in tips:
            self.screen.blit(self.small.render(tip, True, (148, 155, 173)), (x, tips_y)); tips_y += 21

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

    def _export_bank_panel_rect(self) -> pygame.Rect:
        width = min(1120, self.screen.get_width() - 50)
        height = min(720, self.screen.get_height() - 50)
        panel = pygame.Rect(0, 0, width, height)
        panel.center = self.screen.get_rect().center
        return panel

    def _handle_help_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.help_open = False
            self.status = "Hotkey reference closed"
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        close = next(
            (
                rect for rect, action, _label in reversed(self.buttons)
                if action == "help_close"
            ),
            None,
        )
        if close and close.collidepoint(event.pos):
            self.help_open = False
            self.status = "Hotkey reference closed"

    def _help_panel_rect(self) -> pygame.Rect:
        width = min(980, self.screen.get_width() - 60)
        height = min(700, self.screen.get_height() - 60)
        panel = pygame.Rect(0, 0, width, height)
        panel.center = self.screen.get_rect().center
        return panel

    def _draw_help(self) -> None:
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((7, 9, 13, 222))
        self.screen.blit(shade, (0, 0))
        panel = self._help_panel_rect()
        pygame.draw.rect(self.screen, (25, 28, 36), panel, border_radius=10)
        pygame.draw.rect(self.screen, (77, 84, 102), panel, 1, border_radius=10)
        x, y = panel.x + 28, panel.y + 22
        self.screen.blit(self.title.render("HOTKEYS / HELP", True, (238, 241, 248)), (x, y))
        self.screen.blit(
            self.small.render(
                "Keyboard and mouse reference · F1 opens this window · Esc closes it",
                True, (143, 153, 174),
            ),
            (x, y + 32),
        )
        self._button(pygame.Rect(panel.right - 76, y, 50, 32), "help_close", "×", False)

        content_y = panel.y + 84
        gap = 34
        column_width = (panel.width - 56 - gap) // 2
        for column_index, sections in enumerate(HELP_COLUMNS):
            column_x = panel.x + 28 + column_index * (column_width + gap)
            cursor_y = content_y
            for section_title, rows in sections:
                self.screen.blit(
                    self.font.render(section_title, True, (113, 184, 255)),
                    (column_x, cursor_y),
                )
                cursor_y += 29
                for key, description in rows:
                    badge = pygame.Rect(column_x, cursor_y, 132, 22)
                    pygame.draw.rect(self.screen, (42, 48, 61), badge, border_radius=4)
                    pygame.draw.rect(self.screen, (74, 83, 103), badge, 1, border_radius=4)
                    key_surface = self.small.render(key, True, (239, 242, 248))
                    self.screen.blit(key_surface, key_surface.get_rect(center=badge.center))
                    description_surface = self.small.render(
                        self._fit_text(description, self.small, column_width - 146),
                        True, (183, 190, 207),
                    )
                    self.screen.blit(description_surface, (badge.right + 12, cursor_y + 4))
                    cursor_y += 26
                cursor_y += 14

        footer = "Tip: hover buttons for labels; timeline markers remain visible on inactive layers."
        footer_surface = self.small.render(footer, True, (112, 123, 145))
        self.screen.blit(footer_surface, (x, panel.bottom - 31))

    def _draw_export_bank(self) -> None:
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((7, 9, 13, 218))
        self.screen.blit(shade, (0, 0))
        panel = self._export_bank_panel_rect()
        pygame.draw.rect(self.screen, (25, 28, 36), panel, border_radius=10)
        pygame.draw.rect(self.screen, (77, 84, 102), panel, 1, border_radius=10)
        x, y = panel.x + 26, panel.y + 21
        self.screen.blit(self.title.render("EFFECT BANK / EXPORT", True, (238, 241, 248)), (x, y))
        subtitle = self.small.render(
            "V4 baked RGB memory planner · 150 KiB flash · embedded editable projects",
            True, (143, 153, 174),
        )
        self.screen.blit(subtitle, (x, y + 31))
        self._button(pygame.Rect(panel.right - 344, y, 126, 32), "export_bank_map", "Map header…", False)
        errors = self._export_bank_validation_errors()
        self._button(
            pygame.Rect(panel.right - 210, y, 126, 32), "export_bank_write",
            "Export bank…", not errors,
        )
        self._button(pygame.Rect(panel.right - 76, y, 50, 32), "export_bank_close", "×", False)

        used = self._export_bank_used_bytes()
        free = max(0, EFFECT_BANK_CAPACITY - used)
        frame_ms = max(1, int(self.project.frame_ms or 50))
        free_seconds = (free // 204) * frame_ms / 1000.0
        capacity_y = panel.y + 83
        summary = (
            f"{used / 1024:.1f} KiB used  /  {EFFECT_BANK_CAPACITY / 1024:.0f} KiB   ·   "
            f"{free / 1024:.1f} KiB free   ·   ≈{free_seconds:.1f}s at {1000 / frame_ms:.2f} FPS"
        )
        summary_color = (255, 104, 92) if used > EFFECT_BANK_CAPACITY else (104, 221, 165)
        self.screen.blit(self.font.render(summary, True, summary_color), (x, capacity_y))
        memory = pygame.Rect(x, capacity_y + 31, panel.width - 52, 42)
        pygame.draw.rect(self.screen, (40, 44, 54), memory, border_radius=6)
        pygame.draw.rect(
            self.screen,
            (255, 104, 92) if used > EFFECT_BANK_CAPACITY else (87, 94, 112),
            memory, 2 if used > EFFECT_BANK_CAPACITY else 1, border_radius=6,
        )
        entries = [
            (-1, self.project.name, self.project.flash_bytes),
            *[(index, effect.name, effect.flash_bytes) for index, effect in enumerate(self.export_bank_effects)],
        ]
        old_clip = self.screen.get_clip()
        self.screen.set_clip(memory.inflate(-2, -2))
        cursor = memory.x + 2
        usable_width = memory.width - 4
        for order, (index, name, byte_count) in enumerate(entries):
            width = max(3, round(byte_count / EFFECT_BANK_CAPACITY * usable_width))
            block = pygame.Rect(cursor, memory.y + 2, width, memory.height - 4)
            color = BANK_COLORS[order % len(BANK_COLORS)]
            pygame.draw.rect(self.screen, color, block)
            if index == self.export_bank_selected:
                pygame.draw.rect(self.screen, (255, 255, 255), block, 2)
            self.buttons.append((block, f"export_bank_select:{index}", name))
            cursor += width
        self.screen.set_clip(old_clip)

        mapped = str(self.export_bank_path) if self.export_bank_path else "No effect_data.h mapped — export defaults to exports/effect_data.h"
        self.screen.blit(
            self.small.render(self._fit_text(mapped, self.small, panel.width - 52), True, (129, 138, 158)),
            (x, memory.bottom + 12),
        )

        list_rect = pygame.Rect(x, panel.y + 224, panel.width - 398, panel.height - 294)
        detail = pygame.Rect(list_rect.right + 18, list_rect.y, panel.right - list_rect.right - 44, list_rect.height)
        self.screen.blit(self.font.render("BANK CONTENT", True, (211, 216, 228)), (list_rect.x, list_rect.y - 29))
        self.screen.blit(self.font.render("SELECTED EFFECT", True, (211, 216, 228)), (detail.x, detail.y - 29))
        pygame.draw.rect(self.screen, (20, 23, 30), list_rect, border_radius=6)
        pygame.draw.rect(self.screen, (20, 23, 30), detail, border_radius=6)
        pygame.draw.rect(self.screen, (57, 63, 77), list_rect, 1, border_radius=6)
        pygame.draw.rect(self.screen, (57, 63, 77), detail, 1, border_radius=6)

        visible = max(1, list_rect.height // 53)
        maximum_scroll = max(0, len(entries) - visible)
        self.export_bank_scroll = min(self.export_bank_scroll, maximum_scroll)
        for visible_index, (entry_index, name, byte_count) in enumerate(
            entries[self.export_bank_scroll:self.export_bank_scroll + visible]
        ):
            absolute_order = self.export_bank_scroll + visible_index
            row = pygame.Rect(list_rect.x + 7, list_rect.y + 7 + visible_index * 53, list_rect.width - 14, 46)
            selected = entry_index == self.export_bank_selected
            pygame.draw.rect(self.screen, (47, 58, 78) if selected else (31, 35, 44), row, border_radius=5)
            color = BANK_COLORS[absolute_order % len(BANK_COLORS)]
            bar_width = max(3, round(byte_count / EFFECT_BANK_CAPACITY * (row.width - 8)))
            pygame.draw.rect(self.screen, color, (row.x + 4, row.bottom - 7, min(row.width - 8, bar_width), 3), border_radius=2)
            if entry_index == -1:
                effect_id, frames, timing = self.project.effect_id, self.project.stored_frame_count, int(self.project.frame_ms or 1)
                marker = "CURRENT"
            else:
                effect = self.export_bank_effects[entry_index]
                effect_id, frames, timing = effect.effect_id, len(effect.frames), effect.frame_ms
                marker = "BANK+PROJECT" if effect.project_data is not None else "BANK"
            label = self._fit_text(f"{marker}  ·  ID {effect_id}  ·  {name}", self.small, row.width - 178)
            self.screen.blit(self.small.render(label, True, (232, 235, 242)), (row.x + 9, row.y + 7))
            stats = f"{byte_count / 1024:.1f}K  {frames * timing / 1000:.2f}s"
            stats_surface = self.small.render(stats, True, (159, 168, 187))
            self.screen.blit(stats_surface, (row.right - stats_surface.get_width() - 9, row.y + 7))
            self.buttons.append((row, f"export_bank_select:{entry_index}", name))

        self._draw_export_bank_details(detail)
        status_color = (255, 110, 98) if errors else (102, 218, 163)
        status = " · ".join(errors) if errors else self.export_bank_status
        self.screen.blit(
            self.small.render(self._fit_text(status, self.small, panel.width - 52), True, status_color),
            (x, panel.bottom - 37),
        )

    def _draw_export_bank_details(self, detail: pygame.Rect) -> None:
        effect = self._export_bank_selected_effect()
        current = effect is None
        name = self.project.name if current else effect.name
        effect_id = self.project.effect_id if current else effect.effect_id
        frames = self.project.stored_frame_count if current else len(effect.frames)
        frame_ms = int(self.project.frame_ms or 1) if current else effect.frame_ms
        loops = self.project.loops if current else effect.loops
        loop_frames = self.project.normalized_loop_frames if current else effect.normalized_loop_frames
        overlay = self.project.overlay if current else effect.overlay
        byte_count = self.project.flash_bytes if current else effect.flash_bytes
        x, y = detail.x + 14, detail.y + 16
        badge = "CURRENT PROJECT" if current else "MAPPED HEADER EFFECT"
        self.screen.blit(self.small.render(badge, True, BANK_COLORS[0 if current else (self.export_bank_selected + 1) % len(BANK_COLORS)]), (x, y))
        y += 35
        self.screen.blit(self.small.render("Name", True, (143, 152, 171)), (x, y))
        name_field = pygame.Rect(x, y + 18, detail.width - 28, 34)
        pygame.draw.rect(self.screen, (42, 47, 58), name_field, border_radius=4)
        pygame.draw.rect(self.screen, ACCENT if self.export_bank_name_editing else (74, 81, 98), name_field, 1, border_radius=4)
        name_value = self.export_bank_name_input if self.export_bank_name_editing else name
        if self.export_bank_name_editing and (pygame.time.get_ticks() // 500) % 2 == 0:
            name_value += "|"
        self.screen.blit(self.small.render(self._fit_text(name_value, self.small, name_field.width - 16), True, (238, 241, 247)), (name_field.x + 8, name_field.y + 9))
        self.buttons.append((name_field, "export_bank_edit_name", "Effect name"))
        y += 67
        self.screen.blit(self.small.render("Firmware ID", True, (143, 152, 171)), (x, y))
        id_field = pygame.Rect(x, y + 18, 104, 34)
        pygame.draw.rect(self.screen, (42, 47, 58), id_field, border_radius=4)
        pygame.draw.rect(self.screen, ACCENT if self.export_bank_id_editing else (74, 81, 98), id_field, 1, border_radius=4)
        id_value = self.export_bank_id_input if self.export_bank_id_editing else str(effect_id)
        if self.export_bank_id_editing and (pygame.time.get_ticks() // 500) % 2 == 0:
            id_value += "|"
        self.screen.blit(self.font.render(id_value, True, (238, 241, 247)), (id_field.x + 10, id_field.y + 6))
        self.buttons.append((id_field, "export_bank_edit_id", "Firmware effect ID"))
        y += 75
        metadata = [
            ("Stored frames", f"{frames}"),
            ("Frame time", f"{frame_ms} ms  /  {1000 / frame_ms:.2f} FPS"),
            ("Stored duration", f"{frames * frame_ms / 1000:.2f} s"),
            ("Flash", f"{byte_count / 1024:.2f} KiB"),
            ("Loop / outro", f"{loop_frames} loop frames  ×{loops}"),
            ("Canvas mode", "OVERLAY" if overlay else "FULL"),
            ("Editable project", "Embedded" if current or effect.project_data is not None else "Not available"),
        ]
        for label, value in metadata:
            self.screen.blit(self.small.render(label, True, (132, 141, 160)), (x, y))
            rendered = self.small.render(value, True, (220, 225, 235))
            self.screen.blit(rendered, (detail.right - rendered.get_width() - 14, y))
            y += 20
        if not current:
            if effect.project_data is not None:
                self._button(
                    pygame.Rect(x, detail.bottom - 86, detail.width - 28, 32),
                    "export_bank_load_project", "Load editable project", True,
                )
            else:
                self.screen.blit(
                    self.small.render("No project bundle in this header effect", True, (132, 141, 160)),
                    (x, detail.bottom - 76),
                )
            self._button(
                pygame.Rect(x, detail.bottom - 48, detail.width - 28, 32),
                "export_bank_remove", "Remove from export bank", False,
            )

    @staticmethod
    def _fit_text(value: str, font: pygame.font.Font, max_width: int) -> str:
        if font.size(value)[0] <= max_width:
            return value
        suffix = "…"
        while value and font.size(value + suffix)[0] > max_width:
            value = value[:-1]
        return value + suffix

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
    if WINDOW_ICON.is_file():
        pygame.display.set_icon(pygame.image.load(WINDOW_ICON))
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
