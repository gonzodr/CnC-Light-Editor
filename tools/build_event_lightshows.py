from __future__ import annotations

from pathlib import Path

from cnc_light_editor.model import Keyframe, Layer, Project, RandomLedEffect, Shape, StrobeEffect


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "projects" / "event_lightshows"


def keys(target, prop: str, values: list[tuple[int, object, str]]) -> None:
    target.keyframes[prop] = [Keyframe(t, value, easing=easing) for t, value, easing in values]


def project(effect_id: int, name: str, duration_ms: int, *layers: Layer) -> Project:
    return Project(
        name=name, duration_ms=duration_ms, fps=20, frame_ms=50,
        effect_id=effect_id, loops=1, loop_frames=0, intro_frames=0,
        overlay=True, layers=list(layers),
    )


def fade(shape: Shape, attack: int, peak: int, release: int, end: int, opacity: float = 1.0) -> Shape:
    keys(shape, "opacity", [
        (0, 0.0, "linear"), (attack, 0.0, "linear"),
        (peak, opacity, "ease_out"), (release, opacity * 0.75, "linear"),
        (end, 0.0, "ease_in"),
    ])
    return shape


def cnc_complete() -> Project:
    source_layer = Layer("C&C ignition")
    source = Shape(
        "ellipse", "C&C green ignition", x=0.15, y=0.58, width=0.04, height=0.04,
        color=(218, 255, 44), opacity=0.0, feather=0.10,
    )
    keys(source, "width", [(0, 0.03, "ease_out"), (380, 0.34, "ease_out"), (1150, 0.65, "ease_in")])
    keys(source, "height", [(0, 0.03, "ease_out"), (380, 0.26, "ease_out"), (1150, 0.55, "ease_in")])
    fade(source, 0, 100, 450, 1200)
    source_layer.shapes.append(source)

    split = Layer("Cheech and Chong split")
    for name, y, rotation, color, delay in (
        ("Chong unlock trail", 0.50, -7.0, (112, 255, 30), 170),
        ("Cheech unlock trail", 0.40, -18.0, (255, 220, 45), 260),
    ):
        trail = Shape(
            "line", name, x=0.18, y=0.57, width=0.03, height=0.025,
            rotation=rotation, color=color, opacity=0.0,
            fill_mode="stroke", stroke_width=0.026, feather=0.035,
        )
        keys(trail, "x", [(delay, 0.18, "ease_out"), (720, 0.50, "ease_in_out"), (1080, 0.69, "ease_out")])
        keys(trail, "y", [(delay, 0.57, "ease_out"), (720, y, "ease_in_out"), (1080, y - 0.02, "ease_out")])
        keys(trail, "width", [(delay, 0.04, "ease_out"), (720, 0.34, "ease_in_out"), (1080, 0.16, "ease_in")])
        fade(trail, delay, delay + 90, 860, 1250)
        split.shapes.append(trail)

    crown = Layer("Unlocked sparkle crown")
    stars = RandomLedEffect(
        name="Green gold unlock stars", seed=2121, life_ms=260, born_speed=28,
        particle_count=16, color=(224, 255, 82), opacity=0.0,
    )
    keys(stars, "opacity", [(0, 0.0, "linear"), (420, 0.0, "linear"), (570, 0.95, "ease_out"), (1100, 0.70, "linear"), (1350, 0.0, "ease_in")])
    crown.effects.append(stars)
    return project(21, "C&C Complete", 1400, source_layer, split, crown)


