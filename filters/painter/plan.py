# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the painter's stamps go and what they look like (PS-053).

Everything here is numpy and per stamp, so it runs without a GPU and a
test can hold it to v2's arithmetic directly. `painter.build` does the
per-texel work around it: it blurs the picture and takes its gradient,
reads both back at the centres `draws` picked, hands them to `stamps`,
and draws what `quads` returns.

The order of a build is v2's. The steps run from the largest brush to
the smallest and from Start Opacity to End Opacity, each step places
the number of stamps `schedule` works out for it, and every stamp takes
its colour from a blur of the picture below rather than from the
canvas being painted, so a later stamp never samples an earlier one.

Two things differ on purpose, and `docs/tickets/PS-053-gpu-brush-painter.md`
says why:

- the random numbers come from the layer's own seed, drawn per step and
  per stream before anything looks at the picture, so a rebuild after
  painting below moves no stamp;
- the stroke follows the gradient on every edge, where v2 mirrored it
  on the diagonals.

Rows run bottom-up, as `Image.pixels` does, so y points up and an angle
turns counter-clockwise on screen. v2 held its arrays top-down.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil, sqrt, tau

import numpy as np

# The side of the default circular brush, in texels, before it is resized
# to each step. v2 made it this size and resized it like any other.
CIRCLE_SIDE = 50

# v2's count: enough stamps to cover *density* of the image once, assuming
# each overlaps the others by this much, within these bounds.
OVERLAP = 0.7
MIN_STAMPS = 50
# At most one stamp per this many texels of the image.
TEXELS_PER_STAMP = 8

# A sampled colour this transparent places no stamp, as in v2.
MIN_ALPHA = 1e-6
# A gradient field whose strongest edge is this weak has no edges at all,
# so a threshold above zero keeps nothing.
MIN_PEAK = 1e-6

# The independent random streams of a step. Each has its own generator,
# so asking for more stamps extends every stream rather than shifting one
# into the next: raising the coverage adds stamps and moves none.
X, Y, BRUSH, TURN, JITTER = range(5)


@dataclass(frozen=True)
class Settings:
    """A painter layer's parameters, as the build reads them."""

    density: float = 0.7
    min_scale: float = 0.03
    max_scale: float = 0.1
    start_opacity: float = 0.4
    end_opacity: float = 1.0
    steps: int = 4
    threshold: float = 0.0
    # Texels of the image the layer builds, like a blur layer's.
    sigma: int = 3
    seed: int = 42
    # Radians, counter-clockwise.
    rotation: float = 0.0
    random_rotation: bool = False
    rotation_range: float = tau
    hue: float = 0.0
    saturation: float = 0.0
    value: float = 0.0

    @classmethod
    def of(cls, node) -> "Settings":
        return cls(
            density=node.painter_density,
            min_scale=node.painter_min_scale,
            max_scale=node.painter_max_scale,
            start_opacity=node.painter_start_opacity,
            end_opacity=node.painter_end_opacity,
            steps=node.painter_steps,
            threshold=node.painter_threshold,
            sigma=node.painter_sigma,
            seed=node.painter_seed,
            rotation=node.painter_rotation,
            random_rotation=node.painter_random_rotation,
            rotation_range=node.painter_rotation_range,
            hue=node.painter_hue,
            saturation=node.painter_saturation,
            value=node.painter_value,
        )

    def as_dict(self) -> dict:
        return asdict(self)


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
    # Uniform in [-0.5, 0.5), scaled by the rotation range when it is used.
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


# -- brushes ------------------------------------------------------------------


def circle(side: int = CIRCLE_SIDE) -> np.ndarray:
    """v2's default brush: a cone of alpha falling to zero at the rim."""
    centre = side / 2
    y, x = np.ogrid[-centre:side - centre, -centre:side - centre]
    return np.clip(1.0 - np.sqrt(x * x + y * y) / centre, 0.0, 1.0).astype(np.float32)


def square(mask: np.ndarray) -> np.ndarray:
    """*mask* centred on a transparent square, as v2 padded a brush.

    v2 held its rows top-down and put the odd row of padding at the
    bottom, which in rows that run bottom-up is the first one.
    """
    height, width = mask.shape
    if height == width:
        return mask
    side = max(height, width)
    padded = np.zeros((side, side), dtype=np.float32)
    bottom = side - height - (side - height) // 2
    left = (side - width) // 2
    padded[bottom:bottom + height, left:left + width] = mask
    return padded


