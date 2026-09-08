from __future__ import annotations

from pathlib import Path

from cnc_light_editor.model import (
    CometEffect,
    Keyframe,
    Layer,
    Project,
    RandomLedEffect,
    Shape,
    StrobeEffect,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "projects" / "michoakan_multiball_start.cnclight"


def keys(target, prop: str, values: list[tuple[int, object, str]]) -> None:
    target.keyframes[prop] = [
        Keyframe(time_ms, value, easing=easing) for time_ms, value, easing in values
    ]


def smoke_blob(name: str, x: float, delay: int, color: tuple[int, int, int]) -> Shape:
    shape = Shape(
        "ellipse", name, x=x, y=1.10, width=0.50, height=0.30,
        color=color, opacity=0.0, feather=0.19,
        wiggle_enabled=True, wiggle_amplitude=0.025, wiggle_speed=0.8,
    )
    keys(shape, "y", [
        (delay, 1.10, "ease_in_out"),
        (delay + 1450, 0.48, "ease_out"),
        (delay + 3000, -0.16, "ease_in"),
    ])
    keys(shape, "width", [
        (delay, 0.28, "ease_out"),
        (delay + 1500, 0.62, "ease_in_out"),
        (delay + 3000, 0.84, "ease_in"),
    ])
    keys(shape, "height", [
        (delay, 0.18, "ease_out"),
        (delay + 1800, 0.48, "ease_in_out"),
        (delay + 3000, 0.68, "ease_in"),
    ])
    keys(shape, "opacity", [
        (0, 0.0, "linear"),
        (delay, 0.0, "linear"),
        (delay + 300, 0.64, "ease_out"),
        (delay + 2350, 0.40, "linear"),
        (delay + 3000, 0.0, "ease_in"),
    ])
    return shape


def build() -> Project:
    project = Project(
        name="Michoakan Multiball Start",
        duration_ms=4800,
        fps=20,
        frame_ms=50,
        effect_id=20,
        loops=1,
        loop_frames=0,
        intro_frames=0,
        overlay=False,
        layers=[],
    )

    bed = Layer("01 Deep forest bed")
    base = Shape(
        "rectangle", "Deep green atmosphere", x=0.5, y=0.5,
        width=1.25, height=1.25, color=(5, 30, 8), opacity=0.0,
        feather=0.10,
    )
    keys(base, "color", [
        (0, (1, 8, 2), "linear"),
        (650, (10, 50, 8), "ease_out"),
        (3000, (20, 74, 10), "ease_in_out"),
        (4300, (4, 22, 5), "ease_in"),
        (4799, (0, 0, 0), "linear"),
    ])
    keys(base, "opacity", [
        (0, 0.0, "linear"), (250, 0.76, "ease_out"),
        (4050, 0.68, "linear"), (4799, 0.0, "ease_in"),
    ])
    bed.shapes.append(base)

    smoke = Layer("02 Michoakan smoke")
    smoke.shapes.extend([
        smoke_blob("Olive smoke left", 0.18, 250, (108, 132, 5)),
        smoke_blob("Acid smoke right", 0.82, 520, (158, 190, 8)),
        smoke_blob("Green smoke centre", 0.48, 900, (40, 118, 12)),
        smoke_blob("Late smoke curl", 0.70, 1450, (184, 210, 16)),
    ])

    rings = Layer("03 Two-ball spiral burst")
    for index, (color, rotation, delay) in enumerate((
        ((205, 255, 28), -24.0, 700),
        ((255, 232, 88), 156.0, 850),
    ), start=1):
        line = Shape(
            "line", f"Ball trail {index}", x=0.5, y=0.55,
            width=0.08, height=0.035, rotation=rotation,
            color=color, opacity=0.0, fill_mode="stroke", stroke_width=0.035,
            feather=0.045,
        )
        keys(line, "width", [
            (delay, 0.06, "ease_out"),
            (delay + 1050, 1.18, "ease_in_out"),
            (delay + 2400, 1.55, "ease_in"),
        ])
        keys(line, "rotation_turns", [
            (delay, 0.0, "ease_in"),
            (delay + 2400, 2.25 if index == 1 else -2.25, "ease_out"),
        ])
        keys(line, "opacity", [
            (0, 0.0, "linear"), (delay, 0.0, "linear"),
            (delay + 150, 1.0, "ease_out"),
            (delay + 2050, 0.82, "linear"),
            (delay + 2550, 0.0, "ease_in"),
        ])
        rings.shapes.append(line)

    chase = Layer("04 Counter-rotating ball chases")
    for name, color, direction, speed, offset in (
        ("Lime ball chase", (175, 255, 20), 1, 17.0, 700),
        ("Warm ball chase", (255, 226, 96), -1, 14.0, 850),
    ):
        effect = CometEffect(
            name=name, color=color, opacity=0.0, speed=speed,
            trail_length=9.0, direction=direction,
        )
        keys(effect, "opacity", [
            (0, 0.0, "linear"), (offset, 0.0, "linear"),
            (offset + 250, 1.0, "ease_out"),
            (3250, 0.92, "linear"), (3650, 0.0, "ease_in"),
        ])
        chase.comet_effects.append(effect)

    sparkle = Layer("05 Weed crystal sparks")
    particles = RandomLedEffect(
        name="Lime crystal sparks", seed=420, life_ms=330, born_speed=24.0,
        particle_count=18, color=(220, 255, 70), opacity=0.0,
    )
    keys(particles, "opacity", [
        (0, 0.0, "linear"), (1000, 0.0, "linear"),
        (1350, 0.90, "ease_out"), (3350, 0.75, "linear"),
        (3900, 0.0, "ease_in"),
    ])
    sparkle.effects.append(particles)

    impact = Layer("06 Multiball impact")
    flash = Shape(
        "ellipse", "Central bud flash", x=0.5, y=0.56,
        width=0.05, height=0.03, color=(235, 255, 150),
        opacity=0.0, feather=0.18,
    )
    keys(flash, "width", [
        (3300, 0.03, "ease_out"), (3600, 1.35, "ease_out"),
        (4050, 1.65, "ease_in"),
    ])
    keys(flash, "height", [
        (3300, 0.02, "ease_out"), (3600, 0.92, "ease_out"),
        (4050, 1.25, "ease_in"),
    ])
    keys(flash, "opacity", [
        (0, 0.0, "linear"), (3300, 0.0, "linear"),
        (3475, 1.0, "ease_out"), (3725, 0.28, "ease_in"),
        (4200, 0.0, "ease_in"),
    ])
    impact.shapes.append(flash)
    strobe = StrobeEffect(
        name="Three-hit multiball flash", color=(238, 255, 180),
        opacity=0.0, frequency_hz=9.0, duty_cycle=0.28,
    )
    keys(strobe, "opacity", [
        (0, 0.0, "linear"), (3350, 0.0, "linear"),
        (3450, 0.95, "ease_out"), (3920, 0.72, "linear"),
        (4050, 0.0, "ease_in"),
    ])
    impact.strobe_effects.append(strobe)

    blackout = Layer("07 Clean release")
    end = Shape(
        "rectangle", "Return to black", x=0.5, y=0.5,
        width=1.4, height=1.4, color=(0, 0, 0), opacity=0.0,
    )
    keys(end, "opacity", [
        (0, 0.0, "linear"), (4200, 0.0, "linear"),
        (4750, 1.0, "ease_in"), (4799, 1.0, "linear"),
    ])
    blackout.shapes.append(end)

    project.layers.extend([bed, smoke, rings, chase, sparkle, impact, blackout])
    return project


def main() -> None:
    project = build()
    project.save(OUTPUT)
    print(f"Saved {project.name}: {OUTPUT}")
    print(f"{project.stored_frame_count} frames · {project.duration_ms} ms · ID {project.effect_id}")


if __name__ == "__main__":
    main()