def dave_ball_save() -> Project:
    layer = Layer("DAVE shield convergence")
    for name, x0, color, delay in (
        ("Left shield arc", 0.07, (80, 180, 255), 0),
        ("Right shield arc", 0.86, (188, 235, 255), 80),
    ):
        orb = Shape("ellipse", name, x=x0, y=0.69, width=0.12, height=0.10, color=color, opacity=0.0, feather=0.08)
        keys(orb, "x", [(delay, x0, "ease_out"), (650, 0.455, "ease_in_out"), (900, 0.455, "linear")])
        keys(orb, "y", [(delay, 0.69, "ease_out"), (650, 0.855, "ease_in_out"), (900, 0.855, "linear")])
        keys(orb, "width", [(delay, 0.08, "ease_out"), (520, 0.28, "ease_in_out"), (920, 0.10, "ease_in")])
        keys(orb, "height", [(delay, 0.07, "ease_out"), (520, 0.22, "ease_in_out"), (920, 0.08, "ease_in")])
        fade(orb, delay, delay + 100, 720, 980, 0.95)
        layer.shapes.append(orb)
    shield = Shape("ellipse", "Ball save shield", x=0.455, y=0.855, width=0.05, height=0.04, color=(235, 252, 255), opacity=0.0, feather=0.11, fill_mode="stroke", stroke_width=0.04)
    keys(shield, "width", [(540, 0.05, "ease_out"), (850, 0.50, "ease_out"), (1140, 0.76, "ease_in")])
    keys(shield, "height", [(540, 0.04, "ease_out"), (850, 0.28, "ease_out"), (1140, 0.45, "ease_in")])
    fade(shield, 500, 620, 850, 1150)
    layer.shapes.append(shield)
    return project(22, "DAVE Ball Save Lit", 1200, layer)


def drift() -> Project:
    layer = Layer("Lowrider drift streaks")
    for index, (color, y, delay, rotation) in enumerate((
        ((255, 68, 18), 0.30, 0, 13.0),
        ((255, 174, 20), 0.40, 70, 9.0),
        ((255, 238, 120), 0.50, 140, 5.0),
    ), start=1):
        streak = Shape("line", f"Drift streak {index}", x=-0.20, y=y, width=0.48, height=0.024, rotation=rotation, color=color, opacity=0.0, fill_mode="stroke", stroke_width=0.025, feather=0.035)
        keys(streak, "x", [(delay, -0.20, "ease_in"), (520 + delay, 0.70, "ease_out"), (780 + delay, 1.20, "ease_in")])
        keys(streak, "rotation", [(delay, rotation, "ease_out"), (780 + delay, rotation - 24.0, "ease_in")])
        fade(streak, delay, delay + 60, 520 + delay, min(850, 820 + delay))
        layer.shapes.append(streak)
    sparks = RandomLedEffect(name="Tyre sparks", seed=2323, life_ms=170, born_speed=34, particle_count=12, color=(255, 192, 34), opacity=0.0)
    keys(sparks, "opacity", [(0, 0.0, "linear"), (180, 0.85, "ease_out"), (620, 0.72, "linear"), (850, 0.0, "ease_in")])
    layer.effects.append(sparks)
    return project(23, "Drift", 900, layer)


def gift_collected(
    effect_id: int,
    name: str,
    *,
    origin_x: float,
    origin_y: float,
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
    sparkle: tuple[int, int, int],
    seed: int,
) -> Project:
    burst = Layer(f"{name} burst")
    ring = Shape("ellipse", f"{name} ribbon ring", x=origin_x, y=origin_y, width=0.04, height=0.03, color=primary, opacity=0.0, feather=0.07, fill_mode="stroke", stroke_width=0.035)
    keys(ring, "width", [(0, 0.03, "ease_out"), (420, 0.85, "ease_out"), (850, 1.30, "ease_in")])
    keys(ring, "height", [(0, 0.02, "ease_out"), (420, 0.58, "ease_out"), (850, 0.95, "ease_in")])
    fade(ring, 0, 80, 430, 900)
    burst.shapes.append(ring)
    for rotation, color in ((28.0, primary), (-28.0, secondary)):
        ribbon = Shape("line", f"{name} ribbon", x=origin_x, y=origin_y, width=0.05, height=0.03, rotation=rotation, color=color, opacity=0.0, fill_mode="stroke", stroke_width=0.03, feather=0.03)
        keys(ribbon, "width", [(100, 0.04, "ease_out"), (520, 1.05, "ease_out"), (900, 1.35, "ease_in")])
        fade(ribbon, 80, 150, 520, 920)
        burst.shapes.append(ribbon)
    confetti = RandomLedEffect(name=f"{name} confetti", seed=seed, life_ms=320, born_speed=38, particle_count=22, color=sparkle, opacity=0.0)
    keys(confetti, "opacity", [(0, 0.0, "linear"), (120, 1.0, "ease_out"), (650, 0.85, "linear"), (950, 0.0, "ease_in")])
    burst.effects.append(confetti)
    return project(effect_id, name, 1000, burst)


