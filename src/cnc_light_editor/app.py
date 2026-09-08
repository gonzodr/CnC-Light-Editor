from __future__ import annotations
import re

import argparse
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
import math
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import shutil
import subprocess
import sys
import threading
import traceback
from uuid import uuid4

import pygame

from .autosave import AutosaveManager, RecoveryRecord
from .bridge import complete as complete_bridge_request, read_requests as read_bridge_requests
from .engine import noise_color, point_inside, render_leds, sample_gradient
from .effect_bank import (
    SORT_MODES,
    describe_changes,
    effect_matches,
    resolve_effects,
    sort_effects,
    total_playback_ms,
)
from .effect_importer import ImportedEffect, load_effect_data
from .exporter import (
    EFFECT_BANK_CAPACITY,
    TRANSPARENT_SENTINEL,
    export_effect_bank,
    project_to_imported_effect,
    sample_project_frames,
)
from .ledmap import Led, LedMap
from .model import (
    ColorCycleEffect,
    CometEffect,
    GradientStop,
    Keyframe,
    Layer,
    Project,
    PulseEffect,
    RandomLedEffect,
    Shape,
    StrobeEffect,
)
from .performance import is_raspberry_pi, recommended_preview_fps
from .property_widgets import (
    COLOR_CYCLE_PROPERTY_SPECS,
    COMET_PROPERTY_SPECS,
    LAYER_PROPERTY_SPECS,
    NumericPropertySpec,
    PULSE_PROPERTY_SPECS,
    RANDOM_LED_PROPERTY_SPECS,
    SHAPE_PROPERTY_SPECS,
    STROBE_PROPERTY_SPECS,
)
from .ui_settings import UiSettingsStore
from .updater import GitUpdater, UpdateEvent
from .version import APP_VERSION

ROOT = Path(__file__).resolve().parents[2]
PROJECT_FILE = ROOT / "projects" / "current.cnclight"
EXPORT_FILE = ROOT / "exports" / "effect_data.h"
AUTOSAVE_DIR = ROOT / "projects" / ".autosave"
WINDOW_ICON = ROOT / "assets" / "CnC_LightE_ico.png"
PALETTE = [
    (255, 70, 40), (255, 155, 20), (255, 230, 50), (80, 220, 90),
    (30, 180, 255), (90, 90, 255), (210, 80, 255), (255, 255, 255),
    (255, 20, 110), (170, 255, 60), (30, 220, 190), (140, 90, 255),
    (255, 120, 190), (255, 210, 130), (140, 140, 150), (0, 0, 0),
]
CUSTOM_COLOR_CAP = 7  # + the "+" swatch fills out an 8-wide row, matching PALETTE's rows
BANK_COLORS = [
    (74, 151, 255), (118, 92, 246), (224, 82, 151), (255, 126, 64),
    (244, 190, 55), (74, 198, 126), (48, 188, 202), (150, 104, 218),
]
ACTION_TOOLTIPS = {
    "play": "Space · Play, looping the whole sequence",
    "play_loop": "Shift+Space · Play, looping only the loop section",
    "keyframe": "K · Add a keyframe at the playhead",
    "stencil": "Preview the animation through the playfield artwork",
    "calibration": "Open LED Map calibration and firmware ID editing",
    "save": "Ctrl+S · Save the current project",
    "load": "Ctrl+O · Load a project",
    "export": "Open the Effect Bank and firmware exporter",
    "new": "Ctrl+N · Start a new project",
    "help": "F1 · Open shortcuts and help",
    "exit": "Exit the editor safely",
    "snap": "Snap timeline edits and the playhead to exact frame boundaries",
    "import_effect_data": "Import and preview effects from effect_data.h",
    "canvas_layer": "Add the keyframeable Canvas transparency layer",
    "falloff_layer": "Add a keyframeable temporal fade-out layer",
    "generator_menu_toggle": "Add or edit a firmware-order layer effect (Random LED, Strobe, Color Cycle, Pulse, Comet)",
    "duplicate_layer": "Ctrl+D · Duplicate the active layer",
    "delete_layer": "Delete the active layer",
    "layer": "Add a new shape layer",
    "gradient_editor": "Edit gradient colors, stops, direction and radial mode",
    "toggle_wiggle": "Procedurally jitter this shape's position and rotation",
    "duplicate": "Ctrl+D · Duplicate the selected object",
    "delete": "Delete the selected object or keyframes",
}


@dataclass(frozen=True)
class GeneratorKind:
    """One entry per shape-independent, firmware-order layer effect generator.

    Random LED and its four siblings (Strobe, Color Cycle, Pulse, Comet) are
    all flat KeyframeMixin dataclasses with an enabled/opacity pair - this
    table is what lets the FX menu, the Inspector panel and the generic
    scrub/type-to-edit plumbing stay a single implementation shared by every
    kind instead of five near-identical copies.
    """

    key: str
    cls: type
    list_attr: str
    specs: dict
    title: str
    menu_label: str
    description: tuple[str, ...]
    param_props: tuple[str, ...]
    has_color: bool = True
    has_direction: bool = False
    has_blackout: bool = False


GENERATOR_KINDS: dict[str, GeneratorKind] = {
    kind.key: kind for kind in (
        GeneratorKind(
            "random_led", RandomLedEffect, "effects", RANDOM_LED_PROPERTY_SPECS,
            "RANDOM LED / SPARKLE", "Random LED / Sparkle",
            (
                "Directly flashes random firmware LEDs.",
                "Layer shapes do not mask this effect.",
            ),
            ("seed", "life_ms", "born_speed", "particle_count"),
        ),
        GeneratorKind(
            "strobe", StrobeEffect, "strobe_effects", STROBE_PROPERTY_SPECS,
            "STROBE / FLASH", "Strobe / Flash",
            (
                "Flashes every firmware LED on and off at a fixed rate.",
                "Layer shapes do not mask this effect.",
            ),
            ("frequency_hz", "duty_cycle"),
            has_blackout=True,
        ),
        GeneratorKind(
            "color_cycle", ColorCycleEffect, "color_cycle_effects", COLOR_CYCLE_PROPERTY_SPECS,
            "COLOR CYCLE", "Color Cycle / Rainbow",
            (
                "Rotates a rainbow hue across every firmware LED.",
                "Layer shapes do not mask this effect.",
            ),
            ("speed_hz", "spread", "saturation", "brightness"),
            has_color=False, has_direction=True,
        ),
        GeneratorKind(
            "pulse", PulseEffect, "pulse_effects", PULSE_PROPERTY_SPECS,
            "PULSE / BREATHE", "Pulse / Breathe",
            (
                "Breathes every firmware LED's brightness up and down.",
                "Layer shapes do not mask this effect.",
            ),
            ("period_ms", "depth"),
        ),
        GeneratorKind(
            "comet", CometEffect, "comet_effects", COMET_PROPERTY_SPECS,
            "COMET / CHASE", "Comet / Chase",
            (
                "Runs a fading light along the firmware LED order.",
                "Layer shapes do not mask this effect.",
            ),
            ("speed", "trail_length"),
            has_direction=True,
        ),
    )
}
GENERATOR_EDITOR_ACTIONS = {f"{key}_editor" for key in GENERATOR_KINDS}


HELP_COLUMNS = (
    (
        ("PROJECT", (
            ("Ctrl + N / New", "Start a blank project"),
            ("Ctrl + S", "Save project"),
            ("Ctrl + Shift + S", "Save project as"),
            ("Ctrl + O", "Load project"),
            ("Ctrl + I", "Import effect_data.h"),
            ("Autosave", "Recovery snapshot every 30 seconds"),
            ("Ctrl + Z", "Undo"),
            ("Ctrl + Shift + Z", "Redo"),
        )),
        ("TIMELINE / KEYFRAMES", (
            ("Space", "Play / pause, looping the whole sequence"),
            ("Shift + Space", "Play / pause, looping only the loop section"),
            ("|<  /  >|", "Step exactly one frame"),
            ("K", "Add keyframe at playhead"),
            ("V", "Toggle shape, Canvas or Falloff state"),
            ("Ctrl + C / V", "Copy / paste selected keyframes"),
            ("Delete", "Delete selected keyframes or shape"),
            ("G", "Toggle snapping"),
            ("Mouse wheel", "Zoom timeline"),
            ("Shift + wheel", "Scroll timeline horizontally"),
            ("Drag top edge", "Resize timeline; double-click resets"),
            ("Right-drag", "Marquee-select keyframes"),
        )),
    ),
    (
        ("LAYERS / CANVAS", (
            ("Ctrl + D", "Duplicate active layer"),
            ("F2 / layer R", "Rename active layer"),
            ("Layer L", "Lock or unlock layer editing"),
            ("C+ / F+", "Add Canvas / temporal Falloff layer"),
            ("Layer ON/OFF", "Special layers create a state keyframe"),
            ("Enable / Disable", "Write an explicit special-layer state key"),
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
            ("Top FPS meter", "Measured / configured preview FPS"),
        )),
        ("LED MAP", (
            ("Tab / Right", "Next LED position"),
            ("Left", "Previous LED position"),
            ("+  /  -", "Step firmware LED ID"),
            ("Ctrl + S", "Save LED map while LED map is open"),
            ("Ctrl + Z / Shift+Z", "Undo / redo LED map edits (own history)"),
            ("Esc", "Cancel LED movement"),
        )),
    ),
)