def resize_bilinear(mask: np.ndarray, side: int) -> np.ndarray:
    """v2's `_resize_mask_bilinear` to *side* by *side*, which the count is taken on.

    Point sampling: every output texel reads the two by two input texels
    nearest it and nothing else, so shrinking a brush a long way skips
    most of it. That is why `resize` filters first; the count keeps this
    one, because it is the one v2's count was taken on.
    """
    src_h, src_w = mask.shape
    if (src_h, src_w) == (side, side):
        return mask.astype(np.float32, copy=True)
    if side <= 1:
        return np.full((1, 1), float(mask.mean()), dtype=np.float32)
    y = np.linspace(0, src_h - 1, side, dtype=np.float32)
    x = np.linspace(0, src_w - 1, side, dtype=np.float32)
    y0 = np.floor(y).astype(np.int32)
    x0 = np.floor(x).astype(np.int32)
    y1 = np.minimum(y0 + 1, src_h - 1)
    x1 = np.minimum(x0 + 1, src_w - 1)
    wy = (y - y0)[:, None]
    wx = (x - x0)[None, :]
    top = mask[y0[:, None], x0[None, :]] * (1.0 - wx) + mask[y0[:, None], x1[None, :]] * wx
    bottom = mask[y1[:, None], x0[None, :]] * (1.0 - wx) + mask[y1[:, None], x1[None, :]] * wx
    return (top * (1.0 - wy) + bottom * wy).astype(np.float32)


def resize(mask: np.ndarray, side: int) -> np.ndarray:
    """*mask* resized to *side* for drawing, averaged first when it shrinks.

    A brush shrunk by a whole factor of two or more is box-filtered by
    that factor before the bilinear step, so every input texel counts
    towards the stamp. The rows and columns that do not divide evenly
    are trimmed from both edges alike, which keeps the brush centred.
    """
    src = mask.shape[0]
    factor = src // side
    if factor >= 2:
        kept = src // factor * factor
        start = (src - kept) // 2
        block = mask[start:start + kept, start:start + kept]
        cells = kept // factor
        mask = block.reshape(cells, factor, cells, factor).mean(axis=(1, 3))
    return resize_bilinear(mask, side)


# -- the schedule -------------------------------------------------------------


def schedule(settings: Settings, width: int, height: int, masks) -> list[Step]:
    """The steps of a build of *width* by *height*, with v2's sizes and counts."""
    steps = []
    for index in range(settings.steps):
        if settings.steps == 1:
            scale, opacity = settings.min_scale, settings.end_opacity
        else:
            # In v2's order of operations, so that a size on the edge of
            # a whole texel rounds the way v2's did.
            last = settings.steps - 1
            scale = settings.max_scale + (
                settings.min_scale - settings.max_scale) * index / last
            opacity = settings.start_opacity + (
                settings.end_opacity - settings.start_opacity) * index / last
        size = max(1, int(scale * min(width, height)))
        steps.append(Step(index=index, size=size, opacity=opacity,
                          count=stamp_count(settings.density, width, height, masks, size)))
    return steps