def ufo_ball_back() -> Project:
    layer = Layer("UFO ball return")
    wave = Shape("ellipse", "UFO cyan release wave", x=0.74, y=0.43, width=0.04, height=0.025, color=(45, 225, 255), opacity=0.0, feather=0.10, fill_mode="stroke", stroke_width=0.035)
    keys(wave, "y", [(0, 0.43, "ease_out"), (620, 0.68, "ease_out"), (799, 0.78, "ease_in")])
    keys(wave, "x", [(0, 0.74, "ease_out"), (620, 0.56, "ease_out"), (799, 0.48, "ease_in")])
    keys(wave, "width", [(0, 0.04, "ease_out"), (450, 0.85, "ease_out"), (799, 1.25, "ease_in")])
    keys(wave, "height", [(0, 0.025, "ease_out"), (450, 0.36, "ease_out"), (799, 0.56, "ease_in")])
    fade(wave, 0, 60, 380, 750)
    core = Shape("ellipse", "UFO release core", x=0.74, y=0.43, width=0.05, height=0.04, color=(220, 255, 235), opacity=0.0, feather=0.07)
    keys(core, "width", [(0, 0.03, "ease_out"), (220, 0.28, "ease_out"), (620, 0.10, "ease_in")])
    keys(core, "height", [(0, 0.02, "ease_out"), (220, 0.18, "ease_out"), (620, 0.06, "ease_in")])
    fade(core, 0, 50, 260, 690)
    beam = Shape("line", "UFO eject beam", x=0.66, y=0.53, width=0.04, height=0.025, rotation=55.0, color=(105, 255, 145), opacity=0.0, fill_mode="stroke", stroke_width=0.03, feather=0.04)
    keys(beam, "width", [(80, 0.04, "ease_out"), (380, 0.52, "ease_out"), (720, 0.90, "ease_in")])
    keys(beam, "x", [(80, 0.73, "ease_out"), (520, 0.60, "ease_out"), (720, 0.51, "ease_in")])
    keys(beam, "y", [(80, 0.44, "ease_out"), (520, 0.61, "ease_out"), (720, 0.74, "ease_in")])
    fade(beam, 50, 100, 430, 750)
    layer.shapes.extend([wave, core, beam])
    return project(25, "UFO Ball Back", 800, layer)


def bonus_x() -> Project:
    layer = Layer("Bonus multiplier staircase")
    xs = (0.338, 0.415, 0.494, 0.571)
    for index, x in enumerate(xs):
        delay = index * 90
        step = Shape("ellipse", f"Bonus step {index + 1}", x=x, y=0.755, width=0.055, height=0.045, color=(255, 184 + index * 18, 38 + index * 18), opacity=0.0, feather=0.055)
        keys(step, "width", [(delay, 0.025, "ease_out"), (delay + 180, 0.20, "ease_out"), (delay + 600, 0.11, "ease_in")])
        keys(step, "height", [(delay, 0.02, "ease_out"), (delay + 180, 0.16, "ease_out"), (delay + 600, 0.08, "ease_in")])
        fade(step, delay, delay + 40, delay + 300, min(850, delay + 690))
        layer.shapes.append(step)
    sweep = Shape("line", "Bonus gold underline", x=0.455, y=0.77, width=0.06, height=0.025, color=(255, 245, 150), opacity=0.0, fill_mode="stroke", stroke_width=0.026, feather=0.03)
    keys(sweep, "width", [(180, 0.05, "ease_out"), (580, 0.55, "ease_out"), (850, 0.82, "ease_in")])
    fade(sweep, 140, 200, 580, 850)
    layer.shapes.append(sweep)
    return project(26, "Bonus X Level Up", 900, layer)


