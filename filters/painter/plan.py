# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the painter's stamps go and how they look (PS-053).

Everything here is numpy and works per stamp, so it runs without a GPU
and tests can check it against v2's arithmetic directly. `painter.build`
does the per-texel work around it. It blurs the picture, takes its
gradient, reads both at the centres `draws` picked, passes them to
`stamps`, and draws what `drawing.quads` returns. How a brush is
resized and what it covers is in `resizing`, and the colour shift is in
`color_jitter`.

A build follows v2's order:

- Steps go from the largest stroke to the smallest, and from the first
  pass's opacity to the last.
- Each step places the number of stamps `schedule` works out for it.
- Every stamp takes its colour from a blur of the picture below, not
  from the canvas being painted. So a later stamp never samples an
  earlier one.

Two things differ from v2 on purpose. The reasons are in
`docs/tickets/PS-053-gpu-brush-painter.md`.

- Random numbers come from the layer's own seed. They are drawn per
  step and per stream before the picture is read, so a rebuild after
  painting below moves no stamp.
- The stroke follows the gradient on every edge. v2 mirrored it on
  diagonal edges.

Rows run bottom-up, as in `Image.pixels`, so y points up and a positive
angle turns counter-clockwise on screen. v2 stored its arrays top-down.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .color_jitter import jitter_hsv
from .resizing import Areas

# v2's stamp count: enough stamps to cover *density* of the image once,
# assuming each stamp overlaps the others by `OVERLAP`, and never fewer
# than `MIN_STAMPS`.
OVERLAP = 0.7
MIN_STAMPS = 50
# At most one stamp per this many texels of the image.
TEXELS_PER_STAMP = 8

# A layer's Smoothing is in texels of an image this wide, which is v2's
# usual size and a new filter layer's. It scales with the layer's
# resolution, so the strokes follow the same shapes at any resolution.
SMOOTHING_SIDE = 2048

# A sampled colour this transparent places no stamp, as in v2.
MIN_ALPHA = 1e-6
# When the strongest edge in the gradient field is this weak, the field
# counts as having no edges, and any threshold above zero keeps nothing.
MIN_PEAK = 1e-6

# The independent random streams of a step. Each has its own generator,
# so asking for more stamps makes every stream longer instead of shifting
# one stream's numbers into the next. Raising the coverage adds stamps
# and moves none of the existing ones.
X, Y, BRUSH, TURN, JITTER = range(5)


@dataclass(frozen=True)
class Settings:
    """A painter layer's parameters, in the units the build uses.

    The planning is written in v2's quantities: stroke sizes, coverage
    and threshold as fractions, and the blur in texels of the image being
    built. The layer stores percentages and a resolution-independent
    Smoothing. `of` converts from the layer's settings.
    """

    density: float = 0.7
    min_scale: float = 0.03
    max_scale: float = 0.1
    start_opacity: float = 0.4
    end_opacity: float = 1.0
    steps: int = 4
    threshold: float = 0.0
    # Texels of the image the layer builds.
    sigma: float = 3.0
    seed: int = 42
    # Radians, counter-clockwise.
    rotation: float = 0.0
    # Full width of the random turn, centred on zero. Zero turns nothing.
    rotation_range: float = 0.0
    hue: float = 0.0
    saturation: float = 0.0
    value: float = 0.0
    # A `painter.brushes` identifier. The first preset, as on a new layer.
    brush: str = 'GOUACHE_SHORT_1'

    @classmethod
    def of(cls, node) -> "Settings":
        # A filter layer's image is square, *resolution* on a side.
        side = int(node.resolution)
        painter = node.painter
        return cls(
            brush=painter.brush,
            density=_fraction(painter.coverage),
            min_scale=_fraction(painter.smallest_stroke),
            max_scale=_fraction(painter.largest_stroke),
            start_opacity=painter.first_opacity,
            end_opacity=painter.last_opacity,
            steps=painter.passes,
            threshold=_fraction(painter.edge_threshold),
            sigma=painter.smoothing * side / SMOOTHING_SIDE,
            seed=painter.seed,
            rotation=painter.rotation,
            rotation_range=painter.random_rotation,
            hue=painter.hue,
            saturation=painter.saturation,
            value=painter.value,
        )

    def as_dict(self) -> dict:
        return asdict(self)


def _fraction(percentage: float) -> float:
    """*percentage* as a fraction, rounded to single precision.

    v2 stored its settings as single-precision fractions, and a stamp
    count is a truncated product of them. A double 0.7 can give one stamp
    more than the single-precision 0.7 that v2 used.
    """
    return float(np.float32(percentage / 100.0))


@dataclass(frozen=True)
class Step:
    """One pass of stamps, all of one size and one opacity."""

    index: int
    # Side of every stamp in this step, in texels.
    size: int
    opacity: float
    count: int