def stamp_count(density: float, width: int, height: int, masks, size: int) -> int:
    """v2's `calculate_brush_area_density`, on brushes resized to *size*.

    The area of a brush is the number of texels it covers at all, counted
    on v2's own resize so that the count matches v2's exactly. A brush
    that covers nothing counts as one texel rather than dividing by zero.
    """
    areas = [int(np.count_nonzero(resize_bilinear(mask, size) > 0)) for mask in masks]
    area = max(1.0, sum(areas) / len(areas))
    image = width * height
    count = int(image * density / (area * OVERLAP))
    return max(MIN_STAMPS, min(count, image // TEXELS_PER_STAMP))


def draws(settings: Settings, step: Step, width: int, height: int, brushes: int) -> Draws:
    """The random numbers of *step*, from the layer's seed.

    Every stream is drawn in full whatever the settings use, so switching
    Random Rotation on or raising a colour shift changes no position.
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

    *colors* is the blurred picture at each drawn centre, straight sRGB;
    *gradients* is ``(gx, gy, magnitude)`` there, with y up. *peak* is
    the strongest magnitude anywhere in the picture, which the threshold
    is relative to; None when the threshold is zero and it was not
    measured.

    A stamp is dropped where the picture is transparent or the edge is
    weaker than the threshold. Dropping it changes nothing about the
    others: each one's numbers were drawn for it alone.
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
    if settings.random_rotation:
        angle = angle + drawn.turn * settings.rotation_range

    weight = (alpha * step.opacity)[:, None]
    color = np.concatenate([colors[:, :3] * weight, weight], axis=1)
    return Stamps(x=drawn.x[keep], y=drawn.y[keep], brush=drawn.brush[keep],
                  angle=angle[keep], color=color[keep].astype(np.float32))


def jitter_hsv(colors: np.ndarray, shifts, jitter: np.ndarray) -> np.ndarray:
    """v2's `apply_color_shift`, per stamp and from the seeded stream.

    A shift of *s* moves hue by up to half of *s* turns either way, and
    saturation and value by up to half of *s*, clamped. Alpha is left
    alone, and a colour with no shift at all is returned as it came.
    """
    hue, saturation, value = shifts
    if not (hue or saturation or value):
        return colors
    h, s, v = rgb_to_hsv(colors[:, :3])
    h = (h + jitter[:, 0] * hue) % 1.0
    s = np.clip(s + jitter[:, 1] * saturation, 0.0, 1.0)
    v = np.clip(v + jitter[:, 2] * value, 0.0, 1.0)
    return np.concatenate([np.clip(hsv_to_rgb(h, s, v), 0.0, 1.0), colors[:, 3:4]], axis=1)


def rgb_to_hsv(rgb: np.ndarray):
    """``(n, 3)`` RGB to three arrays of hue in turns, saturation and value."""
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    high = rgb.max(axis=1)
    low = rgb.min(axis=1)
    delta = high - low
    safe = np.where(delta > 0.0, delta, 1.0)
    hue = np.where(high == r, (g - b) / safe,
                   np.where(high == g, 2.0 + (b - r) / safe, 4.0 + (r - g) / safe))
    hue = np.where(delta > 0.0, (hue / 6.0) % 1.0, 0.0)
    saturation = np.where(high > 0.0, delta / np.where(high > 0.0, high, 1.0), 0.0)
    return hue, saturation, high


def hsv_to_rgb(h, s, v) -> np.ndarray:
    """Three arrays of hue in turns, saturation and value to ``(n, 3)`` RGB."""
    sector = h * 6.0
    index = np.floor(sector).astype(np.int64) % 6
    f = sector - np.floor(sector)
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    choices = (
        (v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q),
    )
    channels = [np.choose(index, [choice[channel] for choice in choices])
                for channel in range(3)]
    return np.stack(channels, axis=1)


# -- geometry -----------------------------------------------------------------


def atlas_layout(brushes: int, side: int, limit: int) -> tuple[int, int, int]:
    """``(columns, rows, cell side)`` for *brushes* cells of up to *side* texels.

    Each cell carries a one-texel transparent gutter, so a bilinear read
    at a cell's edge fades to nothing instead of reaching into the next
    brush. A step whose brushes would not fit in a texture of *limit*
    texels gets smaller cells, which the stamp then magnifies.
    """
    columns = ceil(sqrt(brushes))
    rows = ceil(brushes / columns)
    fits = min(limit // columns, limit // rows) - 2
    return columns, rows, max(1, min(side, fits))


def atlas(masks, cell: int, columns: int, rows: int) -> tuple[np.ndarray, np.ndarray]:
    """The brushes resized to *cell* and laid out in one single-channel image.

    Returns the image, row 0 at the bottom, and the lower-left texel of
    each brush in it as an ``(n, 2)`` array of ``(x, y)``.
    """
    pitch = cell + 2
    image = np.zeros((rows * pitch, columns * pitch), dtype=np.float32)
    origins = np.zeros((len(masks), 2), dtype=np.float32)
    for index, mask in enumerate(masks):
        column, row = index % columns, index // columns
        x, y = column * pitch + 1, row * pitch + 1
        image[y:y + cell, x:x + cell] = resize(mask, cell)
        origins[index] = (x, y)
    return image, origins


def quads(stamps: Stamps, size: int, origins: np.ndarray, cell: int):
    """Vertex positions, atlas coordinates, colours and indices for *stamps*.

    A stamp of *size* covers the square v2 wrote it into -- from ``x -
    size // 2`` for *size* texels -- turned about that square's centre.
    Unturned, its corners sit on texel edges and the atlas cell maps onto
    it texel for texel, so a stamp at angle zero reproduces its brush.
    """
    count = len(stamps)
    half = size / 2.0
    centre_x = stamps.x - size // 2 + half
    centre_y = stamps.y - size // 2 + half
    corners = np.array([(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]) * half
    cos, sin = np.cos(stamps.angle)[:, None], np.sin(stamps.angle)[:, None]
    positions = np.empty((count, 4, 2), dtype=np.float32)
    positions[..., 0] = centre_x[:, None] + corners[:, 0] * cos - corners[:, 1] * sin
    positions[..., 1] = centre_y[:, None] + corners[:, 0] * sin + corners[:, 1] * cos

    unit = np.array([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]) * cell
    coords = (origins[stamps.brush][:, None, :] + unit[None, :, :]).astype(np.float32)

    colors = np.repeat(stamps.color[:, None, :], 4, axis=1)
    base = (np.arange(count, dtype=np.int32) * 4)[:, None]
    indices = np.concatenate([base + (0, 1, 2), base + (0, 2, 3)], axis=1)
    return (positions.reshape(-1, 2), coords.reshape(-1, 2), colors.reshape(-1, 4),
            indices.reshape(-1, 3).astype(np.int32))