TOP_BAR = 54
TOOLBAR_WIDTH = 68
INSPECTOR_WIDTH = 318
DEFAULT_TIMELINE_HEIGHT = 218
MIN_TIMELINE_HEIGHT = 150
MAX_TIMELINE_HEIGHT = 520
TIMELINE_TOOLBAR_HEIGHT = 34
TIMELINE_RULER_HEIGHT = 24
TIMELINE_BOTTOM_PADDING = 13
MIN_VIEWPORT_HEIGHT = 260
DEFAULT_WINDOW_SIZE = (1280, 1024)
MIN_WINDOW_SIZE = (1024, 720)
AUTOSAVE_INTERVAL_MS = 30_000
UI_SETTINGS_FILE = ROOT / "projects" / ".editor_settings.json"
CRASH_LOG_FILE = ROOT / "projects" / "crash.log"
CRASH_LOG_MAX_ENTRIES = 20
_CRASH_LOG_SEPARATOR = "=" * 70 + "\n"
ACCENT = (77, 148, 255)
PANEL = (31, 34, 42)
PANEL_DARK = (24, 26, 33)
CANVAS_STENCIL_BACKGROUND = (40, 112, 142)

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
    "falloff_enabled": "Falloff enabled",
    "layer_opacity": "Layer opacity",
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

    def __init__(
        self,
        screen: pygame.Surface,
        *,
        autosave_directory: str | Path = AUTOSAVE_DIR,
        settings_path: str | Path = UI_SETTINGS_FILE,
        check_recovery: bool = True,
        target_fps: int | None = None,
        safe_scaling: bool | None = None,
    ):
        self.screen = screen
        self.window_flags = pygame.FULLSCREEN if pygame.display.is_fullscreen() else pygame.RESIZABLE
        self.clock = pygame.time.Clock()
        self.target_fps = recommended_preview_fps(override=target_fps)
        self.safe_scaling = is_raspberry_pi() if safe_scaling is None else safe_scaling
        # Looked up once (shells out to git) - shown in the Help panel so
        # it's always clear exactly which commit is running, since the
        # auto-updater can move a Pi to a new one at any restart.
        self.git_commit = get_git_commit()
        self.font = pygame.font.Font(None, 22)
        self.small = pygame.font.Font(None, 18)
        self.title = pygame.font.Font(None, 30)
        self.project = Project("First playfield effect")
        self.project_path: Path | None = None
        self.saved_project_state: dict | None = None
        self.autosave_manager = AutosaveManager(autosave_directory)
        self.ui_settings_store = UiSettingsStore(settings_path)
        self.ui_settings = self.ui_settings_store.load()
        self.timeline_height = self._clamp_timeline_height(
            self.ui_settings.get("timeline_height", DEFAULT_TIMELINE_HEIGHT)
        )
        # A personal palette shared across all projects (like the built-in
        # PALETTE, but user-mixed) - bounded so it never grows unbounded.
        self.custom_colors: list[tuple[int, int, int]] = [
            tuple(color) for color in self.ui_settings.get("custom_colors", [])
        ][-CUSTOM_COLOR_CAP:]
        self.color_picker_open = False
        self.color_picker_rgb: list[int] = [255, 255, 255]
        self.autosave_interval_ms = AUTOSAVE_INTERVAL_MS
        self.last_autosave_tick = pygame.time.get_ticks()
        self.recovery_record: RecoveryRecord | None = (
            self.autosave_manager.latest() if check_recovery else None
        )
        self.recovery_open = self.recovery_record is not None
        self.last_window_caption = ""
        self.current_ms = 0
        self.playing = False
        self.loop_playback = False
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
        self.strobe_editor_open = False
        self.color_cycle_editor_open = False
        self.pulse_editor_open = False
        self.comet_editor_open = False
        self.generator_menu_open = False
        self._generator_menu_anchor: pygame.Rect | None = None
        self.random_effect_editing: str | None = None
        self.random_effect_input = ""
        self.random_effect_input_select_all = False
        self.random_effect_drag_start: float | int | None = None
        self.layer_name_editing_id: str | None = None
        self.layer_name_input = ""
        self.layer_name_input_select_all = False
        self.imported_effects: list[ImportedEffect] = []
        self.active_import_index: int | None = None
        self.effect_data_path: Path | None = None
        self.export_bank_open = False
        self.help_open = False
        self.export_bank_effects: list[ImportedEffect] = []
        self.export_bank_path: Path | None = None
        self.export_bank_project_origin_id: int | None = None
        self.export_bank_selected = -1
        self.export_bank_scroll = 0
        self.export_bank_name_editing = False
        self.export_bank_name_input = ""
        self.export_bank_id_editing = False
        self.export_bank_id_input = ""
        self.export_bank_input_select_all = False
        self.export_bank_status = "Map an effect_data.h or export the current project"
        self.export_bank_sort_mode = "id"
        self.export_bank_query = ""
        self.export_bank_query_editing = False
        # (frames id, effect id, size) -> rendered thumbnail. Keyed off the
        # frames list's own identity, which dataclasses.replace() (renaming/
        # re-IDing an effect) does not change, so edits that only touch the
        # name/ID keep their cached thumbnail; a fresh map/import gets a new
        # frames list and so a fresh (uncached) one.
        self._effect_thumbnail_cache: dict[tuple, pygame.Surface] = {}
        self._falloff_preview_project_state: dict | None = None
        self._falloff_preview_slots: list[tuple[float, float] | None] | None = None
        self._falloff_preview_frames: list[tuple[int, ...]] = []
        self.file_browser_open = False
        self.file_browser_mode = "open"
        self.file_browser_purpose = ""
        self.file_browser_title = "Choose file"
        self.file_browser_directory = ROOT
        self.file_browser_extension = ""
        self.file_browser_filename = ""
        self.file_browser_path_input = str(ROOT)
        self.file_browser_selected = -1
        self.file_browser_scroll = 0
        self.file_browser_path_editing = False
        self.file_browser_filename_editing = False
        self.file_browser_input_select_all = False
        self.file_browser_status = ""
        self.confirmation_open = False
        self.confirmation_title = "Confirm"
        self.confirmation_message = ""
        self.confirmation_action = ""
        self.confirmation_payload: object | None = None
        self.exit_requested = False
        self.restart_requested = False
        self.update_state = "idle"
        self.update_status = f"v{APP_VERSION} · {self.target_fps} FPS"
        self.update_events: SimpleQueue[UpdateEvent] = SimpleQueue()
        self.update_thread: threading.Thread | None = None
        self._scaled_view_cache: dict[
            tuple[str, tuple[int, int], tuple[int, int, int, int]], pygame.Surface
        ] = {}
        self._glow_sprite_cache: dict[tuple[int, tuple[int, int, int]], pygame.Surface] = {}
        self._glow_canvas_cache: dict[tuple[int, int], pygame.Surface] = {}
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
        self.inspector_scroll = 0
        self.inspector_max_scroll = 0
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
        # LED map edits get their OWN undo/redo history, separate from the
        # project's - Ctrl+Z/Ctrl+Shift+Z route to this stack instead while
        # LED map (calibration) mode is open (see _handle_key).
        self.led_map_undo_stack: list[LedMap] = []
        self.led_map_redo_stack: list[LedMap] = []
        self.led_map_change_snapshot: LedMap | None = None
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
            guide_source = self._scale_surface(guide_source, (2048, 990))
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
        self._load_remembered_effect_bank_header()

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

    def run(
        self,
        smoke_test: bool = False,
        screenshot: Path | None = None,
        *,
        auto_update: bool = True,
    ) -> bool:
        if auto_update and not smoke_test:
            self.start_update_check(auto_install=True)
        frames = 0
        redraw_requested = True
        while not self.exit_requested and not self.restart_requested:
            dt = self.clock.tick(self.target_fps)
            events = pygame.event.get()
            for event in events:
                if event.type == pygame.QUIT:
                    self._request_exit()
                else:
                    self.handle_event(event)
            update_changed = self._poll_update_events()
            bridge_changed = self._poll_bridge_requests()
            if self.playing:
                if self.loop_playback:
                    start_ms, end_ms = self._loop_section_bounds()
                    span = max(1.0, end_ms - start_ms)
                    if not start_ms <= self.current_ms < end_ms:
                        self.current_ms = start_ms
                    self.current_ms = start_ms + (self.current_ms - start_ms + dt) % span
                else:
                    duration = max(1, self._playback_duration())
                    self.current_ms = (self.current_ms + dt) % duration
            autosaved = self._maybe_autosave()
            if (
                redraw_requested
                or bool(events)
                or update_changed
                or bridge_changed
                or autosaved
                or self.playing
                or smoke_test
            ):
                self.draw()
                pygame.display.flip()
                frames += 1
                redraw_requested = False
            if smoke_test and frames >= 3:
                if screenshot:
                    pygame.image.save(self.screen, str(screenshot))
                self.exit_requested = True
        return self.restart_requested

    def _poll_bridge_requests(self) -> bool:
        changed = False
        for path, request in read_bridge_requests():
            changed = True
            request_id = str(request.get("id") or path.stem)
            command = request.get("command")
            try:
                if command == "load":
                    target = Path(str(request["path"])).resolve()
                    self.load_project_file(target)
                    self.current_ms = 0
                    self.playing = bool(request.get("play", False))
                    message = f"Loaded through bridge: {target.name}"
                    if self.playing:
                        message += " — playing"
                    self.status = message
                elif command == "bank":
                    target = Path(str(request["path"])).resolve()
                    self.map_effect_bank_file(target)
                    self._open_export_bank()
                    message = f"Mapped through bridge: {target.name} — Effect Bank open"
                    self.export_bank_status = message
                    self.status = message
                elif command == "play":
                    self.current_ms = 0
                    self.playing = True
                    self.loop_playback = False
                    message = "Bridge playback started"
                    self.status = message
                elif command == "stop":
                    self.playing = False
                    message = "Bridge playback stopped"
                    self.status = message
                elif command == "seek":
                    self.playing = False
                    self.current_ms = max(
                        0, min(float(request["time_ms"]), max(0, self.project.duration_ms - 1))
                    )
                    message = f"Bridge playhead: {int(self.current_ms)} ms"
                    self.status = message
                else:
                    raise ValueError(request.get("error") or f"unknown command: {command}")
            except (OSError, ValueError, TypeError, KeyError) as error:
                complete_bridge_request(path, request_id, ok=False, message=f"Bridge error: {error}")
            else:
                complete_bridge_request(path, request_id, ok=True, message=message)
        return changed

    def start_update_check(self, *, auto_install: bool = True) -> bool:
        if self.update_thread and self.update_thread.is_alive():
            return False
        self.update_state = "checking"
        self.update_status = "Checking for updates…"

        def worker() -> None:
            updater = GitUpdater(ROOT, python_executable=sys.executable)
            updater.run(auto_install=auto_install, emit=self.update_events.put)

        self.update_thread = threading.Thread(
            target=worker,
            name="cnc-light-editor-updater",
            daemon=True,
        )
        self.update_thread.start()
        return True

    def _poll_update_events(self) -> bool:
        changed = False
        while True:
            try:
                event = self.update_events.get_nowait()
            except Empty:
                break
            changed = True
            self.update_state = event.state
            self.update_status = event.message
            if event.state in {"downloading", "installing", "failed"}:
                self.status = event.message
            elif event.state == "restart":
                if self._project_is_dirty():
                    self._maybe_autosave(force=True)
                self.restart_requested = True
        return changed

    def _request_exit(self) -> None:
        if self.update_state in {"downloading", "installing"}:
            self.status = "Please wait for the update to finish before exiting"
            return
        self.playing = False
        if self._project_is_dirty():
            self._request_confirmation(
                "Exit editor",
                "The project has unsaved changes. A recovery snapshot will be saved before exit.",
                "exit_application",
            )
            return
        self.exit_requested = True

    def layout(self) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect]:
        width, height = self.screen.get_size()
        self.timeline_height = self._clamp_timeline_height(self.timeline_height)
        workspace = pygame.Rect(
            TOOLBAR_WIDTH, TOP_BAR,
            max(300, width - TOOLBAR_WIDTH - INSPECTOR_WIDTH),
            max(MIN_VIEWPORT_HEIGHT, height - TOP_BAR - self.timeline_height),
        )
        panel = pygame.Rect(width - INSPECTOR_WIDTH, TOP_BAR, INSPECTOR_WIDTH, height - TOP_BAR)
        timeline = pygame.Rect(
            TOOLBAR_WIDTH, height - self.timeline_height, workspace.width, self.timeline_height,
        )
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

    def _clamp_canvas_pan(self, viewport: pygame.Rect) -> None:
        """Keep the playfield reachable after zooming or panning.

        A small canvas stays completely inside the workspace.  A zoomed-in canvas
        may move beyond it, but at least a grab-sized strip always remains visible.
        """
        canvas, _, _ = self.layout()
        grab = min(48, canvas.width, canvas.height)

        if canvas.width <= viewport.width:
            min_left, max_left = viewport.left, viewport.right - canvas.width
        else:
            min_left = viewport.left + grab - canvas.width
            max_left = viewport.right - grab
        if canvas.height <= viewport.height:
            min_top, max_top = viewport.top, viewport.bottom - canvas.height
        else:
            min_top = viewport.top + grab - canvas.height
            max_top = viewport.bottom - grab

        wanted_left = max(min_left, min(max_left, canvas.left))
        wanted_top = max(min_top, min(max_top, canvas.top))
        self.pan += pygame.Vector2(wanted_left - canvas.left, wanted_top - canvas.top)

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.VIDEORESIZE:
            size = (
                max(MIN_WINDOW_SIZE[0], int(event.w)),
                max(MIN_WINDOW_SIZE[1], int(event.h)),
            )
            self.screen = pygame.display.set_mode(size, self.window_flags)
            self.timeline_height = self._clamp_timeline_height(self.timeline_height)
            self.status = f"Workspace resized to {size[0]}×{size[1]}"
            return
        canvas, panel, timeline = self.layout()
        viewport = pygame.Rect(TOOLBAR_WIDTH, TOP_BAR, panel.x - TOOLBAR_WIDTH, timeline.y - TOP_BAR)

        if self.recovery_open:
            self._handle_recovery_event(event)
            return

        if self.confirmation_open:
            self._handle_confirmation_event(event)
            return

        if self.file_browser_open:
            self._handle_file_browser_event(event)
            return

        if self.color_picker_open:
            self._handle_color_picker_event(event)
            return

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
            if (
                event.key == pygame.K_SPACE
                and not self.led_name_editing
                and not self.layer_name_editing_id
            ):
                self.space_down = True
                self._handle_key(event)
                return
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
            if self.layer_name_editing_id:
                self._handle_layer_name_input(event)
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
            if event.key == pygame.K_ESCAPE and self._active_generator_panel_kind():
                self._close_generator_panels()
                self.status = "Closed effect editor"
                return
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
            if event.button == 1 and self.generator_menu_open:
                action = next(
                    (action for rect, action, _ in reversed(self.buttons) if rect.collidepoint(event.pos)),
                    None,
                )
                if action in GENERATOR_EDITOR_ACTIONS:
                    self._action(action)
                else:
                    self.generator_menu_open = False
                return
            if event.button == 1 and self.property_editing:
                self.property_editing = None
                self.property_input_select_all = False
            if event.button == 1 and self.random_effect_editing:
                self.random_effect_editing = None
                self.random_effect_input_select_all = False
            if event.button == 1 and self.layer_name_editing_id:
                self._cancel_layer_rename("Layer rename cancelled")
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
                        if self.project.layers[self.active_layer].locked:
                            self.status = f"Layer {self.project.layers[self.active_layer].name} is locked"
                        else:
                            self.tool_drag = action.split(":", 1)[1]
                            self.drag_origin = event.pos
                    elif action.startswith("drag_layer:"):
                        layer_index = int(action.split(":")[1])
                        if getattr(event, "clicks", 1) >= 2:
                            self._start_layer_rename(layer_index)
                        else:
                            self.drag_layer_index = layer_index
                            self.active_layer = self.drag_layer_index
                            self.timeline_selected_layer_id = None
                            self.drag_origin = event.pos
                            self._begin_change()
                    elif action.startswith("select_layer:") and getattr(event, "clicks", 1) >= 2:
                        self._start_layer_rename(int(action.split(":", 1)[1]))
                    elif action == "duration_slider":
                        self.drag_mode = "duration"
                        self.drag_origin = event.pos
                        self._begin_change()
                        self._set_duration_from_x(event.pos[0], self._duration_slider_rect(timeline))
                    elif action == "loop_bracket_handle":
                        self.drag_mode = "loop_bracket"
                        self.drag_origin = event.pos
                        self._begin_change()
                        self._set_loop_boundary_from_x(event.pos[0], timeline)
                    elif action == "intro_bracket_handle":
                        self.drag_mode = "intro_bracket"
                        self.drag_origin = event.pos
                        self._begin_change()
                        self._set_intro_boundary_from_x(event.pos[0], timeline)
                    elif action == "timeline_resize_handle":
                        if getattr(event, "clicks", 1) >= 2:
                            self.timeline_height = self._clamp_timeline_height(
                                DEFAULT_TIMELINE_HEIGHT
                            )
                            self._save_ui_settings()
                            self.status = f"Timeline reset to {self.timeline_height} px"
                        else:
                            self.drag_mode = "timeline_resize"
                            self.drag_origin = event.pos
                            self.status = "Drag to resize the timeline"
                    elif action == "scrub:layer_opacity":
                        layer = self.project.layers[self.active_layer]
                        self.scrub_prop = "layer_opacity"
                        self.property_editing = None
                        self.drag_mode = "layer_opacity_scrub"
                        self.drag_origin = event.pos
                        self.drag_shape_state = {"layer_opacity": layer.opacity}
                        self._begin_change()
                    elif action.startswith("scrub:") and self.selected:
                        self.scrub_prop = action.split(":", 1)[1]
                        self.property_editing = None
                        self.drag_mode = "scrub"
                        self.drag_origin = event.pos
                        self.drag_shape_state = dict(self.selected.state_at(self.current_ms))
                        self._begin_change()
                    elif action.startswith("generator_field:"):
                        kind = self._active_generator_panel_kind()
                        effect = self._active_generator_effect(kind) if kind else None
                        if effect:
                            prop = action.split(":", 1)[1]
                            self.random_effect_editing = None
                            self.random_effect_input_select_all = False
                            self.scrub_prop = prop
                            self.drag_mode = "generator_scrub"
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
                if self.drag_mode == "timeline_resize":
                    self.drag_mode = None
                    self._save_ui_settings()
                    self.status = f"Timeline height: {self.timeline_height} px"
                    return
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
                    self.drag_mode == "generator_scrub"
                    and self.scrub_prop is not None
                    and pygame.Vector2(event.pos).distance_to(self.drag_origin) < 4
                )
                open_layer_opacity_input = (
                    self.drag_mode == "layer_opacity_scrub"
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
                    self.property_input = SHAPE_PROPERTY_SPECS[prop].input_text(value)
                    self.property_input_select_all = True
                    self.status = f"Type {TIMELINE_PROPERTY_LABELS[prop]}, then press Enter"
                elif open_random_effect_input and self.scrub_prop:
                    self._open_random_effect_input(self.scrub_prop)
                elif open_layer_opacity_input:
                    layer = self.project.layers[self.active_layer]
                    self.property_editing = "layer_opacity"
                    self.property_input = LAYER_PROPERTY_SPECS["layer_opacity"].input_text(
                        layer.opacity
                    )
                    self.property_input_select_all = True
                    self.status = "Type layer opacity, then press Enter"
                self.drag_mode = None
                self.scrub_prop = None
                self.drag_key_time = None
                self.random_effect_drag_start = None
            return

        if event.type == pygame.MOUSEMOTION:
            if self.drag_mode == "pan":
                self.pan += pygame.Vector2(event.rel)
                self._clamp_canvas_pan(viewport)
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
            elif self.drag_mode == "loop_bracket":
                self._set_loop_boundary_from_x(event.pos[0], timeline)
            elif self.drag_mode == "intro_bracket":
                self._set_intro_boundary_from_x(event.pos[0], timeline)
            elif self.drag_mode == "timeline_resize":
                self.timeline_height = self._clamp_timeline_height(
                    self.screen.get_height() - event.pos[1]
                )
                self._clamp_timeline_layer_scroll(self.layout()[2])
            elif self.drag_mode == "led_move":
                self._move_selected_led(event.pos, canvas)
            elif self.drag_mode == "scrub":
                self._scrub_property(event.pos[0])
            elif self.drag_mode == "generator_scrub":
                self._scrub_random_effect_parameter(event.pos[0])
            elif self.drag_mode == "layer_opacity_scrub":
                self._scrub_layer_opacity(event.pos[0])
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
                anchor = mouse if canvas.collidepoint(mouse) else canvas.center
                old_point = self._screen_to_world(anchor, canvas)
                self.zoom = max(0.35, min(5.0, self.zoom * (1.12 ** event.y)))
                new_canvas, _, _ = self.layout()
                new_screen = self._world_to_screen(old_point, new_canvas)
                self.pan += pygame.Vector2(anchor) - pygame.Vector2(new_screen)
                self._clamp_canvas_pan(viewport)
            elif panel.collidepoint(mouse):
                self.inspector_scroll = max(
                    0, min(self.inspector_max_scroll, self.inspector_scroll - event.y * 40),
                )

    def _clamp_timeline_height(self, value: object) -> int:
        try:
            requested = int(value)
        except (TypeError, ValueError):
            requested = DEFAULT_TIMELINE_HEIGHT
        available = max(
            MIN_TIMELINE_HEIGHT,
            self.screen.get_height() - TOP_BAR - MIN_VIEWPORT_HEIGHT,
        )
        maximum = min(MAX_TIMELINE_HEIGHT, available)
        return max(MIN_TIMELINE_HEIGHT, min(maximum, requested))

    def _save_ui_settings(self) -> None:
        self.ui_settings["timeline_height"] = self.timeline_height
        try:
            self.ui_settings_store.save(self.ui_settings)
        except OSError:
            self.status = "Timeline resized, but its UI preference could not be saved"

    def _handle_key(self, event: pygame.event.Event) -> None:
        modifiers = getattr(event, "mod", pygame.key.get_mods())
        ctrl = bool(modifiers & pygame.KMOD_CTRL)
        shift = bool(modifiers & pygame.KMOD_SHIFT)
        if ctrl and event.key == pygame.K_z:
            if self.calibration:
                self._redo_led_map() if shift else self._undo_led_map()
            else:
                self._redo() if shift else self._undo()
            return
        if ctrl and event.key == pygame.K_y:
            self._redo_led_map() if self.calibration else self._redo()
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
        if event.key == pygame.K_SPACE:
            if not getattr(event, "repeat", False):
                self._toggle_loop_playback() if shift else self._toggle_playback()
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

        if event.key == pygame.K_F2:
            self._start_layer_rename(self.active_layer)
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
            if layer.is_canvas or layer.is_falloff:
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
        if self._action_requires_unlocked_layer(action):
            layer = self.project.layers[self.active_layer]
            if layer.locked:
                self.status = f"Layer {layer.name} is locked"
                return
        mutating = (
            action.startswith((
                "add:", "color:", "custom_color:", "fill_mode:", "gradient_type:",
                "gradient_radial_mode:", "toggle_layer:", "toggle_layer_lock:",
                "canvas_state:", "falloff_state:", "falloff_ms:",
            ))
            or action in {
                "layer", "canvas_layer", "falloff_layer", "duplicate_layer", "delete_layer", "delete", "duplicate", "keyframe",
                "effect_id:-1", "effect_id:1", "cycle_fps", "loops:-1", "loops:1",
                "clear_loop_end", "toggle_overlay",
                "gradient_add_stop", "gradient_delete_stop",
                "generator_toggle", "generator_keyframe", "generator_opacity_keyframe",
                "generator_toggle_direction", "generator_toggle_blackout",
                "generator_delete", "toggle_wiggle",
            }
        )
        if mutating:
            self._begin_change()
        if action.startswith("add:"):
            if (
                self.project.layers[self.active_layer].is_canvas
                or self.project.layers[self.active_layer].is_falloff
            ):
                self.status = "Select a regular layer before adding a shape"
            else:
                self._create_shape(action.split(":", 1)[1], (0.5, 0.5), history=False)
        elif action == "layer":
            self.project.layers.append(Layer(f"Layer {len(self.project.layers) + 1}"))
            self.active_layer = len(self.project.layers) - 1
            self.timeline_selected_layer_id = None
            self.selected = None
            self._ensure_active_layer_visible()
        elif action.startswith("rename_layer:"):
            self._start_layer_rename(int(action.split(":", 1)[1]))
        elif action == "canvas_layer":
            existing = next(
                (index for index, layer in enumerate(self.project.layers) if layer.is_canvas),
                None,
            )
            if existing is not None:
                self.active_layer = existing
                self.timeline_selected_layer_id = None
                self.selected = None
                self._close_generator_panels()
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
                self._close_generator_panels()
                self.selected_keyframes = {(canvas_layer.id, "canvas_enabled", 0)}
                self.timeline_expanded_layers.add(canvas_layer.id)
                self._ensure_active_layer_visible()
                self.status = "Canvas layer added — ON means empty LEDs stay transparent"
        elif action == "falloff_layer":
            existing = next(
                (index for index, layer in enumerate(self.project.layers) if layer.is_falloff),
                None,
            )
            if existing is not None:
                self.active_layer = existing
                self.timeline_selected_layer_id = None
                self.selected = None
                self._close_generator_panels()
                self.timeline_expanded_layers.add(self.project.layers[existing].id)
                self.status = "The project already has a Falloff layer"
            else:
                falloff_layer = Layer(
                    "Falloff", is_falloff=True, falloff_enabled=True, falloff_ms=600,
                )
                falloff_layer.add_keyframe("falloff_enabled", 0, True)
                self.project.layers.append(falloff_layer)
                self.active_layer = len(self.project.layers) - 1
                self.timeline_selected_layer_id = None
                self.selected = None
                self._close_generator_panels()
                self.selected_keyframes = {(falloff_layer.id, "falloff_enabled", 0)}
                self.timeline_expanded_layers.add(falloff_layer.id)
                self._ensure_active_layer_visible()
                self.status = "Falloff layer added — LEDs fade out over 600 ms"
        elif action == "duplicate_layer":
            source = self.project.layers[self.active_layer]
            if source.is_canvas or source.is_falloff:
                kind = "Canvas" if source.is_canvas else "Falloff"
                self.status = f"A project can only have one {kind} layer"
            else:
                selected_index = source.shapes.index(self.selected) if self.selected in source.shapes else None
                clone = deepcopy(source)
                clone.id = uuid4().hex[:10]
                clone.name = f"{source.name} copy"
                for shape in clone.shapes:
                    shape.id = uuid4().hex[:10]
                for effect in self._layer_generator_effects(clone):
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
            if layer.is_canvas or layer.is_falloff:
                prop = "canvas_enabled" if layer.is_canvas else "falloff_enabled"
                value = bool(layer.value_at(prop, self.current_ms))
                layer.add_keyframe(prop, self.current_ms, value)
                self.selected_keyframes = {(layer.id, prop, self.current_ms)}
                self.timeline_expanded_layers.add(layer.id)
                kind = "Canvas" if layer.is_canvas else "Falloff"
                self.status = f"{kind} keyframe at {self.current_ms} ms"
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
        elif action.startswith("falloff_state:"):
            layer = self.project.layers[self.active_layer]
            if layer.is_falloff:
                self._set_falloff_enabled_key(layer, bool(int(action.split(":", 1)[1])))
        elif action.startswith("falloff_ms:"):
            layer = self.project.layers[self.active_layer]
            if layer.is_falloff:
                delta = int(action.split(":", 1)[1])
                layer.falloff_ms = max(100, min(2000, layer.falloff_ms + delta))
                self.status = f"Falloff duration: {layer.falloff_ms} ms"
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
            self._toggle_playback()
        elif action == "play_loop":
            self._toggle_loop_playback()
        elif action.startswith("step_frame:"):
            self._step_frame(int(action.split(":", 1)[1]))
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
            self._close_generator_panels()
            self.selected_gradient_stop_id = self.selected.gradient_stops[0].id
            self.status = "Gradient editor — click the bar to add a color stop"
        elif action == "gradient_back":
            self.gradient_editor_open = False
            self.drag_mode = None
            self.status = "Gradient changes applied"
        elif action == "generator_menu_toggle":
            self.generator_menu_open = not self.generator_menu_open
        elif action in GENERATOR_EDITOR_ACTIONS:
            kind = action[: -len("_editor")]
            info = GENERATOR_KINDS[kind]
            self.generator_menu_open = False
            if (
                self.project.layers[self.active_layer].is_canvas
                or self.project.layers[self.active_layer].is_falloff
            ):
                self.status = f"Select a regular layer before adding a {info.menu_label} effect"
                if mutating:
                    self._commit_change()
                return
            effect = self._active_generator_effect(kind)
            if effect is None:
                self._begin_change()
                effect = info.cls()
                getattr(self.project.layers[self.active_layer], info.list_attr).append(effect)
                self._commit_change()
            self._close_generator_panels()
            setattr(self, f"{kind}_editor_open", True)
            self.random_effect_editing = None
            self.random_effect_input_select_all = False
            self.gradient_editor_open = False
            self.selected_keyframes.clear()
            self.status = f"{info.menu_label} — {info.description[0]}"
        elif action == "generator_back":
            self._close_generator_panels()
            self.random_effect_editing = None
            self.random_effect_input_select_all = False
            self.status = "Effect changes applied"
        elif action == "generator_delete":
            kind = self._active_generator_panel_kind()
            effect = self._active_generator_effect(kind) if kind else None
            if effect and kind:
                getattr(self.project.layers[self.active_layer], GENERATOR_KINDS[kind].list_attr).remove(effect)
                self.selected_keyframes = {
                    key for key in self.selected_keyframes if key[0] != effect.id
                }
            self._close_generator_panels()
            self.random_effect_editing = None
            self.random_effect_input_select_all = False
            self.status = "Effect removed from layer"
        elif action == "generator_toggle":
            kind = self._active_generator_panel_kind()
            effect = self._active_generator_effect(kind) if kind else None
            if effect:
                value = not bool(effect.value_at("enabled", self.current_ms))
                if self.current_ms == 0 and not effect.keyframes.get("enabled"):
                    effect.enabled = value
                else:
                    effect.add_keyframe("enabled", self.current_ms, value)
                    self.selected_keyframes = {(effect.id, "enabled", self.current_ms)}
                self.status = f"Effect {'enabled' if value else 'disabled'} at {self.current_ms} ms"
        elif action == "generator_keyframe":
            kind = self._active_generator_panel_kind()
            effect = self._active_generator_effect(kind) if kind else None
            if effect:
                value = bool(effect.value_at("enabled", self.current_ms))
                effect.add_keyframe("enabled", self.current_ms, value)
                self.selected_keyframes = {(effect.id, "enabled", self.current_ms)}
                self.status = f"Enabled keyframe at {self.current_ms} ms"
        elif action == "generator_opacity_keyframe":
            kind = self._active_generator_panel_kind()
            effect = self._active_generator_effect(kind) if kind else None
            if effect:
                value = float(effect.value_at("opacity", self.current_ms))
                effect.add_keyframe("opacity", self.current_ms, value)
                self.selected_keyframes = {(effect.id, "opacity", self.current_ms)}
                self.timeline_expanded_layers.add(self.project.layers[self.active_layer].id)
                self.status = f"Opacity keyframe at {self.current_ms} ms"
        elif action == "generator_toggle_direction":
            kind = self._active_generator_panel_kind()
            effect = self._active_generator_effect(kind) if kind else None
            if effect is not None and hasattr(effect, "direction"):
                effect.direction = -1 if effect.direction >= 0 else 1
                self.status = f"Direction: {'reverse' if effect.direction < 0 else 'forward'}"
        elif action == "generator_toggle_blackout":
            kind = self._active_generator_panel_kind()
            effect = self._active_generator_effect(kind) if kind else None
            if effect is not None and hasattr(effect, "blackout"):
                effect.blackout = not effect.blackout
                self.status = (
                    "Strobe cuts to black, interrupting the animation underneath"
                    if effect.blackout else "Strobe flashes its own color"
                )
        elif action == "toggle_wiggle" and self.selected:
            enabled = bool(self.selected.state_at(self.current_ms)["wiggle_enabled"])
            self._set_animated("wiggle_enabled", not enabled)
            self.status = f"Wiggle {'enabled' if not enabled else 'disabled'}"
        elif action.startswith("gradient_type:") and self.selected:
            gradient_type = action.split(":", 1)[1]
            self._set_animated("gradient_type", gradient_type)
            target = self.selected.state_at(self.current_ms).get("fill_mode", "fill")
            self.status = f"Gradient {target}: {gradient_type}"
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
        elif action == "exit":
            self._request_exit()
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
            self._begin_led_change()
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
            self._commit_led_change()
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
        elif action.startswith("color:") and (self.selected or self._active_generator_panel_kind()):
            self._apply_color(PALETTE[int(action.split(":", 1)[1])])
        elif action.startswith("custom_color:") and (self.selected or self._active_generator_panel_kind()):
            index = int(action.split(":", 1)[1])
            if 0 <= index < len(self.custom_colors):
                self._apply_color(tuple(self.custom_colors[index]))
        elif action == "open_color_picker":
            panel_kind = self._active_generator_panel_kind()
            if panel_kind:
                effect = self._active_generator_effect(panel_kind)
                current = getattr(effect, "color", (255, 255, 255)) if effect else (255, 255, 255)
            elif self.gradient_editor_open and self.selected_gradient_stop_id:
                stop = self._selected_gradient_stop()
                current = stop.color if stop else (255, 255, 255)
            elif self.selected:
                current = tuple(self.selected.state_at(self.current_ms)["color"])
            else:
                current = (255, 255, 255)
            self.color_picker_rgb = list(current)
            self.color_picker_open = True
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
                    if target is layer or target in [*layer.shapes, *self._layer_generator_effects(layer)]
                ),
                None,
            )
            if target and layer_index is not None:
                self.active_layer = layer_index
                self.timeline_selected_layer_id = None
                self.gradient_editor_open = False
                self._close_generator_panels()
                if isinstance(target, Shape):
                    self.selected = target
                elif isinstance(target, Layer):
                    self.selected = None
                else:
                    self.selected = None
                    generator_kind = self._generator_kind_of(target)
                    if generator_kind:
                        setattr(self, f"{generator_kind}_editor_open", True)
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
            self._close_generator_panels()
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
            elif layer.is_falloff:
                self.active_layer = index
                enabled = not bool(layer.value_at("falloff_enabled", self.current_ms))
                self._set_falloff_enabled_key(layer, enabled)
            else:
                layer.visible = not layer.visible
                if not layer.visible and self.selected in layer.shapes:
                    self.selected = None
                    self.selected_keyframes.clear()
                self.status = f"Layer {layer.name}: {'ON' if layer.visible else 'OFF'}"
        elif action.startswith("toggle_layer_lock:"):
            index = int(action.split(":", 1)[1])
            layer = self.project.layers[index]
            layer.locked = not layer.locked
            self.active_layer = index
            self.timeline_selected_layer_id = layer.id
            if layer.locked:
                if self.selected in layer.shapes:
                    self.selected = None
                target_ids = {
                    layer.id, *(shape.id for shape in layer.shapes),
                    *(effect.id for effect in self._layer_generator_effects(layer)),
                }
                self.selected_keyframes = {
                    key for key in self.selected_keyframes if key[0] not in target_ids
                }
                self._close_generator_panels()
            self.status = f"Layer {layer.name}: {'LOCKED' if layer.locked else 'UNLOCKED'}"
        if mutating:
            self._commit_change()

    @staticmethod
    def _action_requires_unlocked_layer(action: str) -> bool:
        if action.startswith((
            "add:", "color:", "fill_mode:", "gradient_type:",
            "gradient_radial_mode:", "canvas_state:", "falloff_state:", "falloff_ms:",
        )):
            return True
        if action in GENERATOR_EDITOR_ACTIONS:
            return True
        return action in {
            "delete_layer", "delete", "duplicate", "keyframe",
            "gradient_editor", "gradient_add_stop", "gradient_delete_stop",
            "generator_toggle", "generator_keyframe",
            "generator_opacity_keyframe", "generator_delete", "toggle_wiggle",
        }

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
            (
                shape for layer in self.project.layers if not layer.locked
                for shape in layer.shapes if shape.id == selected_id
            ),
            None,
        )
        if (
            self.selected is None
            and not self.project.layers[self.active_layer].locked
            and self.project.layers[self.active_layer].shapes
        ):
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

    def _begin_led_change(self) -> None:
        if self.led_map_change_snapshot is None:
            self.led_map_change_snapshot = deepcopy(self.led_map)

    def _commit_led_change(self) -> None:
        if self.led_map_change_snapshot is None:
            return
        if self.led_map_change_snapshot != self.led_map:
            self.led_map_undo_stack.append(self.led_map_change_snapshot)
            self.led_map_undo_stack = self.led_map_undo_stack[-100:]
            self.led_map_redo_stack.clear()
        self.led_map_change_snapshot = None

    def _restore_led_map(self, led_map: LedMap) -> None:
        self.led_map = deepcopy(led_map)
        self.led_points = self.led_map.normalized_points()
        if not any(led.id == self.selected_led_id for led in self.led_map.leds):
            self.selected_led_id = self.led_map.leds[0].id
        self.led_id_editing = False
        self.led_name_editing = False
        self._sync_led_fields()

    def _undo_led_map(self) -> None:
        if not self.led_map_undo_stack:
            self.status = "Nothing to undo in the LED map"
            return
        self.led_map_redo_stack.append(deepcopy(self.led_map))
        self._restore_led_map(self.led_map_undo_stack.pop())
        self.status = "LED map: Undo"

    def _redo_led_map(self) -> None:
        if not self.led_map_redo_stack:
            self.status = "Nothing to redo in the LED map"
            return
        self.led_map_undo_stack.append(deepcopy(self.led_map))
        self._restore_led_map(self.led_map_redo_stack.pop())
        self.status = "LED map: Redo"

    def _create_shape(
        self, kind: str, point: tuple[float, float], history: bool = True
    ) -> None:
        active_layer = self.project.layers[self.active_layer]
        if active_layer.locked:
            self.status = f"Layer {active_layer.name} is locked"
            return
        if active_layer.is_canvas or active_layer.is_falloff:
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
        if (
            not self.selected or not self.drag_shape_state
            or self._target_is_locked(self.selected.id)
        ):
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
        track_top = rect.y + TIMELINE_TOOLBAR_HEIGHT + TIMELINE_RULER_HEIGHT
        return pygame.Rect(
            rect.x + 180,
            track_top,
            max(80, rect.width - 195),
            max(1, rect.bottom - TIMELINE_BOTTOM_PADDING - track_top),
        )

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
        if layer.is_canvas or layer.is_falloff:
            return [layer]
        return [*layer.shapes, *Editor._layer_generator_effects(layer)]

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
        if self._target_is_locked(handle[0]):
            self.status = "Unlock the layer before editing its easing curve"
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
        if self._target_is_locked(target.id):
            self.status = "Unlock the layer before moving its keyframes"
            return
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

    def _timeline_keyframe_button_rect(self, timeline: pygame.Rect) -> pygame.Rect:
        """+ Keyframe button rect - right after the transport buttons
        (prev/play/next/loop end at timeline.x + 328), same row as play."""
        label_width = self.small.size("+ Keyframe")[0]
        width = max(90, label_width + 20)
        return pygame.Rect(timeline.x + 328 + 10, timeline.y + 5, width, 25)

    def _timeline_duration_area_x(self, timeline: pygame.Rect) -> int:
        """Left edge for the Length label + input + slider cluster - right
        after the + Keyframe button, so it sits in the same row/line as the
        play button too."""
        return self._timeline_keyframe_button_rect(timeline).right + 14

    def _duration_input_rect(self, timeline: pygame.Rect) -> pygame.Rect:
        label_width = self.small.size("Length")[0]
        x = self._timeline_duration_area_x(timeline) + label_width + 5
        # y = timeline.y + 9, not +5 like the transport buttons: the resize
        # handle strip covers timeline.y .. timeline.y + 8 (full width), and
        # this field's clickable rect must clear it entirely (unlike the
        # transport buttons, its slider sibling's hit-box is inflated and
        # would otherwise reach back into that strip - see _duration_slider_rect).
        return pygame.Rect(x, timeline.y + 9, 64, 25)

    def _duration_slider_rect(self, timeline: pygame.Rect) -> pygame.Rect:
        input_rect = self._duration_input_rect(timeline)
        # y = timeline.y + 16: its clickable hit-box is this rect inflated by
        # 14px vertically (see _draw_timeline), so the bar itself must sit low
        # enough that even the inflated box (from timeline.y + 9) still clears
        # the resize handle strip (timeline.y .. timeline.y + 8).
        return pygame.Rect(input_rect.right + 8, timeline.y + 16, 78, 16)

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

    def _set_loop_boundary_from_x(self, screen_x: int, timeline: pygame.Rect) -> None:
        if self._active_imported_effect():
            return
        time_ms = self._timeline_x_to_time(screen_x, timeline)
        frame_ms = max(1.0, self._timeline_frame_ms())
        boundary = max(1, min(self.project.stored_frame_count, round(time_ms / frame_ms)))
        self.project.loop_frames = 0 if boundary >= self.project.stored_frame_count else boundary
        self.status = (
            "Loop uses the full effect"
            if self.project.loop_frames == 0
            else f"Loop ends after frame {self.project.loop_frames - 1} — outro plays once, ×{self.project.loops} repeats"
        )

    def _set_intro_boundary_from_x(self, screen_x: int, timeline: pygame.Rect) -> None:
        if self._active_imported_effect():
            return
        time_ms = self._timeline_x_to_time(screen_x, timeline)
        frame_ms = max(1.0, self._timeline_frame_ms())
        boundary = max(0, min(self.project.normalized_loop_frames, round(time_ms / frame_ms)))
        self.project.intro_frames = boundary
        self.status = (
            "No intro — the loop starts immediately"
            if boundary == 0
            else f"Intro plays once for {boundary} frame(s), then the loop starts"
        )

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
        elif (
            self.project.layers[self.active_layer].is_canvas
            or self.project.layers[self.active_layer].is_falloff
        ):
            layer_index = self.active_layer
        elif (kind := self._active_generator_panel_kind()) and self._active_generator_effect(kind):
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
        if self._selection_contains_locked_keyframes():
            self.status = "Unlock the layer before moving its keyframes"
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
        if self._selection_contains_locked_keyframes():
            self.status = "Unlock the layer before deleting its keyframes"
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
        if any(self._target_is_locked(target_id) for _target, target_id, *_rest in records):
            self.status = "Unlock the target layer before pasting keyframes"
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
        if self._selection_contains_locked_keyframes():
            self.status = "Unlock the layer before editing its keyframes"
            return
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

    def _start_layer_rename(self, layer_index: int) -> None:
        if not 0 <= layer_index < len(self.project.layers):
            return
        self.active_layer = layer_index
        layer = self.project.layers[layer_index]
        self.layer_name_editing_id = layer.id
        self.layer_name_input = layer.name
        self.layer_name_input_select_all = True
        self.status = "Type the layer name, then press Enter"

    def _cancel_layer_rename(self, status: str = "Layer rename cancelled") -> None:
        self.layer_name_editing_id = None
        self.layer_name_input = ""
        self.layer_name_input_select_all = False
        self.status = status

    def _handle_layer_name_input(self, event: pygame.event.Event) -> None:
        layer = next(
            (
                item for item in self.project.layers
                if item.id == self.layer_name_editing_id
            ),
            None,
        )
        if layer is None:
            self._cancel_layer_rename()
            return
        ctrl = bool(getattr(event, "mod", pygame.key.get_mods()) & pygame.KMOD_CTRL)
        if ctrl and event.key == pygame.K_a:
            self.layer_name_input_select_all = True
            self.status = "Layer name selected"
            return
        if ctrl and event.key == pygame.K_v:
            pasted = " ".join(
                self._clipboard_text().replace("\x00", "").splitlines()
            ).strip()
            if not pasted:
                self.status = "Clipboard contains no text"
                return
            self.layer_name_input = pasted[:40] if self.layer_name_input_select_all else (
                self.layer_name_input + pasted
            )[:40]
            self.layer_name_input_select_all = False
            self.status = "Pasted layer name — press Enter"
            return
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            name = self.layer_name_input.strip()
            if not name:
                self.status = "Layer name cannot be empty"
                return
            self._begin_change()
            layer.name = name
            self._commit_change()
            self._cancel_layer_rename(f"Layer renamed: {name}")
            return
        if event.key == pygame.K_ESCAPE:
            self._cancel_layer_rename()
            return
        if event.key == pygame.K_BACKSPACE:
            if self.layer_name_input_select_all:
                self.layer_name_input = ""
                self.layer_name_input_select_all = False
            else:
                self.layer_name_input = self.layer_name_input[:-1]
            return
        character = getattr(event, "unicode", "")
        if character.isprintable() and character not in "\r\n\t":
            if self.layer_name_input_select_all:
                self.layer_name_input = ""
                self.layer_name_input_select_all = False
            if len(self.layer_name_input) < 40:
                self.layer_name_input += character

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
        spec = SHAPE_PROPERTY_SPECS[prop]
        value = spec.normalize(start + delta * spec.sensitivity)
        if self.snap:
            step = 0.001 if prop in {"stroke_width", "feather", "mask_expansion"} else 0.01
            value = round(value / step) * step
        self._set_animated(prop, value)

    def _scrub_layer_opacity(self, screen_x: int) -> None:
        if not self.drag_shape_state:
            return
        spec = LAYER_PROPERTY_SPECS["layer_opacity"]
        start = float(self.drag_shape_state["layer_opacity"])
        value = spec.normalize(start + (screen_x - self.drag_origin[0]) * spec.sensitivity)
        self.project.layers[self.active_layer].opacity = float(value)
        self.status = f"Layer opacity: {spec.display_text(value)}"

    @staticmethod
    def _random_effect_parameter_value(
        effect, prop: str, time_ms: int | None = None,
    ) -> float | int:
        if prop == "opacity" and time_ms is not None:
            return float(effect.value_at(prop, time_ms))
        return getattr(effect, prop)

    def _active_generator_specs(self) -> dict:
        kind = self._active_generator_panel_kind()
        return GENERATOR_KINDS[kind].specs if kind else RANDOM_LED_PROPERTY_SPECS

    def _normalize_random_effect_parameter(self, prop: str, value: float) -> float | int:
        try:
            return self._active_generator_specs()[prop].normalize(value)
        except KeyError as error:
            raise ValueError(f"Unknown effect parameter: {prop}") from error

    def _set_random_effect_parameter(
        self, effect, prop: str, value: float,
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
        kind = self._active_generator_panel_kind()
        effect = self._active_generator_effect(kind) if kind else None
        if not effect or not self.scrub_prop or self.random_effect_drag_start is None:
            return
        prop = self.scrub_prop
        spec = self._active_generator_specs()[prop]
        value = float(self.random_effect_drag_start) + (
            screen_x - self.drag_origin[0]
        ) * spec.sensitivity
        normalized = self._set_random_effect_parameter(effect, prop, value)
        self.status = self._random_effect_parameter_status(prop, normalized)

    def _open_random_effect_input(self, prop: str) -> None:
        kind = self._active_generator_panel_kind()
        effect = self._active_generator_effect(kind) if kind else None
        if not effect:
            return
        value = self._random_effect_parameter_value(effect, prop, self.current_ms)
        self.random_effect_editing = prop
        self.random_effect_input = self._active_generator_specs()[prop].input_text(value)
        self.random_effect_input_select_all = True
        self.status = f"Type {self._random_effect_parameter_label(prop)}, then press Enter"

    def _random_effect_parameter_label(self, prop: str) -> str:
        return self._active_generator_specs()[prop].label

    def _random_effect_parameter_status(self, prop: str, value: float | int) -> str:
        spec = self._active_generator_specs()[prop]
        return f"{spec.label}: {spec.display_text(value)}"

    def _set_animated(self, prop: str, value) -> None:
        if not self.selected:
            return
        if self.current_ms == 0 and not self.selected.keyframes.get(prop):
            setattr(self.selected, prop, value)
        else:
            self.selected.add_keyframe(prop, self.current_ms, value)

    def _apply_color(self, color: tuple[int, int, int]) -> None:
        """Applies a colour to whatever the palette is currently attached to
        (random LED flash colour, a gradient stop, selected keyframes, or
        the active shape). Undo-tracking is the CALLER's job: _action()'s
        "color:"/"custom_color:" mutating-wrap covers palette swatch clicks,
        and the colour picker's Apply button wraps this call explicitly."""
        panel_kind = self._active_generator_panel_kind()
        if panel_kind:
            effect = self._active_generator_effect(panel_kind)
            if effect is not None and hasattr(effect, "color"):
                effect.color = color
                self.status = f"{GENERATOR_KINDS[panel_kind].menu_label} color: RGB {color}"
            return
        if not self.selected:
            return
        if self.gradient_editor_open and self.selected_gradient_stop_id:
            stop = self._selected_gradient_stop()
            if stop:
                stop.color = color
                self.status = f"Gradient stop color: RGB {color}"
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

    def _apply_custom_color(self, color: tuple[int, int, int]) -> None:
        """Applies `color` (from the mixer) and, unless it's already saved,
        adds it to the personal custom-colour palette (bounded, FIFO -
        oldest evicted first, mirroring the effect bank's single-rolling-
        backup philosophy: bounded state, not an ever-growing list)."""
        if color not in (tuple(existing) for existing in self.custom_colors):
            self.custom_colors.append(color)
            self.custom_colors = self.custom_colors[-CUSTOM_COLOR_CAP:]
            self.ui_settings["custom_colors"] = [list(entry) for entry in self.custom_colors]
            try:
                self.ui_settings_store.save(self.ui_settings)
            except OSError:
                pass
        self._apply_color(color)

    def _set_canvas_enabled_key(self, layer: Layer, enabled: bool) -> None:
        layer.add_keyframe("canvas_enabled", self.current_ms, enabled)
        self.selected = None
        self._close_generator_panels()
        self.selected_keyframes = {(layer.id, "canvas_enabled", self.current_ms)}
        self.timeline_expanded_layers.add(layer.id)
        mode = "transparent empty LEDs" if enabled else "blackout empty LEDs"
        self.status = f"Canvas {'enabled' if enabled else 'disabled'} at {self.current_ms} ms — {mode}"

    def _set_falloff_enabled_key(self, layer: Layer, enabled: bool) -> None:
        layer.add_keyframe("falloff_enabled", self.current_ms, enabled)
        self.selected = None
        self._close_generator_panels()
        self.selected_keyframes = {(layer.id, "falloff_enabled", self.current_ms)}
        self.timeline_expanded_layers.add(layer.id)
        self.status = (
            f"Falloff {'enabled' if enabled else 'disabled'} at {self.current_ms} ms"
            + (f" — {layer.falloff_ms} ms fade" if enabled else "")
        )

    def _set_rotation_total(self, total_degrees: float) -> None:
        turns = math.floor(total_degrees / 360.0)
        angle = total_degrees - turns * 360.0
        self._set_animated("rotation", round(angle, 6))
        self._set_animated("rotation_turns", float(turns))

    def _pick(self, point: tuple[float, float]) -> Shape | None:
        for layer in reversed(self.project.layers):
            if not layer.visible or layer.locked:
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

    def _active_generator_effect(self, kind: str) -> object | None:
        if not 0 <= self.active_layer < len(self.project.layers):
            return None
        layer = self.project.layers[self.active_layer]
        effects = getattr(layer, GENERATOR_KINDS[kind].list_attr)
        return effects[0] if effects else None

    def _active_random_led_effect(self) -> RandomLedEffect | None:
        return self._active_generator_effect("random_led")

    def _active_generator_panel_kind(self) -> str | None:
        return next(
            (kind for kind in GENERATOR_KINDS if getattr(self, f"{kind}_editor_open")), None,
        )

    def _close_generator_panels(self) -> None:
        for kind in GENERATOR_KINDS:
            setattr(self, f"{kind}_editor_open", False)

    @staticmethod
    def _layer_generator_effects(layer: Layer) -> list:
        effects: list = []
        for kind in GENERATOR_KINDS.values():
            effects.extend(getattr(layer, kind.list_attr))
        return effects

    @staticmethod
    def _generator_kind_of(effect: object) -> str | None:
        return next(
            (kind.key for kind in GENERATOR_KINDS.values() if isinstance(effect, kind.cls)), None,
        )

    def _active_keyframe_target(self) -> Layer | Shape | RandomLedEffect | None:
        layer = self.project.layers[self.active_layer]
        if layer.is_canvas or layer.is_falloff:
            return layer
        kind = self._active_generator_panel_kind()
        if kind:
            return self._active_generator_effect(kind)
        return self.selected

    def _find_keyframe_target(self, target_id: str) -> Layer | Shape | RandomLedEffect | None:
        for layer in self.project.layers:
            for target in [layer, *layer.shapes, *self._layer_generator_effects(layer)]:
                if target.id == target_id:
                    return target
        return None

    def _layer_for_target_id(self, target_id: str) -> Layer | None:
        for layer in self.project.layers:
            if layer.id == target_id:
                return layer
            if any(
                target.id == target_id
                for target in [*layer.shapes, *self._layer_generator_effects(layer)]
            ):
                return layer
        return None

    def _target_is_locked(self, target_id: str) -> bool:
        layer = self._layer_for_target_id(target_id)
        return bool(layer and layer.locked)

    def _selection_contains_locked_keyframes(self) -> bool:
        return any(
            self._target_is_locked(target_id)
            for target_id, _prop, _time_ms in self.selected_keyframes
        )

    def _playback_duration(self) -> int:
        effect = self._active_imported_effect()
        return effect.duration_ms if effect else self.project.duration_ms

    def _loop_section_bounds(self) -> tuple[int, int]:
        """The [start_ms, end_ms) span "Play Loop" repeats - the intro/loop
        boundary the timeline bracket controls, not the whole stored duration."""
        imported = self._active_imported_effect()
        frame_ms = self._timeline_frame_ms()
        if imported:
            intro_frames = imported.normalized_intro_frames
            loop_frames = imported.normalized_loop_frames
        else:
            intro_frames = self.project.normalized_intro_frames
            loop_frames = self.project.normalized_loop_frames
        start_ms = intro_frames * frame_ms
        end_ms = max(start_ms + frame_ms, loop_frames * frame_ms)
        return round(start_ms), round(end_ms)

    def _toggle_playback(self) -> None:
        if self.playing and not self.loop_playback:
            self.playing = False
            self.status = "Playback stopped"
            return
        self.playing = True
        self.loop_playback = False
        duration = max(1, self._playback_duration())
        if self.current_ms >= duration:
            self.current_ms = 0
        self.status = "Playing the full sequence, looping"

    def _toggle_loop_playback(self) -> None:
        if self.playing and self.loop_playback:
            self.playing = False
            self.loop_playback = False
            self.status = "Playback stopped"
            return
        self.playing = True
        self.loop_playback = True
        start_ms, end_ms = self._loop_section_bounds()
        if not start_ms <= self.current_ms < end_ms:
            self.current_ms = start_ms
        self.status = "Looping only the loop section"

    def _step_frame(self, direction: int) -> None:
        frame_ms = max(1, round(self._timeline_frame_ms()))
        duration = max(1, self._playback_duration())
        last_frame = max(0, (duration - 1) // frame_ms)
        if direction < 0:
            frame_index = math.ceil(self.current_ms / frame_ms) - 1
        else:
            frame_index = math.floor(self.current_ms / frame_ms) + 1
        frame_index = max(0, min(last_frame, frame_index))
        self.current_ms = frame_index * frame_ms
        self.playing = False
        self.status = f"Frame {frame_index}  •  {self.current_ms} ms"

    def load_effect_data_file(self, path: str | Path) -> None:
        path = Path(path)
        self.imported_effects = load_effect_data(path)
        self.export_bank_compressed = bool(
            re.search(r"#define\s+FX_FRAME_CODEC\s+1\b", path.read_text(encoding="utf-8"))
        )
        self._packed_size_cache = {}
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

    def _maybe_autosave(self, *, force: bool = False, now_ms: int | None = None) -> bool:
        now = pygame.time.get_ticks() if now_ms is None else int(now_ms)
        if self.recovery_open or not self._project_is_dirty():
            return False
        if not force and now - self.last_autosave_tick < self.autosave_interval_ms:
            return False
        try:
            path = self.autosave_manager.write(self.project.to_dict(), self.project_path)
            self.recovery_record = self.autosave_manager.latest()
            self.status = f"Autosaved recovery snapshot: {path.name}"
            return True
        except (OSError, TypeError, ValueError) as error:
            self.status = f"Autosave failed: {error}"
            return False
        finally:
            self.last_autosave_tick = now

    def _recover_autosave(self) -> bool:
        record = self.recovery_record
        if record is None:
            self.recovery_open = False
            return False
        try:
            project = Project.from_dict(record.project_data)
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            self.status = f"Recovery snapshot is invalid: {error}"
            return False
        self._install_project(
            project,
            record.source_path,
            saved=False,
            status="Recovered autosave — save the project to keep it",
        )
        self.recovery_open = False
        self.last_autosave_tick = pygame.time.get_ticks()
        return True

    def _discard_autosave(self) -> None:
        try:
            self.autosave_manager.clear()
        except OSError as error:
            self.status = f"Could not discard recovery snapshot: {error}"
            return
        self.recovery_record = None
        self.recovery_open = False
        self.status = "Discarded autosave recovery"

    def save_project_file(self, path: str | Path) -> None:
        target = Path(path)
        if target.suffix.lower() != ".cnclight":
            target = target.with_suffix(".cnclight")
        self.project.save(target)
        self.project_path = target.resolve()
        self.saved_project_state = deepcopy(self.project.to_dict())
        self.status = f"Saved project: {target.name}"
        self._clear_recovery_snapshots()

    def load_project_file(self, path: str | Path) -> None:
        target = Path(path)
        project = Project.load(target)
        self._clear_recovery_snapshots()
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
        self.export_bank_project_origin_id = None
        self._falloff_preview_project_state = None
        self._falloff_preview_slots = None
        self._falloff_preview_frames = []
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
        self._close_generator_panels()
        self.random_effect_editing = None
        self.random_effect_input_select_all = False
        self.random_effect_drag_start = None
        self.layer_name_editing_id = None
        self.layer_name_input = ""
        self.layer_name_input_select_all = False
        self.export_bank_open = False
        self.help_open = False
        self.property_editing = None
        self.duration_input = f"{self.project.duration_ms / 1000:.1f}"
        self.timeline_zoom = 1.0
        self.timeline_scroll_ms = 0.0
        self.timeline_layer_scroll = 0
        self.inspector_scroll = 0
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

    def _clear_recovery_snapshots(self) -> bool:
        try:
            self.autosave_manager.clear()
        except OSError:
            return False
        self.recovery_record = None
        self.recovery_open = False
        return True

    def _request_confirmation(
        self, title: str, message: str, action: str, payload: object | None = None,
    ) -> None:
        self.confirmation_open = True
        self.confirmation_title = title
        self.confirmation_message = message
        self.confirmation_action = action
        self.confirmation_payload = payload
        self.playing = False
        self.status = f"Confirmation required: {title}"

    def _new_project(self, confirm: bool = True) -> bool:
        if confirm and self._project_is_dirty():
            self._request_confirmation(
                "New project",
                "The current project has unsaved changes. Discard them and start a new project?",
                "new_project",
            )
            return False
        self._install_project(
            Project("Untitled effect"), None, saved=False, status="New blank project",
        )
        self._clear_recovery_snapshots()
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
        default = self.project_path or PROJECT_FILE
        self._open_file_browser(
            title="Save CnC Light project",
            mode="save",
            purpose="project_save",
            initial_directory=default.parent,
            extension=".cnclight",
            filename=default.name,
        )

    def _choose_project_load(self) -> None:
        self._open_file_browser(
            title="Open CnC Light project",
            mode="open",
            purpose="project_load",
            initial_directory=self.project_path.parent if self.project_path else PROJECT_FILE.parent,
            extension=".cnclight",
        )

    def _choose_effect_data(self) -> None:
        initial = Path(r"F:\Projects\cheech and chong\firmware\CnC_firmware4")
        if self.effect_data_path:
            initial = self.effect_data_path.parent
        self._open_file_browser(
            title="Open firmware effect_data.h",
            mode="open",
            purpose="effect_import",
            initial_directory=initial if initial.exists() else ROOT,
            extension=".h",
        )

    def _open_file_browser(
        self,
        *,
        title: str,
        mode: str,
        purpose: str,
        initial_directory: str | Path,
        extension: str,
        filename: str = "",
    ) -> None:
        directory = Path(initial_directory).expanduser()
        if directory.is_file():
            directory = directory.parent
        if not directory.is_dir():
            directory = ROOT if ROOT.is_dir() else Path.cwd()
        try:
            directory = directory.resolve()
        except OSError:
            directory = Path.cwd()
        if self._try_native_file_dialog(
            title=title, mode=mode, purpose=purpose,
            directory=directory, extension=extension, filename=filename,
        ):
            return
        self.file_browser_open = True
        self.file_browser_mode = mode
        self.file_browser_purpose = purpose
        self.file_browser_title = title
        self.file_browser_directory = directory
        self.file_browser_extension = extension.lower()
        self.file_browser_filename = filename
        self.file_browser_path_input = str(directory)
        self.file_browser_selected = -1
        self.file_browser_scroll = 0
        self.file_browser_path_editing = False
        self.file_browser_filename_editing = mode == "save"
        self.file_browser_input_select_all = mode == "save"
        self.file_browser_status = (
            f"Showing folders and *{extension} files" if extension else "Choose a file"
        )
        self.playing = False

    def _try_native_file_dialog(
        self,
        *,
        title: str,
        mode: str,
        purpose: str,
        directory: Path,
        extension: str,
        filename: str = "",
    ) -> bool:
        """On a real (non-headless) Windows session, use the native Explorer
        picker instead of the in-app browser. Returns True once handled
        (accepted or cancelled) - the caller should not also open the
        in-app browser. Returns False to fall back to it: not Windows,
        no real display (tests/--smoke-test force SDL_VIDEODRIVER=dummy),
        or tkinter isn't available (not guaranteed on every Python build,
        e.g. Raspberry Pi OS - though the Pi never reaches this branch
        anyway since it isn't Windows).
        """
        if os.name != "nt" or os.environ.get("SDL_VIDEODRIVER") == "dummy":
            return False
        try:
            import tkinter
            from tkinter import filedialog
        except ImportError:
            return False
        label = extension.lstrip(".").upper() or "All"
        file_types = [(f"{label} files", f"*{extension}")] if extension else []
        file_types.append(("All files", "*.*"))
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            if mode == "save":
                chosen = filedialog.asksaveasfilename(
                    parent=root, title=title, initialdir=str(directory),
                    initialfile=filename, defaultextension=extension,
                    filetypes=file_types,
                )
            else:
                chosen = filedialog.askopenfilename(
                    parent=root, title=title, initialdir=str(directory), filetypes=file_types,
                )
        finally:
            root.destroy()
        if not chosen:
            if purpose.startswith("bank_"):
                self.export_bank_status = "File selection cancelled"
            else:
                self.status = "File selection cancelled"
            return True
        # asksaveasfilename already asked "replace existing file?" natively
        # when applicable, so _complete_file_browser_path can write directly.
        self._complete_file_browser_path(purpose, Path(chosen))
        return True

    def _file_browser_entries(self) -> list[Path]:
        try:
            children = list(self.file_browser_directory.iterdir())
        except OSError as error:
            self.file_browser_status = f"Cannot read folder: {error}"
            return []
        extension = self.file_browser_extension
        visible = [
            child for child in children
            if child.is_dir() or not extension or child.suffix.lower() == extension
        ]
        return sorted(
            visible,
            key=lambda child: (not child.is_dir(), child.name.casefold()),
        )

    def _file_browser_locations(self) -> list[tuple[str, Path]]:
        candidates = [
            ("Home", Path.home()),
            ("Project", ROOT),
            ("Projects", PROJECT_FILE.parent),
            ("Exports", EXPORT_FILE.parent),
            ("Root", Path(self.file_browser_directory.anchor or os.sep)),
        ]
        if os.name == "nt":
            candidates.extend(
                (f"{chr(code)}:", Path(f"{chr(code)}:\\"))
                for code in range(ord("A"), ord("Z") + 1)
            )
        locations: list[tuple[str, Path]] = []
        seen: set[str] = set()
        for label, path in candidates:
            try:
                resolved = path.expanduser().resolve()
            except OSError:
                continue
            key = os.path.normcase(str(resolved))
            if key in seen or not resolved.is_dir():
                continue
            seen.add(key)
            locations.append((label, resolved))
        return locations

    def _navigate_file_browser(self, directory: str | Path) -> bool:
        target = Path(directory).expanduser()
        try:
            target = target.resolve()
        except OSError as error:
            self.file_browser_status = f"Invalid path: {error}"
            return False
        if not target.is_dir():
            self.file_browser_status = "That path is not an accessible folder"
            return False
        self.file_browser_directory = target
        self.file_browser_path_input = str(target)
        self.file_browser_selected = -1
        self.file_browser_scroll = 0
        self.file_browser_path_editing = False
        self.file_browser_status = f"Opened {target.name or target}"
        return True

    def _cancel_file_browser(self) -> None:
        purpose = self.file_browser_purpose
        self.file_browser_open = False
        self.file_browser_path_editing = False
        self.file_browser_filename_editing = False
        if purpose.startswith("bank_"):
            self.export_bank_status = "File selection cancelled"
        else:
            self.status = "File selection cancelled"

    def _selected_file_browser_path(self) -> Path | None:
        entries = self._file_browser_entries()
        if 0 <= self.file_browser_selected < len(entries):
            return entries[self.file_browser_selected]
        return None

    def _activate_file_browser_selection(self, *, accept_file: bool = True) -> None:
        selected = self._selected_file_browser_path()
        if selected is None:
            if self.file_browser_mode == "save" and accept_file:
                self._accept_file_browser()
            return
        if selected.is_dir():
            self._navigate_file_browser(selected)
            return
        self.file_browser_filename = selected.name
        if self.file_browser_mode == "open" and accept_file:
            self._accept_file_browser()

    def _file_browser_target(self) -> Path | None:
        if self.file_browser_mode == "open":
            selected = self._selected_file_browser_path()
            if selected is None or not selected.is_file():
                self.file_browser_status = "Select a file first"
                return None
            return selected
        filename = self.file_browser_filename.strip()
        if not filename:
            self.file_browser_status = "Enter a file name"
            return None
        target = self.file_browser_directory / filename
        if self.file_browser_extension and target.suffix.lower() != self.file_browser_extension:
            target = target.with_suffix(self.file_browser_extension)
        return target

    def _accept_file_browser(self, *, overwrite_confirmed: bool = False) -> bool:
        target = self._file_browser_target()
        if target is None:
            return False
        if (
            self.file_browser_mode == "save"
            and target.exists()
            and not overwrite_confirmed
        ):
            purpose = self.file_browser_purpose
            self.file_browser_open = False
            self._request_confirmation(
                "Replace existing file?",
                f"{target.name} already exists. Replace it?",
                "complete_file_browser",
                (purpose, target),
            )
            return False
        return self._complete_file_browser_path(self.file_browser_purpose, target)

    def _complete_file_browser_path(self, purpose: str, target: Path) -> bool:
        try:
            if purpose == "project_save":
                self.save_project_file(target)
            elif purpose == "project_load":
                if self._project_is_dirty():
                    self.file_browser_open = False
                    self._request_confirmation(
                        "Unsaved project",
                        "The current project has unsaved changes. Open another project anyway?",
                        "load_project",
                        target,
                    )
                    return False
                self.load_project_file(target)
            elif purpose == "effect_import":
                self.load_effect_data_file(target)
            elif purpose == "bank_map":
                self.map_effect_bank_file(target)
            elif purpose == "bank_export":
                self.export_effect_bank_file(target)
            elif purpose == "bank_convert":
                self.convert_effect_bank_file(target)
            else:
                raise ValueError(f"Unknown file operation: {purpose}")
        except (OSError, TypeError, ValueError, KeyError) as error:
            self.file_browser_open = True
            self.file_browser_status = f"Operation failed: {error}"
            if purpose.startswith("bank_"):
                self.export_bank_status = self.file_browser_status
            else:
                self.status = self.file_browser_status
            return False
        self.file_browser_open = False
        self.file_browser_path_editing = False
        self.file_browser_filename_editing = False
        return True

    def _handle_file_browser_event(self, event: pygame.event.Event) -> None:
        entries = self._file_browser_entries()
        if event.type == pygame.KEYDOWN:
            if self.file_browser_path_editing or self.file_browser_filename_editing:
                self._handle_file_browser_text_input(event)
                return
            if event.key == pygame.K_ESCAPE:
                self._cancel_file_browser()
            elif event.key == pygame.K_BACKSPACE:
                self._navigate_file_browser(self.file_browser_directory.parent)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._activate_file_browser_selection()
            elif event.key in (pygame.K_UP, pygame.K_DOWN):
                delta = -1 if event.key == pygame.K_UP else 1
                self.file_browser_selected = max(
                    0, min(max(0, len(entries) - 1), self.file_browser_selected + delta)
                )
            return
        if event.type == pygame.MOUSEWHEEL:
            panel = self._file_browser_panel_rect()
            list_rect = self._file_browser_list_rect(panel)
            mouse = getattr(event, "pos", pygame.mouse.get_pos())
            if list_rect.collidepoint(mouse):
                visible = max(1, list_rect.height // 38)
                maximum = max(0, len(entries) - visible)
                self.file_browser_scroll = max(
                    0, min(maximum, self.file_browser_scroll - event.y * 3)
                )
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        action = next((
            action for rect, action, _label in reversed(self.buttons)
            if action.startswith("file_browser_") and rect.collidepoint(event.pos)
        ), None)
        if not action:
            return
        if action == "file_browser_cancel":
            self._cancel_file_browser()
        elif action == "file_browser_accept":
            self._accept_file_browser()
        elif action == "file_browser_up":
            self._navigate_file_browser(self.file_browser_directory.parent)
        elif action == "file_browser_path":
            self.file_browser_path_editing = True
            self.file_browser_filename_editing = False
            self.file_browser_path_input = str(self.file_browser_directory)
            self.file_browser_input_select_all = True
        elif action == "file_browser_filename":
            self.file_browser_filename_editing = True
            self.file_browser_path_editing = False
            self.file_browser_input_select_all = True
        elif action.startswith("file_browser_location:"):
            index = int(action.rsplit(":", 1)[1])
            locations = self._file_browser_locations()
            if 0 <= index < len(locations):
                self._navigate_file_browser(locations[index][1])
        elif action.startswith("file_browser_entry:"):
            index = int(action.rsplit(":", 1)[1])
            self.file_browser_selected = index
            selected = self._selected_file_browser_path()
            if selected and selected.is_dir():
                self._navigate_file_browser(selected)
            elif selected:
                self.file_browser_filename = selected.name
                if getattr(event, "clicks", 1) >= 2:
                    self._activate_file_browser_selection()

    def _handle_file_browser_text_input(self, event: pygame.event.Event) -> None:
        path_field = self.file_browser_path_editing
        value = self.file_browser_path_input if path_field else self.file_browser_filename
        ctrl = bool(getattr(event, "mod", pygame.key.get_mods()) & pygame.KMOD_CTRL)
        if ctrl and event.key == pygame.K_a:
            self.file_browser_input_select_all = True
            return
        if ctrl and event.key == pygame.K_v:
            pasted = self._clipboard_text().replace("\r", "").replace("\n", "")
            value = pasted if self.file_browser_input_select_all else value + pasted
            self.file_browser_input_select_all = False
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if path_field:
                self._navigate_file_browser(value)
            else:
                self.file_browser_filename_editing = False
                self._accept_file_browser()
            return
        elif event.key == pygame.K_ESCAPE:
            self.file_browser_path_editing = False
            self.file_browser_filename_editing = False
            self.file_browser_input_select_all = False
            return
        elif event.key == pygame.K_BACKSPACE:
            value = "" if self.file_browser_input_select_all else value[:-1]
            self.file_browser_input_select_all = False
        else:
            character = getattr(event, "unicode", "")
            if character.isprintable():
                value = character if self.file_browser_input_select_all else value + character
                self.file_browser_input_select_all = False
        if path_field:
            self.file_browser_path_input = value[:500]
        else:
            self.file_browser_filename = value[:160]

    def _handle_confirmation_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_n):
                self._resolve_confirmation(False)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_y):
                self._resolve_confirmation(True)
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        action = next((
            action for rect, action, _label in reversed(self.buttons)
            if action.startswith("confirmation_") and rect.collidepoint(event.pos)
        ), None)
        if action == "confirmation_yes":
            self._resolve_confirmation(True)
        elif action == "confirmation_no":
            self._resolve_confirmation(False)

    def _resolve_confirmation(self, accepted: bool) -> None:
        action = self.confirmation_action
        payload = self.confirmation_payload
        self.confirmation_open = False
        self.confirmation_action = ""
        self.confirmation_payload = None
        if not accepted:
            self.status = "Operation cancelled — current project kept"
            if action == "complete_file_browser" and isinstance(payload, tuple):
                purpose, target = payload
                self._open_file_browser(
                    title=self.file_browser_title,
                    mode="save",
                    purpose=str(purpose),
                    initial_directory=Path(target).parent,
                    extension=Path(target).suffix,
                    filename=Path(target).name,
                )
                self.file_browser_status = "Choose another name or cancel"
            return
        if action == "new_project":
            self._new_project(confirm=False)
        elif action == "exit_application":
            self._maybe_autosave(force=True)
            self.exit_requested = True
        elif action == "load_project" and isinstance(payload, Path):
            try:
                self.load_project_file(payload)
            except (OSError, TypeError, ValueError, KeyError) as error:
                self.status = f"Project load failed: {error}"
        elif action == "load_bank_project" and isinstance(payload, int):
            self.export_bank_selected = payload
            self._load_export_bank_project(confirm=False)
        elif action == "save_mapped_bank" and isinstance(payload, Path):
            self.save_mapped_effect_bank(confirm_replacement=False)
        elif action == "complete_file_browser" and isinstance(payload, tuple):
            purpose, target = payload
            self._complete_file_browser_path(str(purpose), Path(target))

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
        self._suggest_effect_id_if_still_default()

    def map_effect_bank_file(self, path: str | Path) -> None:
        target = Path(path)
        effects = load_effect_data(target)
        self.export_bank_compressed = bool(re.search(r"#define\s+FX_FRAME_CODEC\s+1\b", target.read_text(encoding="utf-8")))
        self._packed_size_cache = {}
        self.export_bank_effects = deepcopy(effects)
        self.export_bank_path = target.resolve()
        self.export_bank_selected = 0 if effects else -1
        self.export_bank_scroll = 0
        self.export_bank_status = f"Mapped {len(effects)} effects from {target.name}"
        self._suggest_effect_id_if_still_default()
        # Remembered across restarts (see _load_remembered_effect_bank_header)
        # so mapping the same firmware header is a one-time action.
        self.ui_settings["last_effect_bank_header"] = str(self.export_bank_path)
        try:
            self.ui_settings_store.save(self.ui_settings)
        except OSError:
            pass

    def _load_remembered_effect_bank_header(self) -> None:
        remembered = self.ui_settings.get("last_effect_bank_header")
        if not remembered:
            return
        try:
            self.map_effect_bank_file(remembered)
        except Exception:
            # Moved/deleted/corrupt since last time. This is a best-effort
            # startup convenience - deliberately broad so a stale remembered
            # path can never prevent the editor from launching.
            self.export_bank_status = f"Could not re-map {remembered} — pick it again from Map header…"

    def _export_bank_backup_path(self, path: str | Path) -> Path:
        target = Path(path)
        return target.with_name(target.name + ".bak")

    def export_effect_bank_file(self, path: str | Path) -> Path:
        led_errors = self.led_map.validate()
        if led_errors:
            raise ValueError("LED map: " + "; ".join(led_errors))
        current = project_to_imported_effect(self.project, self.led_map.export_slots())
        # resolve_effects replaces a bank entry that already carries the current
        # project's firmware ID instead of appending a duplicate (which the
        # exporter would otherwise reject at write time).
        combined = resolve_effects(deepcopy(self.export_bank_effects), current)
        changes = describe_changes(self.export_bank_effects, combined)
        target = Path(path)
        backed_up = False
        if target.is_file():
            # A single rolling backup of whatever we're about to overwrite -
            # not a growing history, just "undo my last export". Overwrites
            # any previous backup on purpose (see restore_export_bank_backup).
            shutil.copy2(target, self._export_bank_backup_path(target))
            backed_up = True
        # Saving back to an RLE-mapped header must preserve its storage format.
        # Previously this path silently fell back to the raw exporter, so a
        # perfectly valid compressed bank was rejected by the old raw 150 KiB
        # limit as soon as one effect was added or changed.
        compressed = self._bank_is_compressed()
        destination = export_effect_bank(
            combined,
            path,
            max_bytes=EFFECT_BANK_CAPACITY,
            compressed=compressed,
        )
        self.export_bank_compressed = compressed
        self._packed_size_cache = {}
        self.export_bank_path = destination.resolve()
        # The file on disk is now the source of truth.  Keep every editor view
        # in sync immediately so saving never requires a manual re-map just to
        # see the effect that was written.
        self.export_bank_effects = deepcopy(combined)
        self.imported_effects = deepcopy(combined)
        self.effect_data_path = self.export_bank_path
        self.export_bank_project_origin_id = self.project.effect_id
        self._effect_thumbnail_cache.clear()
        self.ui_settings["last_effect_bank_header"] = str(self.export_bank_path)
        try:
            self.ui_settings_store.save(self.ui_settings)
        except OSError:
            pass
        change_summary = "; ".join(change.description for change in changes)
        self.export_bank_status = (
            f"Exported {len(combined)} effects — "
            f"{self._export_bank_used_bytes() / 1024:.1f} KiB"
            + (f" — {change_summary}" if change_summary else "")
            + (" — previous version backed up" if backed_up else "")
        )
        self.status = f"Effect bank exported: {destination.name}"
        return destination

    def save_mapped_effect_bank(self, *, confirm_replacement: bool = True) -> bool:
        """Save straight back to the mapped header, with collision protection."""
        if self.export_bank_path is None:
            self._choose_export_bank_save()
            return False
        try:
            current = project_to_imported_effect(self.project, self.led_map.export_slots())
            planned = resolve_effects(deepcopy(self.export_bank_effects), current)
            replacements = [
                change for change in describe_changes(self.export_bank_effects, planned)
                if change.kind == "REPLACE"
            ]
        except (TypeError, ValueError) as error:
            self.export_bank_status = f"Save blocked: {error}"
            return False
        owns_slot = self.export_bank_project_origin_id == self.project.effect_id
        if confirm_replacement and replacements and not owns_slot:
            existing = next(
                effect for effect in self.export_bank_effects
                if effect.effect_id == self.project.effect_id
            )
            self._request_confirmation(
                "Replace bank effect?",
                f'ID {existing.effect_id} is "{existing.name}". Replace it with "{self.project.name}"?',
                "save_mapped_bank",
                self.export_bank_path,
            )
            self.export_bank_status = "Confirmation required before replacing an existing effect"
            return False
        try:
            self.export_effect_bank_file(self.export_bank_path)
        except (OSError, TypeError, ValueError, KeyError) as error:
            self.export_bank_status = f"Save failed: {error}"
            return False
        return True

    def restore_export_bank_backup(self) -> bool:
        """Swap the mapped header and its rolling backup, then reload it.

        Restoring only the in-memory list was misleading: the firmware file
        stayed overwritten, and the next save could silently apply the same
        current project again.  A restore is now an immediate on-disk undo;
        the displaced version becomes the new backup, so the button can also
        redo the swap if it was clicked by mistake.
        """
        target = self.export_bank_path or EXPORT_FILE
        backup_path = self._export_bank_backup_path(target)
        if not backup_path.is_file():
            self.export_bank_status = "No backup available to restore"
            return False
        temporary = target.with_name(f".{target.name}.restore.tmp")
        try:
            if target.is_file():
                shutil.copy2(target, temporary)
            shutil.copy2(backup_path, target)
            if temporary.is_file():
                temporary.replace(backup_path)
            source = target.read_text(encoding="utf-8")
            effects = load_effect_data(target)
        except (OSError, TypeError, ValueError, KeyError) as error:
            if temporary.is_file():
                temporary.unlink()
            self.export_bank_status = f"Backup restore failed: {error}"
            return False
        self.export_bank_effects = deepcopy(effects)
        self.export_bank_compressed = bool(
            re.search(r"#define\s+FX_FRAME_CODEC\s+1\b", source)
        )
        self._packed_size_cache = {}
        self.imported_effects = deepcopy(effects)
        self.effect_data_path = Path(target).resolve()
        self.export_bank_project_origin_id = None
        self.export_bank_selected = 0 if effects else -1
        self.export_bank_scroll = 0
        self.export_bank_status = (
            f"Restored backup to {Path(target).name} ({len(effects)} effects) — "
            "the displaced version is now the backup"
        )
        self._suggest_effect_id_if_still_default()
        return True

    def _choose_export_bank_map(self) -> None:
        self._open_file_browser(
            title="Map firmware effect_data.h",
            mode="open",
            purpose="bank_map",
            initial_directory=self.export_bank_path.parent if self.export_bank_path else ROOT,
            extension=".h",
        )

    def convert_effect_bank_file(self, path: str | Path) -> Path:
        """Convert the mapped bank exactly, without replacing entries from the canvas."""
        from .frame_codec import pack_frames
        effects = deepcopy(self.export_bank_effects)
        if not effects:
            raise ValueError("Map an effect bank before converting")
        target = Path(path)
        if target.is_file():
            shutil.copy2(target, self._export_bank_backup_path(target))
        destination = export_effect_bank(effects, target, compressed=True)
        restored = load_effect_data(destination)
        if any(a.frames != b.frames for a, b in zip(effects, restored)) or len(effects) != len(restored):
            raise ValueError("Compressed bank verification failed")
        raw = sum(e.flash_bytes for e in effects)
        packed = sum(len(pack_frames(e.frames)) for e in effects)
        self.export_bank_status = f"Converted {len(effects)} effects: {raw / 1024:.1f} -> {packed / 1024:.1f} KiB — {destination.name}"
        self.status = self.export_bank_status
        return destination

    def _choose_export_bank_convert(self) -> None:
        if not self.export_bank_effects:
            self.export_bank_status = "Map an effect bank before converting"
            return
        default = self.export_bank_path or EXPORT_FILE
        self._open_file_browser(
            title="Convert entire mapped bank to compressed RGB (RLE)",
            mode="save", purpose="bank_convert", initial_directory=default.parent,
            extension=".h", filename="effect_data.h",
        )

    def _choose_export_bank_save(self) -> None:
        default = self.export_bank_path or EXPORT_FILE
        self._open_file_browser(
            title="Export V4 effect bank",
            mode="save",
            purpose="bank_export",
            initial_directory=default.parent,
            extension=".h",
            filename=default.name,
        )

    def _bank_is_compressed(self) -> bool:
        return getattr(self, "export_bank_compressed", False)

    def _bank_effect_bytes(self, effect) -> int:
        if not self._bank_is_compressed():
            return effect.flash_bytes
        from .frame_codec import pack_frames
        cache = getattr(self, "_packed_size_cache", {})
        key = id(effect)
        if key not in cache or cache[key][0] is not effect:
            cache[key] = (effect, len(pack_frames(effect.frames)))
        self._packed_size_cache = cache
        return cache[key][1]

    def _export_bank_used_bytes(self) -> int:
        if self._bank_is_compressed():
            return sum(self._bank_effect_bytes(e) for e in self.export_bank_effects)
        # Mirrors resolve_effects' replace-by-id semantics without baking the
        # project's frames: a bank entry sharing the current effect ID gets
        # replaced on export, not added on top of, so its bytes are excluded
        # from the total instead of double-counted.
        replaced_bytes = sum(
            effect.flash_bytes for effect in self.export_bank_effects
            if effect.effect_id == self.project.effect_id
        )
        return (
            self.project.flash_bytes
            + sum(effect.flash_bytes for effect in self.export_bank_effects)
            - replaced_bytes
        )

    def _export_bank_validation_errors(self) -> list[str]:
        # Only flag IDs that repeat WITHIN the bank itself - that is genuinely
        # ambiguous. The current project sharing an ID with one bank entry is
        # not an error: export_effect_bank_file() replaces that slot instead
        # of appending a duplicate (see resolve_effects in effect_bank.py).
        bank_ids = [effect.effect_id for effect in self.export_bank_effects]
        duplicates = sorted({effect_id for effect_id in bank_ids if bank_ids.count(effect_id) > 1})
        errors = [f"Duplicate ID {effect_id} in bank" for effect_id in duplicates]
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

    def _export_bank_visible_pairs(self) -> list[tuple[int, ImportedEffect]]:
        """(real index into export_bank_effects, effect), filtered by the search
        query and ordered by the current sort mode. The pinned "current project"
        row is handled separately by callers - this only covers mapped/bank
        effects, since searching/sorting the project you're actively editing
        isn't useful (it's always shown last, after the mapped bank).

        Effect IDs are unique within export_bank_effects (enforced by
        _export_bank_validation_errors and the ID-edit commit path), so
        matching sorted effects back to their original index by ID is safe.
        """
        query = self.export_bank_query.strip()
        candidates = self.export_bank_effects
        if query:
            candidates = [effect for effect in candidates if effect_matches(effect, query)]
        ordered = sort_effects(candidates, self.export_bank_sort_mode)
        index_by_id = {effect.effect_id: index for index, effect in enumerate(self.export_bank_effects)}
        return [(index_by_id[effect.effect_id], effect) for effect in ordered]

    def _export_bank_color_index(self, entry_index: int) -> int:
        """Colour-block position for an entry (real bank index, or -1 for the
        current project) - shared by the memory bar, the Bank Content list,
        and the Selected Effect badge so the same effect always gets the
        same colour. The current project is pinned last (see _draw_export_bank),
        so its slot is simply one past the bank's own index range."""
        return len(self.export_bank_effects) if entry_index == -1 else entry_index

    def _render_led_thumbnail(self, colors: list[tuple[int, int, int]], size: int) -> pygame.Surface:
        """A tiny spatial preview: each LED's colour drawn at its own
        playfield position (from the LED map), scaled into a size×size
        square - a rough silhouette of the actual light pattern."""
        surface = pygame.Surface((size, size), pygame.SRCALPHA)
        points = self.led_map.normalized_points(firmware_order=True)
        radius = max(1, size // 20)
        for point, color in zip(points, colors):
            x = round(point[0] * size)
            y = round(point[1] * size)
            pygame.draw.circle(surface, color, (x, y), radius)
        return surface

    def _effect_preview_frame_index(self, effect: ImportedEffect) -> int:
        """The frame with the most non-transparent brightness - usually the
        most recognizable moment of the effect (frame 0 of an overlay is
        often mostly transparent, which makes a poor thumbnail)."""
        if not effect.frames:
            return 0

        def brightness(frame: list[tuple[int, int, int]]) -> int:
            return sum(
                r + g + b for r, g, b in frame
                if not (effect.overlay and (r, g, b) == TRANSPARENT_SENTINEL)
            )

        return max(range(len(effect.frames)), key=lambda index: brightness(effect.frames[index]))

    def _effect_thumbnail(self, effect: ImportedEffect, size: int) -> pygame.Surface:
        key = (id(effect.frames), effect.effect_id, size)
        cached = self._effect_thumbnail_cache.get(key)
        if cached is not None:
            return cached
        if effect.frames:
            frame = effect.frames[self._effect_preview_frame_index(effect)]
            colors = [
                (0, 0, 0) if effect.overlay and color == TRANSPARENT_SENTINEL else color
                for color in frame
            ]
        else:
            colors = [(0, 0, 0)] * len(self.led_map.leds)
        surface = self._render_led_thumbnail(colors, size)
        self._effect_thumbnail_cache[key] = surface
        return surface

    def _suggest_effect_id_if_still_default(self) -> None:
        """A brand-new project starts at the class default firmware ID (1).
        Once a bank is mapped, ID 1 often already belongs to whatever
        effect was exported first there, so exporting would silently
        REPLACE it (see resolve_effects) instead of adding a new entry -
        confusing for a project the user hasn't even assigned an ID to
        yet. Bump to the next free ID once, but only on an actual
        collision: if ID 1 is free in the bank, there is nothing to fix,
        and an ID the user explicitly types is never overridden either."""
        if self.project.effect_id != 1:
            return
        used_ids = [effect.effect_id for effect in self.export_bank_effects]
        if 1 not in used_ids:
            return
        self.project.effect_id = max(used_ids, default=0) + 1

    def _load_export_bank_project(self, confirm: bool = True) -> bool:
        effect = self._export_bank_selected_effect()
        if effect is None or effect.project_data is None:
            self.export_bank_status = "Selected effect has no embedded CnC Light project"
            return False
        if confirm and self._project_is_dirty():
            self._request_confirmation(
                "Load embedded project",
                f"Discard unsaved changes and load the embedded project for {effect.name}?",
                "load_bank_project",
                self.export_bank_selected,
            )
            return False
        try:
            project = Project.from_dict(effect.project_data)
            project.name = effect.name
            project.effect_id = effect.effect_id
        except (TypeError, ValueError, KeyError) as error:
            self.export_bank_status = f"Embedded project is invalid: {error}"
            return False
        self.autosave_manager.clear()
        self.recovery_record = None
        self.recovery_open = False
        self._install_project(
            project,
            None,
            saved=False,
            status=f"Loaded embedded project: {effect.name} · ID {effect.effect_id}",
        )
        self.export_bank_project_origin_id = effect.effect_id
        return True

    def _handle_export_bank_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if self.export_bank_name_editing or self.export_bank_id_editing:
                self._handle_export_bank_input(event)
            elif self.export_bank_query_editing:
                self._handle_export_bank_query_input(event)
            elif (
                event.key == pygame.K_s
                and getattr(event, "mod", pygame.key.get_mods()) & pygame.KMOD_CTRL
            ):
                if getattr(event, "mod", pygame.key.get_mods()) & pygame.KMOD_SHIFT:
                    self._choose_export_bank_save()
                else:
                    self.save_mapped_effect_bank()
            elif event.key == pygame.K_ESCAPE:
                self.export_bank_open = False
                self.status = "Closed Effect Bank"
            return
        if event.type == pygame.MOUSEWHEEL:
            _toolbar, list_rect, _detail = self._export_bank_layout()
            mouse = getattr(event, "pos", pygame.mouse.get_pos())
            if list_rect.collidepoint(mouse):
                visible = max(1, list_rect.height // 53)
                maximum = max(0, len(self._export_bank_visible_pairs()) + 1 - visible)
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
        if action != "export_bank_query_focus":
            self.export_bank_query_editing = False
        if action == "export_bank_sort_cycle":
            index = SORT_MODES.index(self.export_bank_sort_mode)
            self.export_bank_sort_mode = SORT_MODES[(index + 1) % len(SORT_MODES)]
            self.export_bank_scroll = 0
            return
        if action == "export_bank_query_focus":
            self.export_bank_query_editing = True
            self.export_bank_name_editing = False
            self.export_bank_id_editing = False
            return
        if action == "export_bank_query_clear":
            self.export_bank_query = ""
            self.export_bank_scroll = 0
            return
        if action == "export_bank_close":
            self.export_bank_open = False
            self.status = "Closed Effect Bank"
        elif action == "export_bank_restore_backup":
            self.restore_export_bank_backup()
        elif action == "export_bank_map":
            self._choose_export_bank_map()
        elif action == "export_bank_write":
            errors = self._export_bank_validation_errors()
            if errors:
                self.export_bank_status = "Export blocked: " + " · ".join(errors)
            else:
                self.save_mapped_effect_bank()
        elif action == "export_bank_write_as":
            errors = self._export_bank_validation_errors()
            if errors:
                self.export_bank_status = "Export blocked: " + " · ".join(errors)
            else:
                self._choose_export_bank_save()
        elif action == "export_bank_convert":
            self._choose_export_bank_convert()
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

    def _handle_export_bank_query_input(self, event: pygame.event.Event) -> None:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
            self.export_bank_query_editing = False
            return
        if event.key == pygame.K_BACKSPACE:
            self.export_bank_query = self.export_bank_query[:-1]
            self.export_bank_scroll = 0
            return
        character = getattr(event, "unicode", "")
        if character.isprintable():
            self.export_bank_query = (self.export_bank_query + character)[:40]
            self.export_bank_scroll = 0

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
        self._begin_led_change()
        self.led_move_origin = (led.x, led.y)
        self.drag_mode = "led_move"
        self.status = f"Moving LED {led.firmware_index} — click to place, Esc to cancel"

    def _finish_led_move(self) -> None:
        led = self._current_led()
        self.drag_mode = None
        self.led_move_origin = None
        self.led_move_ready_id = led.id
        self.led_map.mapping_status = "calibration_in_progress"
        self._commit_led_change()
        self.status = f"LED {led.firmware_index} placed — save the LED map"

    def _cancel_led_move(self) -> None:
        led = self._current_led()
        if self.led_move_origin is not None:
            led.x, led.y = self.led_move_origin
            self.led_points = self.led_map.normalized_points()
        self.drag_mode = None
        self.led_move_origin = None
        self.led_move_ready_id = led.id
        self.led_map_change_snapshot = None  # reverted in place - nothing to undo
        self.status = f"LED {led.firmware_index} movement cancelled"

    def _add_led(self) -> None:
        if len(self.led_map.leds) >= 68:
            self.status = "The firmware output is limited to 68 LED slots"
            return
        self._begin_led_change()
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
        self._commit_led_change()
        self.status = f"Added LED {new_index} in the center — click it to move"

    def _delete_led(self) -> None:
        if len(self.led_map.leds) <= 1:
            self.status = "The LED map must keep at least one position"
            return
        self._begin_led_change()
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
        self._commit_led_change()
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
            self._begin_led_change()
            self.led_map.set_firmware_index(led.id, target, swap=True)
            self.led_map.mapping_status = "calibration_in_progress"
            self.led_id_editing = False
            self._sync_led_fields()
            self._commit_led_change()
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
            self._begin_led_change()
            self._current_led().name = name
            self.led_name_editing = False
            self.led_map.mapping_status = "calibration_in_progress"
            self._commit_led_change()
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
            spec = SHAPE_PROPERTY_SPECS["stroke_width"]
            low, high = spec.input_limits()
            if not low <= percent <= high:
                self.status = "Stroke width must be between 0.1 and 5.0"
                return
            self._begin_change()
            self._set_animated("stroke_width", spec.from_input(percent))
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
        kind = self._active_generator_panel_kind()
        effect = self._active_generator_effect(kind) if kind else None
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
            spec = self._active_generator_specs()[prop]
            low, high = spec.input_limits()
            if (low is not None and entered < low) or (high is not None and entered > high):
                self.status = (
                    f"{self._random_effect_parameter_label(prop)} must be {low:g}–{high:g}"
                )
                return
            value = spec.from_input(entered)
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
        layer_property = prop == "layer_opacity"
        if not prop or (not layer_property and not self.selected):
            self.property_editing = None
            return
        label = TIMELINE_PROPERTY_LABELS[prop]
        ctrl = bool(getattr(event, "mod", pygame.key.get_mods()) & pygame.KMOD_CTRL)
        if ctrl and event.key == pygame.K_a:
            self.property_input_select_all = True
            self.status = f"{label} value selected"
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
            self.status = f"Pasted {label} — press Enter"
            return
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            try:
                entered = float(self.property_input.replace(",", "."))
            except ValueError:
                self.status = f"{label} must be a number"
                return
            spec = (
                LAYER_PROPERTY_SPECS[prop]
                if layer_property else SHAPE_PROPERTY_SPECS[prop]
            )
            low, high = spec.input_limits()
            if (low is not None and entered < low) or (high is not None and entered > high):
                self.status = f"{label} must be {low:g}–{high:g}"
                return
            value = spec.from_input(entered)
            self._begin_change()
            if layer_property:
                self.project.layers[self.active_layer].opacity = float(value)
            elif prop == "rotation":
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
            if layer_property:
                self.status = f"Layer opacity: {spec.display_text(value)}"
            elif prop in {"rotation", "rotation_turns"}:
                state = self.selected.state_at(self.current_ms)
                self.status = f"Rotation: {state['rotation']:.1f}° ×{state['rotation_turns']:.0f}"
            else:
                self.status = f"{label}: {spec.display_text(value)}"
            return
        if event.key == pygame.K_ESCAPE:
            self.property_editing = None
            self.property_input_select_all = False
            self.status = f"{label} edit cancelled"
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
        pygame.draw.rect(self.screen, (11, 13, 17), canvas.move(5, 6), border_radius=3)
        pygame.draw.rect(self.screen, (16, 18, 23), canvas, border_radius=2)
        if self.stencil:
            self._draw_leds(canvas, stencil_back=True)
            self._draw_view_surface("artwork", canvas)
        else:
            self._draw_view_surface("guide", canvas)
            self._draw_shapes(canvas)
            self._draw_selection(canvas)
            self._draw_leds(canvas)
        pygame.draw.rect(self.screen, ACCENT if self.stencil else (89, 96, 112), canvas, 1)
        self._draw_viewport_badge(viewport)
        self.screen.set_clip(old_clip)

        self._draw_timeline(timeline)
        self._draw_panel(panel)
        if self.generator_menu_open:
            self._draw_generator_menu()
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
        if self.recovery_open:
            self._draw_recovery_dialog()
        if self.file_browser_open:
            self._draw_file_browser()
        if self.color_picker_open:
            self._draw_color_picker()
        if self.confirmation_open:
            self._draw_confirmation_dialog()
        self._draw_hover_tooltip()

    def _draw_viewport_badge(self, viewport: pygame.Rect) -> None:
        mode = "STENCIL PREVIEW" if self.stencil else "GUIDE VIEW"
        color = (101, 214, 180) if self.stencil else (119, 174, 255)
        label = self.small.render(f"{mode}   ·   {round(self.zoom * 100)}%", True, color)
        badge = label.get_rect(topleft=(viewport.x + 12, viewport.y + 12)).inflate(18, 10)
        fill = pygame.Surface(badge.size, pygame.SRCALPHA)
        fill.fill((18, 21, 28, 222))
        self.screen.blit(fill, badge)
        pygame.draw.rect(self.screen, (*color, 180), badge, 1, border_radius=4)
        self.screen.blit(label, label.get_rect(center=badge.center))

    def _draw_hover_tooltip(self) -> None:
        if any((self.context_menu_pos, self.help_open, self.file_browser_open, self.confirmation_open)):
            return
        mouse = pygame.mouse.get_pos()
        hovered = next(
            ((action, label) for rect, action, label in reversed(self.buttons) if rect.collidepoint(mouse)),
            None,
        )
        if not hovered:
            return
        action, fallback = hovered
        action_base = action.split(":", 1)[0]
        message = ACTION_TOOLTIPS.get(action, ACTION_TOOLTIPS.get(action_base, fallback))
        if not message:
            return
        message = self._fit_text(message, self.small, min(430, self.screen.get_width() - 40))
        text = self.small.render(message, True, (238, 241, 248))
        box = text.get_rect(topleft=(mouse[0] + 16, mouse[1] + 18)).inflate(18, 12)
        if box.right > self.screen.get_width() - 8:
            box.right = self.screen.get_width() - 8
        if box.bottom > self.screen.get_height() - 8:
            box.bottom = mouse[1] - 12
        pygame.draw.rect(self.screen, (14, 16, 21), box, border_radius=5)
        pygame.draw.rect(self.screen, (93, 107, 132), box, 1, border_radius=5)
        self.screen.blit(text, text.get_rect(center=box.center))

    def _draw_view_surface(self, mode: str, canvas: pygame.Rect) -> None:
        """Draw only the visible playfield region instead of scaling its full canvas.

        At 5x zoom the full portrait image can exceed 20 MiB.  Keeping several
        converted copies was enough to exhaust a Pi 3 and could crash SDL/KMSDRM.
        The visible crop is bounded by the workspace, so allocation no longer grows
        with zoom.
        """
        visible = canvas.clip(self.screen.get_clip())
        if visible.width <= 0 or visible.height <= 0:
            return

        source = self.playfield if mode == "artwork" else self.layout_guide
        relative = visible.move(-canvas.x, -canvas.y)
        source_left = math.floor(relative.left * source.get_width() / canvas.width)
        source_top = math.floor(relative.top * source.get_height() / canvas.height)
        source_right = math.ceil(relative.right * source.get_width() / canvas.width)
        source_bottom = math.ceil(relative.bottom * source.get_height() / canvas.height)
        source_rect = pygame.Rect(
            source_left,
            source_top,
            max(1, source_right - source_left),
            max(1, source_bottom - source_top),
        ).clip(source.get_rect())
        if source_rect.width <= 0 or source_rect.height <= 0:
            return

        key = (mode, visible.size, tuple(source_rect))
        cached = self._scaled_view_cache.get(key)
        if cached is None:
            # Release old converted surfaces before allocating the next one.  This
            # avoids a temporary memory spike while the wheel is still moving.
            if len(self._scaled_view_cache) >= 2:
                self._scaled_view_cache.clear()
            # pygame-ce/SDL can SIGBUS on 32-bit ARM when smoothscale reads a
            # subsurface whose pixel pointer is offset inside its parent's buffer.
            # A full-source crop is already contiguous; partial crops need their
            # own aligned storage before entering the native scaler.
            crop = (
                source
                if source_rect == source.get_rect()
                else source.subsurface(source_rect).copy()
            )
            cached = self._scale_surface(crop, visible.size).convert_alpha()
            self._scaled_view_cache[key] = cached
        self.screen.blit(cached, visible)

    def _scale_surface(
        self, source: pygame.Surface, size: tuple[int, int],
    ) -> pygame.Surface:
        """Use the conservative scaler on Raspberry Pi's 32-bit ARM SDL stack."""
        if self.safe_scaling:
            return pygame.transform.scale(source, size)
        return pygame.transform.smoothscale(source, size)

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
        self.screen.blit(
            self.small.render(f"CnC  LIGHT COMPOSER  v{APP_VERSION}", True, (235, 238, 245)),
            (16, 8),
        )
        project_label = self.project_path.name if self.project_path else "Untitled project"
        project_label = project_label if len(project_label) <= 25 else project_label[:22] + "..."
        if self._project_is_dirty():
            project_label += "  •"
        self.screen.blit(self.small.render(project_label, True, (108, 180, 255)), (16, 29))
        items = [
            ("new", "New"),
            ("save", "Save*" if self._project_is_dirty() else "Save"),
            ("load", "Load"),
            ("export", "Bank"),
            None,  # extra gap before Stencil
            ("stencil", "Stencil"),
            None,  # extra gap before LED Map
            ("calibration", "LED Map"),
        ]
        compact = width < 1180
        compact_labels = {
            "new": "New",
            "save": "Save*" if self._project_is_dirty() else "Save",
            "load": "Load",
            "export": "Bank",
            "stencil": "STN",
            "calibration": "LED",
        }
        x = 184 if compact else 238
        for entry in items:
            if entry is None:
                x += 16 if compact else 24
                continue
            action, label = entry
            if compact:
                label = compact_labels[action]
            button_width = max(42 if compact else 58, self.small.size(label)[0] + (12 if compact else 20))
            rect = pygame.Rect(x, 11, button_width, 32)
            active = (action == "stencil" and self.stencil) or (action == "calibration" and self.calibration)
            self._button(rect, action, label, active)
            x += button_width + (4 if compact else 7)

        measured_fps = self.clock.get_fps()
        performance_label = (
            f"{measured_fps:.0f}/{self.target_fps} FPS"
            if measured_fps > 0.5 else f"--/{self.target_fps} FPS"
        )
        show_update_status = self.update_state in {
            "checking", "downloading", "installing", "failed", "restart", "available",
        }
        display_status = self.update_status if show_update_status else performance_label
        update_color = (
            (255, 184, 82) if self.update_state in {"checking", "downloading", "installing", "available"}
            else (255, 111, 99) if self.update_state == "failed"
            else (255, 184, 82) if measured_fps > 0.5 and measured_fps < self.target_fps * 0.8
            else (120, 204, 169)
        )
        update_area = pygame.Rect(width - INSPECTOR_WIDTH + 8, 11, 136, 32)
        update_label = self._fit_text(display_status, self.small, update_area.width)
        update_surface = self.small.render(update_label, True, update_color)
        self.screen.blit(update_surface, update_surface.get_rect(center=update_area.center))

        self._button(pygame.Rect(width - 102, 11, 36, 32), "help", "?", False)
        self._button(pygame.Rect(width - 58, 11, 48, 32), "exit", "Exit", False)

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
        # No standalone "Import" button - effect_data.h import still works via
        # Ctrl+I (see the Hotkeys panel) and the Effect Bank's "Map header…".
        if self.imported_effects:
            imported = self._active_imported_effect()
            self._button(pygame.Rect(10, y + 73, 48, 31), "next_imported_effect", "Next FX", imported is not None)
            self._button(pygame.Rect(10, y + 112, 48, 31), "project_preview", "Project", imported is None)

    def _draw_shapes(self, canvas: pygame.Rect) -> None:
        for layer in self.project.layers:
            if not layer.visible or layer.opacity <= 0.0:
                continue
            layer_opacity = max(0.0, min(1.0, float(layer.opacity)))
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
                    state.get("gradient_type", "solid") in {"linear", "radial", "noise"}
                    and len(state.get("gradient_stops", [])) >= 2
                )
                if gradient:
                    paint = self._gradient_surface(
                        surface.get_size(), state["gradient_stops"], state["gradient_type"],
                        state.get("gradient_radial_mode", "radius"),
                        float(state.get("gradient_angle", 0.0)),
                        int(220 * state["opacity"] * layer_opacity),
                        noise_seed=state.get("noise_seed", 0),
                        time_ms=self.current_ms,
                    )
                else:
                    paint = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
                    paint.fill((*state["color"], int(220 * state["opacity"] * layer_opacity)))
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

    def _gradient_surface(
        self,
        size: tuple[int, int], stops: list[GradientStop], gradient_type: str,
        radial_mode: str, angle: float, alpha: int,
        noise_seed: int = 0, time_ms: int = 0,
    ) -> pygame.Surface:
        width, height = size
        if gradient_type == "noise":
            # A coarse speckle grid (not per-pixel) so the edit-view preview
            # reads as flickering static rather than a smooth gradient, and
            # stays cheap to redraw every frame while scrubbing/playing.
            result = pygame.Surface(size, pygame.SRCALPHA)
            cell = 6
            for grid_y in range(0, height, cell):
                for grid_x in range(0, width, cell):
                    point = (grid_x / max(1, width), grid_y / max(1, height))
                    color = noise_color(noise_seed, point, time_ms, stops)
                    result.fill((*color, alpha), (grid_x, grid_y, cell, cell))
            return result
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
                return self._scale_surface(square, size)

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
            or layer.locked
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
            if any(layer.is_falloff for layer in self.project.layers):
                state = self.project.to_dict()
                if (
                    state != self._falloff_preview_project_state
                    or slots != self._falloff_preview_slots
                ):
                    self._falloff_preview_frames = sample_project_frames(self.project, slots)
                    self._falloff_preview_project_state = deepcopy(state)
                    self._falloff_preview_slots = list(slots)
                frame_ms = max(1, int(self.project.frame_ms or 1))
                frame_index = min(
                    len(self._falloff_preview_frames) - 1,
                    max(0, self.current_ms // frame_ms),
                )
                flattened = self._falloff_preview_frames[frame_index]
                firmware_colors = [
                    tuple(flattened[index:index + 3])
                    for index in range(0, len(flattened), 3)
                ]
                firmware_colors = [
                    (0, 0, 0) if color == TRANSPARENT_SENTINEL else color
                    for color in firmware_colors
                ]
            else:
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

    def _draw_loop_bracket(
        self, ruler: pygame.Rect, track: pygame.Rect, timeline: pygame.Rect, start: float, end: float,
    ) -> None:
        frame_ms = self._timeline_frame_ms()
        duration_ms = self._playback_duration()
        has_outro = self.project.loop_frames != 0
        has_intro = self.project.normalized_intro_frames > 0
        intro_boundary_ms = self.project.normalized_intro_frames * frame_ms
        loop_boundary_ms = self.project.normalized_loop_frames * frame_ms

        def x_at(time_ms: float) -> int:
            return round(max(track.x, min(track.right, self._time_to_timeline_x(time_ms, timeline))))

        def tint_span(left: int, right: int, color: tuple[int, int, int, int]) -> None:
            if right <= left:
                return
            surface = pygame.Surface((right - left, ruler.height), pygame.SRCALPHA)
            surface.fill(color)
            self.screen.blit(surface, (left, ruler.y))

        once_color = (255, 170, 60, 30)
        loop_color = (90, 170, 255, 40)
        intro_right = x_at(intro_boundary_ms)
        loop_right = x_at(loop_boundary_ms if has_outro else duration_ms)
        if has_intro:
            tint_span(x_at(0), intro_right, once_color)
        tint_span(intro_right, loop_right, loop_color)
        if has_outro:
            tint_span(loop_right, x_at(duration_ms), once_color)

        def draw_handle(boundary_ms: float, action: str, drag_name: str, tooltip: str) -> None:
            if not (start <= boundary_ms <= end) or duration_ms <= 0:
                return
            handle_x = round(self._time_to_timeline_x(boundary_ms, timeline))
            dragging = self.drag_mode == drag_name
            handle_color = (255, 214, 110) if dragging else (120, 190, 255)
            pygame.draw.line(self.screen, handle_color, (handle_x, ruler.y), (handle_x, track.bottom), 2)
            tab = pygame.Rect(0, 0, 11, ruler.height - 4)
            tab.center = (handle_x, ruler.centery)
            pygame.draw.rect(self.screen, handle_color, tab, border_radius=3)
            hit_rect = pygame.Rect(0, 0, 18, ruler.height + 12)
            hit_rect.center = (handle_x, ruler.centery)
            self.buttons.append((hit_rect, action, tooltip))

        draw_handle(intro_boundary_ms, "intro_bracket_handle", "intro_bracket", "Drag to set where the intro ends and the loop starts")
        draw_handle(loop_boundary_ms, "loop_bracket_handle", "loop_bracket", "Drag to set where the loop ends and the outro starts")

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
            playhead_tip_y = track.y - 5
            pygame.draw.polygon(
                self.screen, (255, 83, 72),
                [
                    (play_x - 6, playhead_tip_y - 9),
                    (play_x + 6, playhead_tip_y - 9),
                    (play_x, playhead_tip_y),
                ],
            )
            pygame.draw.line(
                self.screen, (255, 83, 72),
                (play_x, playhead_tip_y - 5), (play_x, track.bottom), 2,
            )
        hint = "Graph Editor • drag points in 2D • right-click: easing/delete • wheel: time zoom"
        self.screen.blit(self.small.render(hint, True, (132, 139, 155)), (track.x, timeline.bottom - 22))

    def _draw_leds(self, canvas: pygame.Rect, stencil_back: bool = False) -> None:
        colors = self._preview_led_colors()
        radius = max(3, min(9, canvas.width // 90))
        if stencil_back:
            visible = canvas.clip(self.screen.get_clip())
            if visible.width <= 0 or visible.height <= 0:
                return
            glow_layer = self._glow_canvas_cache.get(visible.size)
            if glow_layer is None:
                # The old code allocated a full-canvas glow surface.  At maximum
                # zoom that was tens of MiB even though only the viewport is shown.
                if len(self._glow_canvas_cache) >= 2:
                    self._glow_canvas_cache.clear()
                glow_layer = pygame.Surface(visible.size).convert()
                self._glow_canvas_cache[visible.size] = glow_layer
            glow_layer.fill(
                CANVAS_STENCIL_BACKGROUND if self._stencil_canvas_active() else (0, 0, 0)
            )
            light_radius = max(20, min(46, canvas.width // 10))
            for led, (x, y), rendered_color in zip(self.led_map.leds, self.led_points, colors):
                color = (0, 0, 0) if led.name.strip().upper() == "NULL" else rendered_color
                if not any(color):
                    continue
                light = self._glow_sprite(light_radius, color)
                center = (light.get_width() // 2, light.get_height() // 2)
                local = (
                    round(canvas.x + x * canvas.width - visible.x - center[0]),
                    round(canvas.y + y * canvas.height - visible.y - center[1]),
                )
                glow_layer.blit(light, local, special_flags=pygame.BLEND_RGB_ADD)
            self.screen.blit(glow_layer, visible.topleft, special_flags=pygame.BLEND_RGB_ADD)
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

    def _stencil_canvas_active(self) -> bool:
        effect = self._active_imported_effect()
        if effect:
            return effect.overlay and any(
                color == TRANSPARENT_SENTINEL for color in effect.colors_at(self.current_ms)
            )
        return self.project.canvas_transparency_at(self.current_ms)

    def _glow_sprite(
        self, radius: int, color: tuple[int, int, int],
    ) -> pygame.Surface:
        quantized = tuple(min(255, round(channel / 8) * 8) for channel in color)
        key = (radius, quantized)
        cached = self._glow_sprite_cache.get(key)
        if cached is not None:
            return cached
        diameter = radius * 2 + 2
        light = pygame.Surface((diameter, diameter)).convert()
        light.fill((0, 0, 0))
        center = (diameter // 2, diameter // 2)
        rings = (
            (radius, 0.10),
            (round(radius * 0.78), 0.18),
            (round(radius * 0.56), 0.34),
            (round(radius * 0.34), 0.68),
            (max(3, round(radius * 0.16)), 1.0),
        )
        for ring_radius, strength in rings:
            pygame.draw.circle(
                light,
                tuple(round(channel * strength) for channel in quantized),
                center,
                ring_radius,
            )
        if len(self._glow_sprite_cache) >= 256:
            self._glow_sprite_cache.clear()
        self._glow_sprite_cache[key] = light
        return light

    def _draw_timeline(self, rect: pygame.Rect) -> None:
        pygame.draw.rect(self.screen, (29, 32, 40), rect)
        pygame.draw.line(self.screen, (67, 72, 86), rect.topleft, rect.topright)
        resize_handle = pygame.Rect(rect.x, rect.y, rect.width, 8)
        resize_hovered = resize_handle.collidepoint(pygame.mouse.get_pos())
        if resize_hovered or self.drag_mode == "timeline_resize":
            pygame.draw.rect(self.screen, (42, 58, 82), resize_handle)
        grip_x = rect.centerx
        pygame.draw.line(self.screen, (104, 135, 180), (grip_x - 22, rect.y + 3), (grip_x + 22, rect.y + 3), 1)
        pygame.draw.line(self.screen, (82, 105, 140), (grip_x - 14, rect.y + 6), (grip_x + 14, rect.y + 6), 1)
        self.buttons.append((resize_handle, "timeline_resize_handle", "Resize timeline"))
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
        self._transport_button(
            pygame.Rect(rect.x + 184, rect.y + 5, 32, 25),
            "step_frame:-1", "previous",
        )
        self._transport_button(
            pygame.Rect(rect.x + 221, rect.y + 5, 40, 25),
            "play", "stop" if self.playing else "play", self.playing,
        )
        self._transport_button(
            pygame.Rect(rect.x + 266, rect.y + 5, 32, 25),
            "step_frame:1", "next",
        )
        self._transport_button(
            pygame.Rect(rect.x + 300, rect.y + 5, 28, 25),
            "play_loop", "loop", self.playing and self.loop_playback,
        )
        self._button(
            self._timeline_keyframe_button_rect(rect), "keyframe", "+ Keyframe", False,
        )

        label_x = self._timeline_duration_area_x(rect)
        self.screen.blit(self.small.render("Length", True, (154, 162, 180)), (label_x, rect.y + 12))
        duration_field = self._duration_input_rect(rect)
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

        duration_slider = self._duration_slider_rect(rect)
        pygame.draw.line(
            self.screen, (58, 63, 76) if imported else (91, 98, 115),
            duration_slider.midleft, duration_slider.midright, 4,
        )
        if not imported:
            ratio = (self.project.duration_ms - 500) / 14500
            handle_x = duration_slider.x + round(max(0.0, min(1.0, ratio)) * duration_slider.width)
            pygame.draw.circle(self.screen, ACCENT, (handle_x, duration_slider.centery), 7)
            self.buttons.append((duration_slider.inflate(0, 14), "duration_slider", "Duration"))

        ruler = pygame.Rect(
            track.x,
            rect.y + TIMELINE_TOOLBAR_HEIGHT,
            track.width,
            TIMELINE_RULER_HEIGHT,
        )
        pygame.draw.rect(self.screen, (26, 29, 36), ruler)
        pygame.draw.line(self.screen, (55, 60, 72), ruler.topleft, ruler.topright)
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
                pygame.draw.line(self.screen, (58, 63, 75), (x, track.y - 7), (x, track.bottom), 1)
            first_label = math.ceil(start / frame_ms / label_step) * label_step
            for frame_index in range(first_label, last_frame + 1, label_step):
                tick = frame_index * frame_ms
                x = round(self._time_to_timeline_x(tick, rect))
                pygame.draw.line(self.screen, (82, 88, 103), (x, track.y - 12), (x, track.bottom), 1)
                self.screen.blit(
                    self.small.render(f"F{frame_index}", True, (145, 151, 166)),
                    (x + 3, ruler.y + 3),
                )
        else:
            tick_ms = 1000 if visible_ms > 4000 else 500 if visible_ms > 2000 else 200 if visible_ms > 1000 else 100
            tick = math.ceil(start / tick_ms) * tick_ms
            while tick <= end:
                x = round(self._time_to_timeline_x(tick, rect))
                pygame.draw.line(self.screen, (72, 77, 91), (x, track.y - 11), (x, track.bottom), 1)
                self.screen.blit(
                    self.small.render(f"{tick / 1000:g}s", True, (145, 151, 166)),
                    (x + 4, ruler.y + 3),
                )
                tick += tick_ms

        if not imported:
            self._draw_loop_bracket(ruler, track, rect, start, end)

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
                    if layer.is_canvas else
                    bool(layer.value_at("falloff_enabled", self.current_ms))
                    if layer.is_falloff else layer.visible
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
                    f"toggle_layer_lock:{index}", "L", layer.locked,
                )
                self._button(
                    pygame.Rect(rect.x + 73, row_rect.y + 5, 22, 21),
                    f"toggle_timeline_layer:{index}", "v" if expanded else ">", expanded,
                )
                layer_text = (
                    f"C {layer.name}" if layer.is_canvas else
                    f"F {layer.name}" if layer.is_falloff else layer.name
                )
                while len(layer_text) > 5 and self.small.size(layer_text)[0] > 76:
                    layer_text = layer_text[:-4] + "..."
                label = self.small.render(
                    layer_text, True,
                    (133, 143, 161) if layer.locked else
                    (226, 230, 239) if active_layer else (166, 172, 187),
                )
                self.screen.blit(label, (rect.x + 101, row_y - 3))
                self.buttons.append((
                    pygame.Rect(rect.x + 98, row_rect.y, 82, row_rect.height),
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
                target_generator_kind = self._generator_kind_of(target)
                target_active = target is self.selected or (
                    target_generator_kind is not None
                    and getattr(self, f"{target_generator_kind}_editor_open")
                    and index == self.active_layer
                ) or (
                    isinstance(target, Layer)
                    and (target.is_canvas or target.is_falloff)
                    and index == self.active_layer
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
            playhead_tip_y = track.y - 5
            pygame.draw.polygon(
                self.screen, (255, 83, 72),
                [
                    (play_x - 6, playhead_tip_y - 9),
                    (play_x + 6, playhead_tip_y - 9),
                    (play_x, playhead_tip_y),
                ],
            )
            pygame.draw.line(
                self.screen, (255, 83, 72),
                (play_x, playhead_tip_y - 5), (play_x, track.bottom), 2,
            )

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

    def _draw_panel_section(self, x: int, y: int, title: str) -> None:
        pygame.draw.rect(self.screen, ACCENT, pygame.Rect(x, y + 2, 3, 18), border_radius=2)
        self.screen.blit(self.font.render(title, True, (224, 228, 238)), (x + 11, y))

    def _draw_panel(self, panel: pygame.Rect) -> None:
        pygame.draw.rect(self.screen, PANEL, panel)
        pygame.draw.line(self.screen, (68, 72, 84), panel.topleft, panel.bottomleft)
        x = panel.x + 14
        pygame.draw.rect(self.screen, (27, 30, 38), (panel.x, panel.y, panel.width, 67))
        pygame.draw.rect(self.screen, ACCENT, (panel.x, panel.y, 3, 67))
        self.screen.blit(self.font.render("INSPECTOR", True, (235, 238, 245)), (x, panel.y + 13))
        map_label = f"{len(self.led_map.leds)} LEDs  •  {self.led_map.mapping_status.replace('_', ' ')}"
        self.screen.blit(self.small.render(map_label, True, (255, 178, 77)), (x, panel.y + 43))
        pygame.draw.line(self.screen, (58, 62, 74), (panel.x, panel.y + 66), (panel.right, panel.y + 66))

        if self.calibration:
            self._draw_calibration_panel(panel, x, panel.y + 80)
            return
        generator_kind = self._active_generator_panel_kind()
        if generator_kind:
            effect = self._active_generator_effect(generator_kind)
            if effect:
                self._draw_generator_panel(panel, x, generator_kind, effect)
                return
            self._close_generator_panels()
        if self.gradient_editor_open:
            if self.selected:
                self._draw_gradient_panel(panel, x)
                return
            self.gradient_editor_open = False

        content_top = panel.y + 76
        content_bottom = panel.bottom - 47
        self.inspector_scroll = max(0, min(self.inspector_max_scroll, self.inspector_scroll))
        old_clip = self.screen.get_clip()
        self.screen.set_clip(pygame.Rect(panel.x, content_top, panel.width, content_bottom - content_top))
        buttons_before = len(self.buttons)

        y = content_top - self.inspector_scroll
        imported = self._active_imported_effect()
        self._draw_panel_section(x, y, "Firmware V4")
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
            self._button(pygame.Rect(x + 136, y, 139, 27), "clear_loop_end", "Full (no outro)", False)
            y += 32
            loop_frames = self.project.normalized_loop_frames
            intro_frames = self.project.normalized_intro_frames
            loop_range = (
                f"{intro_frames}▸{loop_frames}" if intro_frames else f"{loop_frames}"
            )
            stats = (
                f"×{self.project.loops} • {loop_range}/{self.project.stored_frame_count} • "
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
        self._draw_panel_section(x, y, "Layers")
        self._button(pygame.Rect(panel.right - 230, y - 3, 34, 25), "canvas_layer", "C+", False)
        self._button(pygame.Rect(panel.right - 190, y - 3, 34, 25), "falloff_layer", "F+", False)
        fx_button = pygame.Rect(panel.right - 150, y - 3, 34, 25)
        self._generator_menu_anchor = fx_button
        self._button(
            fx_button, "generator_menu_toggle", "FX",
            self.generator_menu_open or self._active_generator_panel_kind() is not None,
        )
        self._button(pygame.Rect(panel.right - 110, y - 3, 28, 25), "duplicate_layer", "D", False)
        self._button(pygame.Rect(panel.right - 76, y - 3, 28, 25), "delete_layer", "−", False)
        self._button(pygame.Rect(panel.right - 42, y - 3, 28, 25), "layer", "+", False)
        y += 24
        layer_capacity = max(2, 4 + (self.screen.get_height() - 900) // 31)
        for index, layer in enumerate(self.project.layers[:layer_capacity]):
            row = pygame.Rect(x, y, panel.width - 28, 27)
            if index == self.active_layer:
                pygame.draw.rect(self.screen, (47, 61, 85), row, border_radius=3)
                pygame.draw.rect(self.screen, ACCENT, (row.x, row.y, 3, row.height))
            eye = pygame.Rect(row.x + 5, row.y + 3, 24, 21)
            layer_on = (
                bool(layer.value_at("canvas_enabled", self.current_ms))
                if layer.is_canvas else
                bool(layer.value_at("falloff_enabled", self.current_ms))
                if layer.is_falloff else layer.visible
            )
            self._button(eye, f"toggle_layer:{index}", "●" if layer_on else "○", False)
            prefix = "C" if layer.is_canvas else "F" if layer.is_falloff else "≡"
            name_color = (
                (105, 218, 224) if layer.is_canvas else
                (255, 190, 92) if layer.is_falloff else (228, 232, 241)
            )
            editing_name = self.layer_name_editing_id == layer.id
            layer_name = self.layer_name_input if editing_name else layer.name
            if editing_name and (pygame.time.get_ticks() // 500) % 2 == 0:
                layer_name += "|"
            layer_name = self._fit_text(layer_name, self.small, row.width - 120)
            name = self.small.render(f"{prefix}   {layer_name}", True, name_color)
            self.screen.blit(name, (row.x + 38, row.y + 6))
            self.buttons.append((
                pygame.Rect(row.x + 34, row.y, row.width - 97, row.height),
                f"drag_layer:{index}", layer.name,
            ))
            self._button(
                pygame.Rect(row.right - 57, row.y + 3, 25, 21),
                f"toggle_layer_lock:{index}", "L", layer.locked,
            )
            self._button(
                pygame.Rect(row.right - 29, row.y + 3, 25, 21),
                f"rename_layer:{index}", "R", editing_name,
            )
            y += 31

        active_panel_layer = self.project.layers[self.active_layer]
        if not active_panel_layer.is_canvas and not active_panel_layer.is_falloff:
            opacity_field = pygame.Rect(x, y + 4, panel.width - 28, 30)
            editing_opacity = self.property_editing == "layer_opacity"
            self._numeric_property_widget(
                opacity_field, LAYER_PROPERTY_SPECS["layer_opacity"],
                active_panel_layer.opacity, "scrub:layer_opacity",
                editing=editing_opacity,
                input_text=self.property_input if editing_opacity else "",
            )
            y += 38

        y += 10
        pygame.draw.line(self.screen, (58, 62, 74), (panel.x, y), (panel.right, y))
        y += 13
        section_title = (
            "Canvas control" if active_panel_layer.is_canvas else
            "Falloff control" if active_panel_layer.is_falloff else "Transform"
        )
        self._draw_panel_section(x, y, section_title)
        y += 31
        if self.selected:
            state = self.selected.state_at(self.current_ms)
            self.screen.blit(self.small.render(self.selected.name, True, (118, 184, 255)), (x, y))
            kind = self.small.render(self.selected.kind.upper(), True, (135, 141, 157))
            self.screen.blit(kind, (panel.right - kind.get_width() - 14, y))
            y += 27
            properties = [
                (prop, state[prop]) for prop in (
                    "x", "y", "width", "height", "rotation", "rotation_turns",
                    "opacity", "feather", "mask_expansion",
                )
            ]
            for index, (prop, value) in enumerate(properties):
                col = index % 2
                row = index // 2
                field = pygame.Rect(x + col * 143, y + row * 37, 132, 30)
                editing = self.property_editing == prop
                self._numeric_property_widget(
                    field, SHAPE_PROPERTY_SPECS[prop], value, f"scrub:{prop}",
                    editing=editing,
                    input_text=self.property_input if editing else "",
                )
            # 9 properties in a 2-column grid leave the last cell (row 4,
            # col 1) empty - the Wiggle toggle lives there so it costs no
            # extra panel height when disabled, which is the common case.
            wiggle_enabled = bool(state["wiggle_enabled"])
            self._button(
                pygame.Rect(x + 143, y + 4 * 37, 132, 30), "toggle_wiggle",
                "Wiggle: ON" if wiggle_enabled else "Wiggle: OFF", wiggle_enabled,
            )
            y += 183
            if wiggle_enabled:
                for index, prop in enumerate(("wiggle_amplitude", "wiggle_speed")):
                    field = pygame.Rect(x + index * 143, y, 132, 30)
                    editing = self.property_editing == prop
                    self._numeric_property_widget(
                        field, SHAPE_PROPERTY_SPECS[prop], getattr(self.selected, prop),
                        f"scrub:{prop}",
                        editing=editing,
                        input_text=self.property_input if editing else "",
                    )
                y += 30
            self.screen.blit(self.small.render("Style", True, (172, 178, 193)), (x, y))
            y += 20
            self._button(
                pygame.Rect(x, y, 132, 29), "fill_mode:fill", "Fill",
                state.get("fill_mode", "fill") == "fill",
            )
            self._button(
                pygame.Rect(x + 143, y, 132, 29), "fill_mode:stroke", "Stroke",
                state.get("fill_mode", "fill") == "stroke",
            )
            y += 34
            if state.get("fill_mode", "fill") == "stroke":
                stroke_field = pygame.Rect(x, y, 275, 30)
                self._numeric_property_widget(
                    stroke_field, SHAPE_PROPERTY_SPECS["stroke_width"],
                    state.get("stroke_width", 0.012), "scrub:stroke_width",
                    editing=self.stroke_editing,
                    input_text=self.stroke_input if self.stroke_editing else "",
                )
                y += 32
            gradient_type = state.get("gradient_type", "solid").title()
            paint_target = "stroke" if state.get("fill_mode", "fill") == "stroke" else "fill"
            self._button(
                pygame.Rect(x, y, 275, 30), "gradient_editor",
                f"Gradient {paint_target}…  {gradient_type}", gradient_type != "Solid",
            )
            y += 32
            self.screen.blit(self.small.render("Color", True, (172, 178, 193)), (x, y))
            y += 20
            y = self._draw_color_palette(x, y, tuple(state["color"]))
            y += 3
            self._button(pygame.Rect(x, y, 132, 30), "duplicate", "Duplicate", False)
            self._button(pygame.Rect(x + 143, y, 132, 30), "delete", "Delete", False)
            y += 30
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
        elif active_panel_layer.is_falloff:
            enabled = bool(active_panel_layer.value_at("falloff_enabled", self.current_ms))
            state_label = (
                f"ENABLED · {active_panel_layer.falloff_ms} ms fade"
                if enabled else "DISABLED · raw baked frames"
            )
            state_color = (105, 218, 174) if enabled else (255, 171, 83)
            self.screen.blit(self.small.render(state_label, True, state_color), (x, y))
            y += 30
            self._button(
                pygame.Rect(x, y, 132, 32), "falloff_state:1", "Enable key", enabled,
            )
            self._button(
                pygame.Rect(x + 143, y, 132, 32), "falloff_state:0", "Disable key", not enabled,
            )
            y += 44
            self.screen.blit(self.small.render("Fade duration", True, (172, 178, 193)), (x, y))
            y += 21
            self._button(pygame.Rect(x, y, 62, 30), "falloff_ms:-100", "−100", False)
            duration = self.font.render(f"{active_panel_layer.falloff_ms} ms", True, (232, 236, 244))
            self.screen.blit(duration, duration.get_rect(center=(x + 137, y + 15)))
            self._button(pygame.Rect(x + 213, y, 62, 30), "falloff_ms:100", "+100", False)
            y += 43
            has_key = any(
                frame.time_ms == self.current_ms
                for frame in active_panel_layer.keyframes.get("falloff_enabled", [])
            )
            key_status = "Keyframe exists at playhead" if has_key else "No Falloff keyframe at playhead"
            self.screen.blit(self.small.render(key_status, True, (151, 161, 180)), (x, y))
            y += 27
            tips = (
                "Brightness increases stay immediate.",
                "Brightness drops are baked into a smooth tail.",
                "Use ON/OFF or V to keyframe where it applies.",
            )
            for tip in tips:
                self.screen.blit(self.small.render(tip, True, (126, 136, 156)), (x, y))
                y += 22
        elif active_panel_layer.locked:
            self.screen.blit(
                self.small.render("Layer locked — unlock it to edit contents.", True, (255, 184, 92)),
                (x, y),
            )
        else:
            self.screen.blit(self.small.render("Select an object on the canvas.", True, (145, 152, 169)), (x, y))
            y += 28
            self.screen.blit(self.small.render("Drag a shape tool onto the playfield.", True, (116, 124, 142)), (x, y))

        content_height = y - (content_top - self.inspector_scroll)
        self.inspector_max_scroll = max(0, content_height - (content_bottom - content_top))
        self.buttons[buttons_before:] = [
            (rect, action, label) for rect, action, label in self.buttons[buttons_before:]
            if rect.bottom > content_top and rect.top < content_bottom
        ]
        self.screen.set_clip(old_clip)
        if self.inspector_max_scroll > 0:
            self._draw_inspector_scrollbar(panel, content_top, content_bottom)

        pygame.draw.rect(self.screen, (22, 25, 32), (panel.x, panel.bottom - 47, panel.width, 47))
        pygame.draw.line(
            self.screen, (58, 64, 78),
            (panel.x, panel.bottom - 47), (panel.right, panel.bottom - 47),
        )
        pygame.draw.circle(self.screen, (100, 216, 162), (x + 4, panel.bottom - 24), 4)
        status_text = self._fit_text(self.status, self.small, panel.width - 43)
        self.screen.blit(self.small.render(status_text, True, (171, 184, 204)), (x + 15, panel.bottom - 31))

    def _draw_inspector_scrollbar(self, panel: pygame.Rect, content_top: int, content_bottom: int) -> None:
        track_height = content_bottom - content_top
        gutter = pygame.Rect(panel.right - 6, content_top, 3, track_height)
        pygame.draw.rect(self.screen, (48, 53, 65), gutter, border_radius=2)
        total_height = track_height + self.inspector_max_scroll
        handle_height = max(24, round(gutter.height * track_height / max(1, total_height)))
        travel = gutter.height - handle_height
        handle_y = gutter.y + round(travel * self.inspector_scroll / max(1, self.inspector_max_scroll))
        pygame.draw.rect(
            self.screen, (104, 126, 164),
            pygame.Rect(gutter.x, handle_y, gutter.width, handle_height), border_radius=2,
        )

    def _draw_generator_menu(self) -> None:
        anchor = self._generator_menu_anchor or pygame.Rect(self.screen.get_width() - 184, 100, 34, 25)
        width = 200
        row_height = 32
        kinds = list(GENERATOR_KINDS.values())
        menu = pygame.Rect(
            min(anchor.x, self.screen.get_width() - width - 6), anchor.bottom + 4,
            width, row_height * len(kinds) + 8,
        )
        pygame.draw.rect(self.screen, (36, 40, 50), menu, border_radius=6)
        pygame.draw.rect(self.screen, (82, 89, 106), menu, 1, border_radius=6)
        layer = self.project.layers[self.active_layer]
        row_y = menu.y + 4
        for kind in kinds:
            row = pygame.Rect(menu.x + 4, row_y, width - 8, row_height - 4)
            has_effect = bool(getattr(layer, kind.list_attr))
            self._button(row, f"{kind.key}_editor", kind.menu_label, has_effect)
            row_y += row_height

    def _draw_generator_panel(self, panel: pygame.Rect, x: int, kind: str, effect: object) -> None:
        info = GENERATOR_KINDS[kind]
        layer = self.project.layers[self.active_layer]
        enabled = bool(effect.value_at("enabled", self.current_ms))

        y = panel.y + 78
        self.screen.blit(self.font.render(info.title, True, (225, 229, 238)), (x, y))
        self._button(pygame.Rect(panel.right - 82, y - 4, 68, 28), "generator_back", "< Back", False)
        y += 35
        self.screen.blit(self.small.render(f"Layer: {layer.name}", True, (118, 184, 255)), (x, y)); y += 28
        for line in info.description:
            self.screen.blit(self.small.render(line, True, (154, 162, 180)), (x, y)); y += 20
        y += 11

        self.screen.blit(self.small.render("Enabled", True, (172, 178, 193)), (x, y))
        state_color = (96, 220, 155) if enabled else (255, 112, 102)
        state = "ON" if enabled else "OFF"
        self.screen.blit(self.font.render(state, True, state_color), (x + 72, y - 3))
        self._button(pygame.Rect(x + 132, y - 6, 68, 30), "generator_toggle", "Toggle", enabled)
        self._button(pygame.Rect(x + 207, y - 6, 68, 30), "generator_keyframe", "State key", False)
        y += 42

        if info.has_direction:
            forward = getattr(effect, "direction", 1) >= 0
            self.screen.blit(self.small.render("Direction", True, (172, 178, 193)), (x, y))
            self._button(
                pygame.Rect(x + 132, y - 6, 143, 30), "generator_toggle_direction",
                "Forward" if forward else "Reverse", True,
            )
            y += 42

        if info.has_blackout:
            blackout = bool(getattr(effect, "blackout", False))
            self.screen.blit(self.small.render("Flash", True, (172, 178, 193)), (x, y))
            self._button(
                pygame.Rect(x + 132, y - 6, 143, 30), "generator_toggle_blackout",
                "Blackout (cut)" if blackout else "Color", True,
            )
            y += 42

        def parameter_row(prop: str, value: float | int) -> None:
            nonlocal y
            row = pygame.Rect(x, y, 275, 34)
            editing = self.random_effect_editing == prop
            has_key = prop == "opacity" and any(
                frame.time_ms == self.current_ms
                for frame in effect.keyframes.get("opacity", [])
            )
            self._numeric_property_widget(
                row, info.specs[prop], value,
                f"generator_field:{prop}",
                editing=editing,
                input_text=self.random_effect_input if editing else "",
                key_action="generator_opacity_keyframe" if prop == "opacity" else None,
                key_active=has_key,
            )
            y += 42

        for prop in info.param_props:
            parameter_row(prop, getattr(effect, prop))
        parameter_row("opacity", float(effect.value_at("opacity", self.current_ms)))

        if info.has_color:
            y += 5
            self.screen.blit(self.small.render("Color", True, (172, 178, 193)), (x, y)); y += 25
            y = self._draw_color_palette(x, y, effect.color)

        y += 14
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
        self._button(pygame.Rect(x, y, 275, 30), "generator_delete", f"Remove {info.menu_label} effect", False)

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
        paint_target = "STROKE" if state.get("fill_mode", "fill") == "stroke" else "FILL"
        self.screen.blit(
            self.font.render(f"GRADIENT {paint_target}", True, (225, 229, 238)), (x, y)
        )
        self._button(pygame.Rect(panel.right - 82, y - 4, 68, 28), "gradient_back", "< Back", False)
        y += 34
        self.screen.blit(self.small.render(self.selected.name, True, (118, 184, 255)), (x, y))
        y += 31
        self.screen.blit(self.small.render("Type", True, (172, 178, 193)), (x, y)); y += 22
        gradient_type = state.get("gradient_type", "solid")
        radial_mode = state.get("gradient_radial_mode", "radius")
        gradient_type_options = (
            ("solid", "Solid"), ("linear", "Linear"), ("radial", "Radial"), ("noise", "Noise"),
        )
        for index, (value, label) in enumerate(gradient_type_options):
            self._button(
                pygame.Rect(x + index * 70, y, 63, 30), f"gradient_type:{value}", label,
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
        palette_y = self._draw_color_palette(
            x, palette_y, selected_stop.color if selected_stop else (255, 255, 255),
        )

        tips_y = palette_y + 20
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

    def _handle_recovery_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_r):
                self._recover_autosave()
            elif event.key in (pygame.K_d, pygame.K_DELETE, pygame.K_BACKSPACE):
                self._discard_autosave()
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        action = next(
            (
                action for rect, action, _label in reversed(self.buttons)
                if action.startswith("recovery_") and rect.collidepoint(event.pos)
            ),
            None,
        )
        if action == "recovery_restore":
            self._recover_autosave()
        elif action == "recovery_discard":
            self._discard_autosave()

    def _draw_recovery_dialog(self) -> None:
        record = self.recovery_record
        if record is None:
            self.recovery_open = False
            return
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((7, 9, 13, 228))
        self.screen.blit(shade, (0, 0))
        panel = pygame.Rect(0, 0, min(650, self.screen.get_width() - 70), 310)
        panel.center = self.screen.get_rect().center
        pygame.draw.rect(self.screen, (25, 29, 38), panel, border_radius=11)
        pygame.draw.rect(self.screen, (88, 99, 122), panel, 1, border_radius=11)
        x, y = panel.x + 30, panel.y + 25
        self.screen.blit(self.title.render("RECOVER UNSAVED PROJECT", True, (239, 242, 248)), (x, y))
        y += 46
        self.screen.blit(
            self.font.render("CnC Light Editor found a newer autosave snapshot.", True, (194, 202, 219)),
            (x, y),
        )
        y += 34
        source = str(record.source_path) if record.source_path else "Untitled project"
        rows = (
            ("Project", record.project_data.get("name", "Untitled effect")),
            ("Original file", source),
            ("Autosaved", record.saved_at.replace("T", " ").replace("+00:00", " UTC")),
        )
        for label, value in rows:
            self.screen.blit(self.small.render(label, True, (126, 138, 162)), (x, y))
            fitted = self._fit_text(str(value), self.small, panel.width - 175)
            self.screen.blit(self.small.render(fitted, True, (226, 231, 240)), (x + 125, y))
            y += 27
        self.screen.blit(
            self.small.render("Recover restores the editable project; it remains unsaved until Ctrl+S.", True, (142, 153, 176)),
            (x, panel.bottom - 78),
        )
        self._button(
            pygame.Rect(x, panel.bottom - 48, 200, 32),
            "recovery_restore", "Recover project", True,
        )
        self._button(
            pygame.Rect(x + 215, panel.bottom - 48, 170, 32),
            "recovery_discard", "Discard snapshot", False,
        )

    def _export_bank_panel_rect(self) -> pygame.Rect:
        width = min(1120, self.screen.get_width() - 50)
        # 800 (not 720) so the SELECTED EFFECT detail panel's frame is tall
        # enough for its tallest content - badge/name/ID fields + 7 metadata
        # rows + the "Load editable project"/"Remove from export bank"
        # buttons - without the buttons spilling past the frame's bottom
        # edge (see _export_bank_layout).
        height = min(800, self.screen.get_height() - 50)
        panel = pygame.Rect(0, 0, width, height)
        panel.center = self.screen.get_rect().center
        return panel

    def _export_bank_layout(self) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect]:
        """Toolbar (sort + search), bank content list, and selected-effect
        detail rects for the Effect Bank screen - shared by the draw and the
        mouse-wheel event handler so their geometry never drifts apart."""
        panel = self._export_bank_panel_rect()
        toolbar = pygame.Rect(panel.x + 26, panel.y + 224, panel.width - 52, 30)
        list_rect = pygame.Rect(panel.x + 26, toolbar.bottom + 29, panel.width - 398, panel.height - 353)
        detail = pygame.Rect(list_rect.right + 18, list_rect.y, panel.right - list_rect.right - 44, list_rect.height)
        return toolbar, list_rect, detail

    def _file_browser_panel_rect(self) -> pygame.Rect:
        width = min(980, self.screen.get_width() - 50)
        height = min(700, self.screen.get_height() - 50)
        panel = pygame.Rect(0, 0, max(700, width), max(540, height))
        panel.center = self.screen.get_rect().center
        return panel

    @staticmethod
    def _file_browser_list_rect(panel: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel.x + 196, panel.y + 122, panel.width - 218, panel.height - 254)

    def _draw_file_browser(self) -> None:
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((6, 8, 12, 226))
        self.screen.blit(shade, (0, 0))
        panel = self._file_browser_panel_rect()
        pygame.draw.rect(self.screen, (25, 28, 36), panel, border_radius=10)
        pygame.draw.rect(self.screen, (80, 88, 107), panel, 1, border_radius=10)
        x = panel.x + 22
        self.screen.blit(
            self.title.render(self.file_browser_title, True, (239, 242, 248)),
            (x, panel.y + 19),
        )
        mode_label = "SAVE FILE" if self.file_browser_mode == "save" else "OPEN FILE"
        rendered_mode = self.small.render(mode_label, True, (108, 180, 255))
        self.screen.blit(rendered_mode, (panel.right - rendered_mode.get_width() - 22, panel.y + 25))

        self.screen.blit(self.small.render("Location", True, (143, 153, 174)), (x, panel.y + 65))
        path_field = pygame.Rect(x + 68, panel.y + 57, panel.width - 190, 34)
        pygame.draw.rect(self.screen, (39, 43, 53), path_field, border_radius=4)
        pygame.draw.rect(
            self.screen,
            ACCENT if self.file_browser_path_editing else (72, 79, 96),
            path_field, 1, border_radius=4,
        )
        path_value = self.file_browser_path_input if self.file_browser_path_editing else str(self.file_browser_directory)
        if self.file_browser_path_editing and (pygame.time.get_ticks() // 500) % 2 == 0:
            path_value += "|"
        self.screen.blit(
            self.small.render(self._fit_text(path_value, self.small, path_field.width - 16), True, (231, 235, 243)),
            (path_field.x + 8, path_field.y + 9),
        )
        self.buttons.append((path_field, "file_browser_path", "Type folder path"))
        self._button(
            pygame.Rect(panel.right - 92, panel.y + 57, 70, 34),
            "file_browser_up", "Up", False,
        )

        locations_rect = pygame.Rect(x, panel.y + 122, 160, panel.height - 254)
        list_rect = self._file_browser_list_rect(panel)
        for area in (locations_rect, list_rect):
            pygame.draw.rect(self.screen, (20, 23, 30), area, border_radius=6)
            pygame.draw.rect(self.screen, (57, 63, 77), area, 1, border_radius=6)
        self.screen.blit(self.small.render("PLACES", True, (139, 149, 170)), (locations_rect.x + 10, locations_rect.y + 10))
        location_y = locations_rect.y + 35
        for index, (label, path) in enumerate(self._file_browser_locations()):
            if location_y + 32 > locations_rect.bottom - 6:
                break
            button = pygame.Rect(locations_rect.x + 7, location_y, locations_rect.width - 14, 30)
            active = os.path.normcase(str(path)) == os.path.normcase(str(self.file_browser_directory))
            pygame.draw.rect(self.screen, (48, 60, 82) if active else (31, 35, 44), button, border_radius=4)
            self.screen.blit(self.small.render(label, True, (222, 227, 237)), (button.x + 9, button.y + 8))
            self.buttons.append((button, f"file_browser_location:{index}", str(path)))
            location_y += 34

        entries = self._file_browser_entries()
        visible = max(1, (list_rect.height - 14) // 38)
        maximum_scroll = max(0, len(entries) - visible)
        self.file_browser_scroll = max(0, min(maximum_scroll, self.file_browser_scroll))
        if self.file_browser_selected >= len(entries):
            self.file_browser_selected = -1
        for visible_index, entry in enumerate(entries[self.file_browser_scroll:self.file_browser_scroll + visible]):
            entry_index = self.file_browser_scroll + visible_index
            row = pygame.Rect(list_rect.x + 7, list_rect.y + 7 + visible_index * 38, list_rect.width - 14, 34)
            selected = entry_index == self.file_browser_selected
            pygame.draw.rect(self.screen, (48, 61, 84) if selected else (30, 34, 43), row, border_radius=4)
            is_dir = entry.is_dir()
            icon_color = (244, 190, 72) if is_dir else (91, 180, 255)
            icon = pygame.Rect(row.x + 9, row.y + 9, 15, 12)
            pygame.draw.rect(self.screen, icon_color, icon, 2, border_radius=2)
            if is_dir:
                pygame.draw.rect(self.screen, icon_color, (icon.x + 2, icon.y - 3, 7, 4), border_radius=1)
            name = self._fit_text(entry.name, self.small, row.width - 150)
            self.screen.blit(self.small.render(name, True, (231, 235, 243)), (row.x + 34, row.y + 9))
            try:
                stats = "FOLDER" if is_dir else f"{entry.stat().st_size / 1024:.1f} KiB"
            except OSError:
                stats = "FOLDER" if is_dir else "FILE"
            stats_surface = self.small.render(stats, True, (132, 143, 164))
            self.screen.blit(stats_surface, (row.right - stats_surface.get_width() - 9, row.y + 9))
            self.buttons.append((row, f"file_browser_entry:{entry_index}", entry.name))
        if not entries:
            pattern = f"*{self.file_browser_extension}" if self.file_browser_extension else "files"
            empty = self.font.render(f"No {pattern} files in this folder", True, (132, 143, 164))
            self.screen.blit(empty, empty.get_rect(center=list_rect.center))
        elif maximum_scroll:
            gutter = pygame.Rect(list_rect.right - 5, list_rect.y + 7, 3, list_rect.height - 14)
            pygame.draw.rect(self.screen, (48, 53, 65), gutter, border_radius=2)
            handle_height = max(24, round(gutter.height * visible / len(entries)))
            travel = gutter.height - handle_height
            handle_y = gutter.y + round(travel * self.file_browser_scroll / maximum_scroll)
            pygame.draw.rect(self.screen, (104, 126, 164), (gutter.x, handle_y, gutter.width, handle_height), border_radius=2)

        bottom_y = panel.bottom - 111
        self.screen.blit(self.small.render("File name", True, (143, 153, 174)), (x, bottom_y + 9))
        filename_field = pygame.Rect(x + 76, bottom_y, panel.width - 290, 35)
        pygame.draw.rect(self.screen, (39, 43, 53), filename_field, border_radius=4)
        pygame.draw.rect(
            self.screen,
            ACCENT if self.file_browser_filename_editing else (72, 79, 96),
            filename_field, 1, border_radius=4,
        )
        filename = self.file_browser_filename
        if self.file_browser_filename_editing and (pygame.time.get_ticks() // 500) % 2 == 0:
            filename += "|"
        filename_color = (231, 235, 243) if self.file_browser_mode == "save" else (159, 169, 188)
        self.screen.blit(
            self.small.render(self._fit_text(filename or "Select a file", self.small, filename_field.width - 16), True, filename_color),
            (filename_field.x + 8, filename_field.y + 9),
        )
        if self.file_browser_mode == "save":
            self.buttons.append((filename_field, "file_browser_filename", "File name"))
        self._button(pygame.Rect(panel.right - 184, bottom_y, 76, 35), "file_browser_cancel", "Cancel", False)
        accept_label = "Save" if self.file_browser_mode == "save" else "Open"
        self._button(pygame.Rect(panel.right - 100, bottom_y, 78, 35), "file_browser_accept", accept_label, True)
        error_prefixes = ("Cannot", "Invalid", "Operation", "That path", "Select", "Enter")
        status_color = (255, 116, 103) if self.file_browser_status.startswith(error_prefixes) else (119, 205, 170)
        status = self._fit_text(self.file_browser_status, self.small, panel.width - 44)
        self.screen.blit(self.small.render(status, True, status_color), (x, panel.bottom - 48))
        hint = "Enter: open/save  ·  Backspace: up  ·  double-click: open  ·  Ctrl+V works in text fields"
        self.screen.blit(self.small.render(hint, True, (112, 123, 145)), (x, panel.bottom - 25))

    def _draw_confirmation_dialog(self) -> None:
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((5, 7, 10, 210))
        self.screen.blit(shade, (0, 0))
        width = min(560, self.screen.get_width() - 60)
        panel = pygame.Rect(0, 0, width, 220)
        panel.center = self.screen.get_rect().center
        pygame.draw.rect(self.screen, (27, 30, 39), panel, border_radius=10)
        pygame.draw.rect(self.screen, (87, 95, 115), panel, 1, border_radius=10)
        x = panel.x + 24
        self.screen.blit(
            self.title.render(self.confirmation_title, True, (239, 242, 248)),
            (x, panel.y + 23),
        )
        message_y = panel.y + 68
        for line in self._wrap_text(self.confirmation_message, self.font, panel.width - 48):
            self.screen.blit(self.font.render(line, True, (191, 198, 213)), (x, message_y))
            message_y += 24
        self._button(
            pygame.Rect(panel.right - 214, panel.bottom - 54, 88, 34),
            "confirmation_no", "Cancel", False,
        )
        self._button(
            pygame.Rect(panel.right - 116, panel.bottom - 54, 92, 34),
            "confirmation_yes", "Continue", True,
        )
        self.screen.blit(
            self.small.render("Enter: continue  ·  Esc: cancel", True, (115, 126, 147)),
            (x, panel.bottom - 43),
        )

    @staticmethod
    def _wrap_text(value: str, font: pygame.font.Font, max_width: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for word in value.split():
            candidate = f"{current} {word}".strip()
            if current and font.size(candidate)[0] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines or [""]

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
        # 824 (not 700) so the tallest column (LAYERS/CANVAS + VIEWPORT/
        # SHAPES + LED MAP) fits above the footer line without its last
        # rows spilling past the panel's own bottom edge.
        height = min(824, self.screen.get_height() - 60)
        panel = pygame.Rect(0, 0, width, height)
        panel.center = self.screen.get_rect().center
        return panel

    def _color_picker_panel_rect(self) -> pygame.Rect:
        width = min(360, self.screen.get_width() - 60)
        height = min(320, self.screen.get_height() - 60)
        panel = pygame.Rect(0, 0, width, height)
        panel.center = self.screen.get_rect().center
        return panel

    def _color_slider_rect(self, panel: pygame.Rect, channel: int) -> pygame.Rect:
        return pygame.Rect(panel.x + 24, panel.y + 148 + channel * 44, panel.width - 48, 20)

    def _set_color_channel_from_x(self, channel: int, screen_x: int) -> None:
        slider = self._color_slider_rect(self._color_picker_panel_rect(), channel)
        ratio = max(0.0, min(1.0, (screen_x - slider.x) / max(1, slider.width)))
        self.color_picker_rgb[channel] = round(ratio * 255)

    def _draw_color_picker(self) -> None:
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((7, 9, 13, 222))
        self.screen.blit(shade, (0, 0))
        panel = self._color_picker_panel_rect()
        pygame.draw.rect(self.screen, (25, 28, 36), panel, border_radius=10)
        pygame.draw.rect(self.screen, (77, 84, 102), panel, 1, border_radius=10)
        x, y = panel.x + 24, panel.y + 20
        self.screen.blit(self.title.render("Color Mixer", True, (238, 241, 248)), (x, y))
        self._button(pygame.Rect(panel.right - 66, y - 6, 42, 32), "close_color_picker", "×", False)

        color = tuple(self.color_picker_rgb)
        preview = pygame.Rect(x, y + 38, panel.width - 48, 56)
        pygame.draw.rect(self.screen, color, preview, border_radius=6)
        pygame.draw.rect(self.screen, (90, 96, 112), preview, 1, border_radius=6)
        hex_label = self.small.render(f"RGB {color}", True, (200, 205, 216))
        self.screen.blit(hex_label, hex_label.get_rect(center=preview.center))

        labels = ("R", "G", "B")
        accents = ((235, 90, 80), (90, 210, 120), (95, 150, 235))
        for channel, label in enumerate(labels):
            slider = self._color_slider_rect(panel, channel)
            value = self.color_picker_rgb[channel]
            self.screen.blit(
                self.small.render(f"{label}   {value}", True, (172, 178, 193)),
                (slider.x, slider.y - 18),
            )
            pygame.draw.rect(self.screen, (40, 44, 54), slider, border_radius=4)
            fill_width = round(slider.width * value / 255)
            if fill_width > 0:
                pygame.draw.rect(
                    self.screen, accents[channel], (slider.x, slider.y, fill_width, slider.height),
                    border_radius=4,
                )
            pygame.draw.rect(self.screen, (78, 84, 100), slider, 1, border_radius=4)
            self.buttons.append((slider.inflate(0, 14), f"color_slider:{channel}", f"{label} channel"))

        apply_rect = pygame.Rect(x, panel.bottom - 56, panel.width - 48, 32)
        self._button(apply_rect, "apply_color_picker", "Apply & save to custom colors", True)

    def _handle_color_picker_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.color_picker_open = False
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            action = next(
                (
                    action for rect, action, _label in reversed(self.buttons)
                    if rect.collidepoint(event.pos)
                ),
                None,
            )
            if action == "close_color_picker":
                self.color_picker_open = False
            elif action == "apply_color_picker":
                self._begin_change()
                self._apply_custom_color(tuple(self.color_picker_rgb))
                self._commit_change()
                self.color_picker_open = False
            elif action and action.startswith("color_slider:"):
                channel = int(action.split(":", 1)[1])
                self.drag_mode = f"color_slider:{channel}"
                self._set_color_channel_from_x(channel, event.pos[0])
            return
        if event.type == pygame.MOUSEMOTION and (self.drag_mode or "").startswith("color_slider:"):
            channel = int(self.drag_mode.split(":", 1)[1])
            self._set_color_channel_from_x(channel, event.pos[0])
            return
        if event.type == pygame.MOUSEBUTTONUP and (self.drag_mode or "").startswith("color_slider:"):
            self.drag_mode = None

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

        version_text = f"v{APP_VERSION}" + (f" · {self.git_commit}" if self.git_commit else "")
        version_surface = self.small.render(version_text, True, (112, 123, 145))
        self.screen.blit(
            version_surface, (panel.right - version_surface.get_width() - 28, panel.bottom - 31),
        )

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
            ("RLE mapped bank · canvas edits excluded until saved · 150 KiB effect budget"
             if self._bank_is_compressed() else
             "V4 baked RGB memory planner · 150 KiB flash · embedded editable projects"),
            True, (143, 153, 174),
        )
        self.screen.blit(subtitle, (x, y + 31))
        backup_path = self._export_bank_backup_path(self.export_bank_path or EXPORT_FILE)
        if backup_path.is_file():
            self._button(
                pygame.Rect(panel.right - 616, y, 126, 32),
                "export_bank_restore_backup", "Restore backup", False,
            )
        self._button(pygame.Rect(panel.right - 480, y, 126, 32), "export_bank_map", "Map header…", False)
        errors = self._export_bank_validation_errors()
        self._button(
            pygame.Rect(panel.right - 344, y, 126, 32), "export_bank_write_as",
            "Save as…", not errors,
        )
        self._button(
            pygame.Rect(panel.right - 210, y, 126, 32), "export_bank_write",
            "Save bank" if self.export_bank_path else "Choose file…", not errors,
        )
        self._button(pygame.Rect(panel.right - 76, y, 50, 32), "export_bank_close", "×", False)
        self._button(pygame.Rect(panel.right - 210, y + 38, 126, 28), "export_bank_convert", "Convert…", False)

        used = self._export_bank_used_bytes()
        free = max(0, EFFECT_BANK_CAPACITY - used)
        frame_ms = max(1, int(self.project.frame_ms or 50))
        free_seconds = (free // 204) * frame_ms / 1000.0
        if self._bank_is_compressed():
            stored_seconds = sum(len(e.frames) * e.frame_ms for e in self.export_bank_effects) / 1000.0
            free_seconds = free * stored_seconds / used if used else 0.0
        capacity_y = panel.y + 83
        # Cheap replace-by-id total (mirrors _export_bank_used_bytes): the
        # project's own duration_ms is used directly instead of baking its
        # frames just to call total_playback_ms on it every draw.
        replaced_ms = sum(
            effect.duration_ms for effect in self.export_bank_effects
            if effect.effect_id == self.project.effect_id
        )
        bank_seconds = (
            self.project.duration_ms + total_playback_ms(self.export_bank_effects) - replaced_ms
        ) / 1000.0
        summary = (
            f"{used / 1024:.1f} KiB used  /  {EFFECT_BANK_CAPACITY / 1024:.0f} KiB   ·   "
            f"{free / 1024:.1f} KiB free   ·   ≈{free_seconds:.1f}s at {1000 / frame_ms:.2f} FPS   ·   "
            f"{bank_seconds:.1f}s total bank playback"
        )
        if self._bank_is_compressed():
            summary = (f"RLE bank: {used / 1024:.1f} / {EFFECT_BANK_CAPACITY / 1024:.0f} KiB · "
                       f"{free / 1024:.1f} KiB free · ~{free_seconds:.1f}s more at bank average")
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
            *[(index, effect.name, self._bank_effect_bytes(effect)) for index, effect in enumerate(self.export_bank_effects)],
            (-1, self.project.name, self.project.flash_bytes),
        ]
        if self._bank_is_compressed():
            entries = entries[:-1]
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

        toolbar, list_rect, detail = self._export_bank_layout()

        sort_rect = pygame.Rect(toolbar.x, toolbar.y, 128, toolbar.height)
        self._button(sort_rect, "export_bank_sort_cycle", f"Sort: {self.export_bank_sort_mode.title()}", False)
        search_rect = pygame.Rect(sort_rect.right + 10, toolbar.y, 240, toolbar.height)
        pygame.draw.rect(self.screen, (42, 47, 58), search_rect, border_radius=4)
        pygame.draw.rect(
            self.screen, ACCENT if self.export_bank_query_editing else (74, 81, 98),
            search_rect, 1, border_radius=4,
        )
        placeholder = not self.export_bank_query and not self.export_bank_query_editing
        query_value = "Search bank…" if placeholder else self.export_bank_query
        if self.export_bank_query_editing and (pygame.time.get_ticks() // 500) % 2 == 0:
            query_value += "|"
        query_color = (129, 138, 158) if placeholder else (238, 241, 247)
        self.screen.blit(
            self.small.render(self._fit_text(query_value, self.small, search_rect.width - 16), True, query_color),
            (search_rect.x + 8, search_rect.y + 8),
        )
        self.buttons.append((search_rect, "export_bank_query_focus", "Search bank"))
        if self.export_bank_query:
            self._button(pygame.Rect(search_rect.right + 8, toolbar.y, 30, toolbar.height), "export_bank_query_clear", "×", False)

        visible_pairs = self._export_bank_visible_pairs()
        if self.export_bank_query:
            count_surface = self.small.render(
                f"{len(visible_pairs)}/{len(self.export_bank_effects)} shown", True, (159, 168, 187),
            )
            self.screen.blit(count_surface, (toolbar.right - count_surface.get_width(), toolbar.y + 8))

        list_entries = [
            *[(index, effect.name, self._bank_effect_bytes(effect)) for index, effect in visible_pairs],
            (-1, self.project.name, self.project.flash_bytes),
        ]

        self.screen.blit(self.font.render("BANK CONTENT", True, (211, 216, 228)), (list_rect.x, list_rect.y - 29))
        self.screen.blit(self.font.render("SELECTED EFFECT", True, (211, 216, 228)), (detail.x, detail.y - 29))
        pygame.draw.rect(self.screen, (20, 23, 30), list_rect, border_radius=6)
        pygame.draw.rect(self.screen, (20, 23, 30), detail, border_radius=6)
        pygame.draw.rect(self.screen, (57, 63, 77), list_rect, 1, border_radius=6)
        pygame.draw.rect(self.screen, (57, 63, 77), detail, 1, border_radius=6)

        visible = max(1, list_rect.height // 53)
        maximum_scroll = max(0, len(list_entries) - visible)
        self.export_bank_scroll = min(self.export_bank_scroll, maximum_scroll)
        for visible_index, (entry_index, name, byte_count) in enumerate(
            list_entries[self.export_bank_scroll:self.export_bank_scroll + visible]
        ):
            row = pygame.Rect(list_rect.x + 7, list_rect.y + 7 + visible_index * 53, list_rect.width - 14, 46)
            selected = entry_index == self.export_bank_selected
            pygame.draw.rect(self.screen, (47, 58, 78) if selected else (31, 35, 44), row, border_radius=5)
            color = BANK_COLORS[self._export_bank_color_index(entry_index) % len(BANK_COLORS)]
            bar_width = max(3, round(byte_count / EFFECT_BANK_CAPACITY * (row.width - 8)))
            pygame.draw.rect(self.screen, color, (row.x + 4, row.bottom - 7, min(row.width - 8, bar_width), 3), border_radius=2)
            if entry_index == -1:
                effect_id, frames, timing = self.project.effect_id, self.project.stored_frame_count, int(self.project.frame_ms or 1)
                marker = "CURRENT"
            else:
                effect = self.export_bank_effects[entry_index]
                effect_id, frames, timing = effect.effect_id, len(effect.frames), effect.frame_ms
                marker = "BANK+PROJECT" if effect.project_data is not None else "BANK"

            thumb_size = 34
            thumb_rect = pygame.Rect(row.x + 5, row.y + 4, thumb_size, thumb_size)
            pygame.draw.rect(self.screen, (14, 16, 21), thumb_rect, border_radius=4)
            thumbnail = (
                self._render_led_thumbnail(self._preview_led_colors(), thumb_size)
                if entry_index == -1 else self._effect_thumbnail(effect, thumb_size)
            )
            self.screen.blit(thumbnail, thumb_rect.topleft)
            pygame.draw.rect(self.screen, (57, 63, 77), thumb_rect, 1, border_radius=4)

            label_x = thumb_rect.right + 10
            label = self._fit_text(
                f"{marker}  ·  ID {effect_id}  ·  {name}", self.small,
                max(20, row.right - label_x - 90),
            )
            self.screen.blit(self.small.render(label, True, (232, 235, 242)), (label_x, row.y + 7))
            stats = f"{byte_count / 1024:.1f}K  {frames * timing / 1000:.2f}s"
            stats_surface = self.small.render(stats, True, (159, 168, 187))
            self.screen.blit(stats_surface, (row.right - stats_surface.get_width() - 9, row.y + 7))
            self.buttons.append((row, f"export_bank_select:{entry_index}", name))

        self._draw_export_bank_details(detail)
        status_color = (255, 110, 98) if errors else (102, 218, 163)
        status = self.export_bank_status if self.export_bank_status.startswith("Converted ") else (" · ".join(errors) if errors else self.export_bank_status)
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
        byte_count = self.project.flash_bytes if current else self._bank_effect_bytes(effect)
        x, y = detail.x + 14, detail.y + 16
        badge = "CURRENT PROJECT" if current else "MAPPED HEADER EFFECT"
        color_index = self._export_bank_color_index(-1 if current else self.export_bank_selected)
        self.screen.blit(self.small.render(badge, True, BANK_COLORS[color_index % len(BANK_COLORS)]), (x, y))
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
        if current:
            existing = next(
                (item for item in self.export_bank_effects if item.effect_id == effect_id),
                None,
            )
            metadata.append((
                "On save",
                f'REPLACE ID {effect_id} · {existing.name}' if existing else f"ADD ID {effect_id}",
            ))
        for label, value in metadata:
            self.screen.blit(self.small.render(label, True, (132, 141, 160)), (x, y))
            rendered = self.small.render(value, True, (220, 225, 235))
            self.screen.blit(rendered, (detail.right - rendered.get_width() - 14, y))
            y += 20
        y += 14
        # Placed right after the metadata block (not anchored to detail.bottom)
        # so they can never slide up and overlap the last metadata rows on a
        # shorter panel - that anchoring is what caused the overlap before.
        if not current:
            if effect.project_data is not None:
                self._button(
                    pygame.Rect(x, y, detail.width - 28, 32),
                    "export_bank_load_project", "Load editable project", True,
                )
                y += 40
            else:
                self.screen.blit(
                    self.small.render("No project bundle in this header effect", True, (132, 141, 160)),
                    (x, y + 8),
                )
                y += 32
            self._button(
                pygame.Rect(x, y, detail.width - 28, 32),
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

    def _numeric_property_widget(
        self,
        rect: pygame.Rect,
        spec: NumericPropertySpec,
        value: float | int,
        action: str,
        *,
        editing: bool = False,
        input_text: str = "",
        key_action: str | None = None,
        key_active: bool = False,
    ) -> pygame.Rect:
        key_width = 39 if key_action else 0
        field = pygame.Rect(rect.x, rect.y, rect.width - key_width, rect.height)
        hovered = field.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(
            self.screen, (49, 57, 72) if editing or hovered else (40, 44, 54),
            rect, border_radius=4,
        )
        pygame.draw.rect(
            self.screen, ACCENT if editing or hovered else (67, 72, 86),
            field, 1, border_radius=4,
        )
        self.screen.blit(
            self.small.render(spec.label, True, (140, 148, 168)),
            (field.x + 8, field.y + 8),
        )
        value_text = input_text if editing else spec.display_text(value)
        if editing and (pygame.time.get_ticks() // 500) % 2 == 0:
            value_text += "|"
        rendered = self.small.render(value_text, True, (235, 238, 245))
        self.screen.blit(
            rendered,
            (field.right - rendered.get_width() - 8, field.y + 8),
        )
        self.buttons.append((field, action, f"{spec.label} — drag or click to type"))
        if key_action:
            self._button(
                pygame.Rect(rect.right - 35, rect.y + 3, 31, rect.height - 6),
                key_action, "+K", key_active,
            )
        return field

    def _draw_color_palette(self, x: int, y: int, current_color: tuple[int, int, int]) -> int:
        """Draws the built-in palette (2 rows of 8), the personal custom-
        colour row, and a "+" swatch that opens the mixer - shared by the
        shape/random-LED/gradient-stop colour sections so all three stay in
        sync. Returns the y position right after everything drawn, so the
        caller can keep laying out further controls below it."""
        current_color = tuple(current_color)
        for row in range(2):
            for col in range(8):
                index = row * 8 + col
                color = PALETTE[index]
                rect = pygame.Rect(x + col * 34, y + row * 34, 27, 27)
                pygame.draw.rect(self.screen, color, rect, border_radius=4)
                if current_color == color:
                    pygame.draw.rect(self.screen, (245, 247, 252), rect.inflate(4, 4), 2, border_radius=5)
                self.buttons.append((rect, f"color:{index}", ""))
        y += 2 * 34 + 12
        for index, color in enumerate(self.custom_colors):
            color = tuple(color)
            rect = pygame.Rect(x + index * 34, y, 27, 27)
            pygame.draw.rect(self.screen, color, rect, border_radius=4)
            if current_color == color:
                pygame.draw.rect(self.screen, (245, 247, 252), rect.inflate(4, 4), 2, border_radius=5)
            self.buttons.append((rect, f"custom_color:{index}", ""))
        edit_rect = pygame.Rect(x + len(self.custom_colors) * 34, y, 27, 27)
        pygame.draw.rect(self.screen, (45, 49, 61), edit_rect, border_radius=4)
        pygame.draw.rect(
            self.screen, ACCENT if edit_rect.collidepoint(pygame.mouse.get_pos()) else (110, 118, 136),
            edit_rect, 1, border_radius=4,
        )
        plus = self.small.render("+", True, (216, 221, 232))
        self.screen.blit(plus, plus.get_rect(center=edit_rect.center))
        self.buttons.append((edit_rect, "open_color_picker", "Mix a custom color"))
        return y + 27

    def _button(self, rect: pygame.Rect, action: str, label: str, active: bool) -> None:
        hovered = rect.collidepoint(pygame.mouse.get_pos())
        action_base = action.split(":", 1)[0]
        destructive = action_base in {"delete", "delete_layer", "export_bank_remove", "exit"}
        if active:
            fill = (65, 94, 154)
            border = (112, 174, 255)
        elif hovered and destructive:
            fill = (93, 48, 52)
            border = (255, 116, 108)
        elif hovered:
            fill = (61, 68, 86)
            border = (126, 144, 176)
        else:
            fill = (45, 50, 63)
            border = (78, 86, 104)
        pygame.draw.rect(self.screen, (20, 23, 30), rect.move(0, 1), border_radius=4)
        pygame.draw.rect(self.screen, fill, rect, border_radius=4)
        pygame.draw.rect(self.screen, border, rect, 1, border_radius=4)
        if active:
            pygame.draw.line(
                self.screen, (125, 190, 255),
                (rect.x + 5, rect.bottom - 2), (rect.right - 5, rect.bottom - 2), 2,
            )
        ink = (255, 225, 222) if hovered and destructive else (245, 246, 250)
        text = self.small.render(label, True, ink)
        self.screen.blit(text, text.get_rect(center=rect.center))
        self.buttons.append((rect, action, label))

    def _transport_button(
        self, rect: pygame.Rect, action: str, icon: str, active: bool = False,
    ) -> None:
        hovered = rect.collidepoint(pygame.mouse.get_pos())
        color = (77, 99, 150) if active else (58, 65, 82) if hovered else (49, 54, 68)
        pygame.draw.rect(self.screen, color, rect, border_radius=4)
        pygame.draw.rect(
            self.screen, ACCENT if active else (91, 98, 117), rect, 1, border_radius=4,
        )
        ink = (246, 248, 252)
        center_x, center_y = rect.center
        if icon == "stop":
            pygame.draw.rect(self.screen, ink, pygame.Rect(center_x - 5, center_y - 5, 10, 10))
        elif icon == "loop":
            pygame.draw.line(
                self.screen, ink, (center_x - 8, center_y - 7), (center_x - 8, center_y + 7), 2,
            )
            pygame.draw.line(
                self.screen, ink, (center_x + 8, center_y - 7), (center_x + 8, center_y + 7), 2,
            )
            pygame.draw.polygon(self.screen, ink, [
                (center_x - 3, center_y - 6), (center_x + 4, center_y), (center_x - 3, center_y + 6),
            ])
        else:
            points = (
                [(center_x - 5, center_y - 7), (center_x + 7, center_y), (center_x - 5, center_y + 7)]
                if icon in {"play", "next"} else
                [(center_x + 5, center_y - 7), (center_x - 7, center_y), (center_x + 5, center_y + 7)]
            )
            if icon == "previous":
                pygame.draw.line(
                    self.screen, ink, (center_x - 8, center_y - 7),
                    (center_x - 8, center_y + 7), 2,
                )
                points = [(x + 2, y) for x, y in points]
            elif icon == "next":
                pygame.draw.line(
                    self.screen, ink, (center_x + 8, center_y - 7),
                    (center_x + 8, center_y + 7), 2,
                )
                points = [(x - 2, y) for x, y in points]
            pygame.draw.polygon(self.screen, ink, points)
        label = {
            "previous": "Previous frame", "play": "Play", "stop": "Stop", "next": "Next frame",
            "loop": "Play Loop",
        }[icon]
        self.buttons.append((rect, action, label))


def _parse_resolution(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().replace(" ", "").split("x", 1)
        width, height = int(width_text), int(height_text)
    except (AttributeError, ValueError) as error:
        raise argparse.ArgumentTypeError("Resolution must use WIDTHxHEIGHT, for example 1280x1024") from error
    if width < MIN_WINDOW_SIZE[0] or height < MIN_WINDOW_SIZE[1]:
        raise argparse.ArgumentTypeError(
            f"Resolution must be at least {MIN_WINDOW_SIZE[0]}x{MIN_WINDOW_SIZE[1]}"
        )
    return width, height


@lru_cache(maxsize=4)
def get_git_commit(repo_dir: str | Path = ROOT) -> str | None:
    """Short commit hash of the running checkout, or None if git/the repo
    isn't available (e.g. a packaged build with no .git directory). Used
    for the Help panel's version line - knowing the exact commit matters
    here since APP_VERSION is a hand-maintained string, not bumped for
    every change, and the auto-updater can move a Pi to a new commit any
    time it restarts.

    Cached (per repo_dir): a real run only ever constructs one Editor, but
    the test suite constructs hundreds - without caching, each one shells
    out to git and roughly doubles the suite's runtime for no benefit."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit or None


def format_crash_entry(exc: BaseException) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return f"{_CRASH_LOG_SEPARATOR}[{timestamp}]\n{body}"


def log_crash(exc: BaseException, path: str | Path = CRASH_LOG_FILE) -> None:
    """Append a timestamped traceback to a small rolling crash log (never
    grows unbounded - only the last CRASH_LOG_MAX_ENTRIES are kept) so a
    crash on a headless Pi (stderr goes nowhere visible) leaves a trail
    instead of just vanishing. Best-effort: logging itself must never be
    what crashes the crash handler."""
    path = Path(path)
    entry = format_crash_entry(exc)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        previous_entries = [part for part in existing.split(_CRASH_LOG_SEPARATOR) if part.strip()]
        kept = previous_entries[-(CRASH_LOG_MAX_ENTRIES - 1):]
        rebuilt = "".join(_CRASH_LOG_SEPARATOR + part for part in kept)
        path.write_text(rebuilt + entry, encoding="utf-8")
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="CnC Pinball Light Editor")
    parser.add_argument("--smoke-test", action="store_true", help="Render three frames, then exit")
    parser.add_argument("--screenshot", type=Path, help="Write a screenshot during smoke test")
    parser.add_argument("--effect-data", type=Path, help="Load and preview an Arduino effect_data.h")
    parser.add_argument(
        "--resolution", type=_parse_resolution,
        default=_parse_resolution(os.environ.get("CNC_LIGHT_EDITOR_RESOLUTION", "1280x1024")),
        help="Initial window size as WIDTHxHEIGHT (default: 1280x1024)",
    )
    parser.add_argument("--fullscreen", action="store_true", help="Use a fullscreen Raspberry Pi display")
    parser.add_argument(
        "--fps", type=int,
        help="Preview/UI frame cap (Pi 3 defaults to 20; export FPS is unchanged)",
    )
    parser.add_argument(
        "--no-auto-update", action="store_true",
        default=os.environ.get("CNC_LIGHT_EDITOR_AUTO_UPDATE", "1").strip().lower() in {"0", "false", "no"},
        help="Do not check, install, and restart after GitHub updates",
    )
    args = parser.parse_args()
    if args.smoke_test:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    # The editor never plays sound (no pygame.mixer usage anywhere), but
    # pygame.init() unconditionally starts SDL's audio subsystem too. On a
    # Pi with no usable ALSA output, that spins a hotplug/underrun-recovery
    # thread that pins a full CPU core for as long as the app runs. Force
    # the no-op audio backend so SDL never touches ALSA in the first place.
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.init()
    if WINDOW_ICON.is_file():
        pygame.display.set_icon(pygame.image.load(WINDOW_ICON))
    flags = pygame.FULLSCREEN if args.fullscreen else pygame.RESIZABLE
    screen = pygame.display.set_mode(args.resolution, flags)
    pygame.display.set_caption("CnC Pinball — Light Effect Editor")
    restart_requested = False
    editor: Editor | None = None
    try:
        editor = Editor(
            screen,
            check_recovery=not args.smoke_test,
            target_fps=args.fps,
        )
        if args.effect_data:
            editor.load_effect_data_file(args.effect_data)
        restart_requested = editor.run(
            args.smoke_test,
            args.screenshot,
            auto_update=not args.no_auto_update,
        )
    except Exception as exc:
        log_crash(exc)
        if editor is not None:
            try:
                editor._maybe_autosave(force=True)
            except Exception:
                pass  # the crash log already has the real error - don't mask it
        raise
    finally:
        pygame.quit()
    if restart_requested:
        os.execv(
            sys.executable,
            [sys.executable, "-m", "cnc_light_editor.app", *sys.argv[1:]],
        )


if __name__ == "__main__":
    main()