@dataclass(frozen=True)
class Draws:
    """Every random number one step needs, drawn before the picture is read."""

    x: np.ndarray
    y: np.ndarray
    brush: np.ndarray
    # Uniform in [-0.5, 0.5), scaled by the rotation range.
    turn: np.ndarray
    # ``(count, 3)``, uniform in [-0.5, 0.5), scaled by the HSV shifts.
    jitter: np.ndarray


@dataclass(frozen=True)
class Stamps:
    """The stamps of one step that survived planning, in the order to draw them."""

    x: np.ndarray
    y: np.ndarray
    brush: np.ndarray
    # Radians, counter-clockwise.
    angle: np.ndarray
    # ``(count, 4)``: the stamp's colour premultiplied by its own alpha
    # and the step's opacity, ready for a premultiplied "over".
    color: np.ndarray

    def __len__(self) -> int:
        return len(self.x)

    def part(self, start: int, stop: int) -> "Stamps":
        return Stamps(x=self.x[start:stop], y=self.y[start:stop],
                      brush=self.brush[start:stop], angle=self.angle[start:stop],
                      color=self.color[start:stop])


# -- the schedule -------------------------------------------------------------


def schedule(settings: Settings, width: int, height: int, areas: Areas) -> list[Step]:
    """The steps of a build of *width* by *height*, with v2's sizes and counts.

    *areas* is the `Areas` of the brushes the build stamps with.
    """
    steps = []
    for index in range(settings.steps):
        if settings.steps == 1:
            scale, opacity = settings.min_scale, settings.end_opacity
        else:
            # Same order of operations as v2, so a size right on a
            # whole-texel boundary rounds the same way as in v2.
            last = settings.steps - 1
            scale = settings.max_scale + (
                settings.min_scale - settings.max_scale) * index / last
            opacity = settings.start_opacity + (
                settings.end_opacity - settings.start_opacity) * index / last
        size = max(1, int(scale * min(width, height)))
        steps.append(Step(index=index, size=size, opacity=opacity,
                          count=stamp_count(settings.density, width, height, areas.mean(size))))
    return steps


def stamp_count(density: float, width: int, height: int, area: float) -> int:
    """v2's `calculate_brush_area_density`, for brushes covering *area* texels on average.

    A brush's area is the number of texels it covers at all after it is
    resized to the step's size. It is counted on v2's own resize, so the
    count matches v2's exactly. `Areas.mean` gives this value and is never
    less than one texel, so a brush that covers nothing does not cause a
    division by zero.
    """
    image = width * height
    count = int(image * density / (area * OVERLAP))
    return max(MIN_STAMPS, min(count, image // TEXELS_PER_STAMP))


def draws(settings: Settings, step: Step, width: int, height: int, brushes: int) -> Draws:
    """The random numbers of *step*, from the layer's seed.

    Every stream is drawn in full, even when the settings do not use it.
    So raising Random Rotation or a colour shift moves no stamp.
    """
    def stream(kind):
        return np.random.default_rng([settings.seed, step.index, kind])

    count = step.count
    return Draws(
        x=stream(X).integers(0, width, count),
        y=stream(Y).integers(0, height, count),
        brush=stream(BRUSH).integers(0, brushes, count),
        turn=stream(TURN).random(count) - 0.5,
        jitter=stream(JITTER).random((count, 3)) - 0.5,
    )


# -- planning -----------------------------------------------------------------


def stamps(settings: Settings, step: Step, drawn: Draws, colors: np.ndarray,
           gradients: np.ndarray, peak: float | None) -> Stamps:
    """The stamps of *step* that land, with their angle and colour.

    *colors* is the blurred picture at each drawn centre, in straight
    sRGB. *gradients* is ``(gx, gy, magnitude)`` at the same centres,
    with y up. *peak* is the strongest magnitude anywhere in the picture,
    and the threshold is relative to it. It is None when the threshold is
    zero, because then it is not measured.

    A stamp is dropped where the picture is transparent or the edge is
    weaker than the threshold. Dropping one does not affect the others,
    because each stamp's random numbers were drawn for it alone.
    """
    colors = jitter_hsv(colors, (settings.hue, settings.saturation, settings.value),
                        drawn.jitter)
    alpha = colors[:, 3]
    keep = alpha > MIN_ALPHA
    if settings.threshold > 0.0:
        if peak is None or peak <= MIN_PEAK:
            keep = np.zeros_like(keep)
        else:
            keep &= gradients[:, 2] / peak >= settings.threshold

    angle = np.arctan2(gradients[:, 1], gradients[:, 0]) + settings.rotation
    if settings.rotation_range > 0.0:
        angle = angle + drawn.turn * settings.rotation_range

    weight = (alpha * step.opacity)[:, None]
    color = np.concatenate([colors[:, :3] * weight, weight], axis=1)
    return Stamps(x=drawn.x[keep], y=drawn.y[keep], brush=drawn.brush[keep],
                  angle=angle[keep], color=color[keep].astype(np.float32))