def beer_full() -> Project:
    layer = Layer("Fishtank foam overflow")
    bubble_specs = (
        (0.750, 0.575, 0, 0.17), (0.820, 0.610, 90, 0.13),
        (0.690, 0.600, 150, 0.11), (0.775, 0.525, 220, 0.15),
    )
    for index, (x, y, delay, size) in enumerate(bubble_specs, start=1):
        bubble = Shape("ellipse", f"Beer bubble {index}", x=x, y=y, width=0.03, height=0.03, color=(170, 244, 255), opacity=0.0, feather=0.045, fill_mode="stroke", stroke_width=0.026)
        keys(bubble, "y", [(delay, y, "ease_out"), (delay + 600, y - 0.35, "ease_in_out"), (min(1099, delay + 850), y - 0.48, "ease_in")])
        keys(bubble, "width", [(delay, 0.025, "ease_out"), (delay + 320, size, "ease_out"), (min(1099, delay + 800), size * 0.55, "ease_in")])
        keys(bubble, "height", [(delay, 0.025, "ease_out"), (delay + 320, size, "ease_out"), (min(1099, delay + 800), size * 0.55, "ease_in")])
        fade(bubble, delay, delay + 70, min(900, delay + 520), min(1050, delay + 840))
        layer.shapes.append(bubble)
    foam = RandomLedEffect(name="Foam sparkle", seed=2727, life_ms=240, born_speed=30, particle_count=14, color=(225, 252, 255), opacity=0.0)
    keys(foam, "opacity", [(0, 0.0, "linear"), (180, 0.85, "ease_out"), (760, 0.70, "linear"), (1050, 0.0, "ease_in")])
    layer.effects.append(foam)
    return project(27, "Beer Full", 1100, layer)


def multiball_end() -> Project:
    layer = Layer("Multiball energy collapse")
    for index, (color, direction, delay) in enumerate((
        ((186, 70, 255), 1.5, 0), ((126, 255, 38), -1.5, 80),
    ), start=1):
        spiral = Shape("line", f"Collapse spiral {index}", x=0.50, y=0.48, width=1.35, height=0.03, rotation=-25 + index * 90, color=color, opacity=0.0, fill_mode="stroke", stroke_width=0.032, feather=0.04)
        keys(spiral, "width", [(delay, 1.35, "ease_in"), (650, 0.16, "ease_in_out"), (880, 0.03, "ease_out")])
        keys(spiral, "rotation_turns", [(delay, 0.0, "ease_in"), (880, direction, "ease_out")])
        fade(spiral, delay, delay + 50, 560, 900)
        layer.shapes.append(spiral)
    core = Shape("ellipse", "Last ball energy dot", x=0.50, y=0.48, width=0.20, height=0.15, color=(238, 255, 185), opacity=0.0, feather=0.08)
    keys(core, "width", [(520, 0.24, "ease_in"), (760, 0.055, "ease_in"), (930, 0.01, "ease_out")])
    keys(core, "height", [(520, 0.18, "ease_in"), (760, 0.04, "ease_in"), (930, 0.01, "ease_out")])
    fade(core, 460, 560, 720, 950)
    layer.shapes.append(core)
    return project(28, "Multiball End", 1000, layer)


def build_all() -> list[Project]:
    return [
        cnc_complete(), dave_ball_save(), drift(),
        gift_collected(
            24, "Gift Cheech", origin_x=0.658, origin_y=0.40,
            primary=(255, 42, 34), secondary=(255, 126, 28),
            sparkle=(255, 92, 54), seed=2424,
        ),
        ufo_ball_back(), bonus_x(), beer_full(), multiball_end(),
        gift_collected(
            29, "Gift Chong", origin_x=0.346, origin_y=0.49,
            primary=(25, 115, 255), secondary=(30, 230, 255),
            sparkle=(90, 190, 255), seed=2929,
        ),
    ]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filenames = {
        24: "24_gift_cheech.cnclight",
        25: "25_ufo_ball_back.cnclight",
    }
    for item in build_all():
        target = OUTPUT_DIR / filenames.get(
            item.effect_id,
            f"{item.effect_id:02d}_{item.name.lower().replace(' ', '_').replace('&', 'and')}.cnclight",
        )
        item.save(target)
        print(f"ID {item.effect_id}: {item.name} — {item.duration_ms} ms — {target}")


if __name__ == "__main__":
    main()
