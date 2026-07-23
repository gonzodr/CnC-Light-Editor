from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NumericPropertySpec:
    key: str
    label: str
    minimum: float | None
    maximum: float | None
    sensitivity: float
    decimals: int = 3
    display_scale: float = 1.0
    suffix: str = ""
    prefix: str = ""
    integer: bool = False
    storage_decimals: int = 4

    def normalize(self, value: float) -> float | int:
        normalized = float(value)
        if self.minimum is not None:
            normalized = max(self.minimum, normalized)
        if self.maximum is not None:
            normalized = min(self.maximum, normalized)
        if self.integer:
            return int(round(normalized))
        return round(normalized, self.storage_decimals)

    def from_input(self, value: float) -> float | int:
        return self.normalize(value / self.display_scale)

    def input_limits(self) -> tuple[float | None, float | None]:
        low = None if self.minimum is None else self.minimum * self.display_scale
        high = None if self.maximum is None else self.maximum * self.display_scale
        return low, high

    def input_text(self, value: float | int) -> str:
        display = float(value) * self.display_scale
        if self.integer:
            return str(int(round(display)))
        return f"{display:.{self.decimals}f}"

    def display_text(self, value: float | int) -> str:
        display = float(value) * self.display_scale
        number = (
            str(int(round(display)))
            if self.integer else f"{display:.{self.decimals}f}"
        )
        return f"{self.prefix}{number}{self.suffix}"


SHAPE_PROPERTY_SPECS = {
    spec.key: spec for spec in (
        NumericPropertySpec("x", "X", 0.0, 1.0, 0.002),
        NumericPropertySpec("y", "Y", 0.0, 1.0, 0.002),
        NumericPropertySpec("width", "W", 0.01, 5.0, 0.002),
        NumericPropertySpec("height", "H", 0.01, 5.0, 0.002),
        NumericPropertySpec("rotation", "ROT", None, None, 0.5, 1, suffix="°"),
        NumericPropertySpec(
            "rotation_turns", "TURNS", -100.0, 100.0, 0.02, 0,
            prefix="×", integer=True,
        ),
        NumericPropertySpec("opacity", "OPACITY", 0.0, 1.0, 0.002),
        NumericPropertySpec("feather", "FEATHER", 0.0, 0.1, 0.00025, 1, 100.0, "%"),
        NumericPropertySpec(
            "mask_expansion", "EXPAND", -0.1, 0.1, 0.00025, 1, 100.0, "%",
        ),
        NumericPropertySpec("stroke_width", "Stroke width", 0.001, 0.05, 0.00025, 1, 100.0),
        NumericPropertySpec("wiggle_amplitude", "Wiggle amount", 0.0, 0.2, 0.0005, 1, 100.0, "%"),
        NumericPropertySpec("wiggle_speed", "Wiggle speed", 0.1, 20.0, 0.02, 1, suffix=" Hz"),
    )
}


LAYER_PROPERTY_SPECS = {
    "layer_opacity": NumericPropertySpec(
        "layer_opacity", "Layer opacity", 0.0, 1.0, 0.005,
        1, 100.0, "%",
    ),
}


RANDOM_LED_PROPERTY_SPECS = {
    spec.key: spec for spec in (
        NumericPropertySpec("seed", "Random seed", 0.0, 2147483647.0, 1.0, 0, integer=True),
        NumericPropertySpec("life_ms", "Life", 50.0, 10000.0, 5.0, 0, suffix=" ms", integer=True),
        NumericPropertySpec(
            "born_speed", "Born speed", 0.5, 100.0, 0.1, 1,
            suffix=" / sec", storage_decimals=1,
        ),
        NumericPropertySpec("particle_count", "Max active", 1.0, 68.0, 0.2, 0, integer=True),
        NumericPropertySpec("opacity", "Opacity", 0.0, 1.0, 0.005, 1, 100.0, "%"),
    )
}


STROBE_PROPERTY_SPECS = {
    spec.key: spec for spec in (
        NumericPropertySpec("frequency_hz", "Frequency", 0.1, 30.0, 0.02, 1, suffix=" Hz"),
        NumericPropertySpec("duty_cycle", "Duty cycle", 0.05, 0.95, 0.002, 1, 100.0, "%"),
        NumericPropertySpec("opacity", "Opacity", 0.0, 1.0, 0.005, 1, 100.0, "%"),
    )
}


COLOR_CYCLE_PROPERTY_SPECS = {
    spec.key: spec for spec in (
        NumericPropertySpec("speed_hz", "Speed", 0.01, 5.0, 0.004, 2, suffix=" Hz"),
        NumericPropertySpec("spread", "Rainbow spread", 0.0, 1.0, 0.005, 1, 100.0, "%"),
        NumericPropertySpec("saturation", "Saturation", 0.0, 1.0, 0.005, 1, 100.0, "%"),
        NumericPropertySpec("brightness", "Brightness", 0.0, 1.0, 0.005, 1, 100.0, "%"),
        NumericPropertySpec("opacity", "Opacity", 0.0, 1.0, 0.005, 1, 100.0, "%"),
    )
}


PULSE_PROPERTY_SPECS = {
    spec.key: spec for spec in (
        NumericPropertySpec("period_ms", "Period", 100.0, 10000.0, 5.0, 0, suffix=" ms", integer=True),
        NumericPropertySpec("depth", "Depth", 0.0, 1.0, 0.005, 1, 100.0, "%"),
        NumericPropertySpec("opacity", "Opacity", 0.0, 1.0, 0.005, 1, 100.0, "%"),
    )
}


COMET_PROPERTY_SPECS = {
    spec.key: spec for spec in (
        NumericPropertySpec("speed", "Speed", 0.5, 68.0, 0.05, 1, suffix=" led/s"),
        NumericPropertySpec("trail_length", "Trail length", 1.0, 34.0, 0.05, 1, suffix=" leds"),
        NumericPropertySpec("opacity", "Opacity", 0.0, 1.0, 0.005, 1, 100.0, "%"),
    )
}
