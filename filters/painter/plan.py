# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the painter's stamps go and how they look (PS-053).

Everything here is numpy and works per stamp, so it runs without a GPU
and tests can check it against v2's arithmetic directly. `painter.build`
does the per-texel work around it. It blurs the picture, takes its
gradient, reads both at the centres `draws` picked, passes them to
`stamps`, and draws what `quads` returns.

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
from math import ceil, sqrt

import numpy as np

# Side of the default circle brush in texels, before it is resized to
# each step. v2 used this size too, and resized it like any other brush.
CIRCLE_SIDE = 50

# v2's stamp count: enough stamps to cover *density* of the image once,
# assuming each stamp overlaps the others by `OVERLAP`, and never fewer
# than `MIN_STAMPS`.
OVERLAP = 0.7
MIN_STAMPS = 50
# At most one stamp per this many texels of the image.
TEXELS_PER_STAMP = 8

# Smallest non-zero brush value that `covered_area` can count without
# running the resize. A smaller value could round to zero inside the
# resize, and the count would no longer match v2's.
SAFE_MIN = 1e-20

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
        return cls(
            brush=node.painter_brush,
            density=_fraction(node.painter_coverage),
            min_scale=_fraction(node.painter_smallest_stroke),
            max_scale=_fraction(node.painter_largest_stroke),
            start_opacity=node.painter_first_opacity,
            end_opacity=node.painter_last_opacity,
            steps=node.painter_passes,
            threshold=_fraction(node.painter_edge_threshold),
            sigma=node.painter_smoothing * side / SMOOTHING_SIDE,
            seed=node.painter_seed,
            rotation=node.painter_rotation,
            rotation_range=node.painter_random_rotation,
            hue=node.painter_hue,
            saturation=node.painter_saturation,
            value=node.painter_value,
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


# -- brushes ------------------------------------------------------------------


def circle(side: int = CIRCLE_SIDE) -> np.ndarray:
    """v2's default brush: a cone of alpha falling to zero at the rim."""
    centre = side / 2
    y, x = np.ogrid[-centre:side - centre, -centre:side - centre]
    return np.clip(1.0 - np.sqrt(x * x + y * y) / centre, 0.0, 1.0).astype(np.float32)


def square(mask: np.ndarray) -> np.ndarray:
    """*mask* centred on a transparent square, padded the way v2 padded a brush.

    v2 stored rows top-down and put the odd row of padding at the bottom.
    Rows here run bottom-up, so that extra row comes first.
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


def _sample_axes(src_h: int, src_w: int, side: int):
    """The sample positions of a bilinear resize from *src_h* by *src_w* to *side*.

    Returns ``(y, x, y0, x0, y1, x1)``: the float32 sample positions on
    each axis, and the int32 texels on either side of them, clamped to
    the edge. `resize_bilinear`, `covered_area` and `resize` all use
    these, so the stamp count and the drawn stamp sample the same texels.
    """
    y = np.linspace(0, src_h - 1, side, dtype=np.float32)
    x = np.linspace(0, src_w - 1, side, dtype=np.float32)
    y0 = np.floor(y).astype(np.int32)
    x0 = np.floor(x).astype(np.int32)
    y1 = np.minimum(y0 + 1, src_h - 1)
    x1 = np.minimum(x0 + 1, src_w - 1)
    return y, x, y0, x0, y1, x1


def resize_bilinear(mask: np.ndarray, side: int) -> np.ndarray:
    """*mask* resized to *side* by *side* with v2's `_resize_mask_bilinear`.

    Each output texel reads only the two by two input texels nearest it.
    So shrinking a brush a lot skips most of its texels, which is why
    `resize` box-filters first for drawing. The stamp count still uses
    this resize, because v2's count was taken on it. `covered_area` gets
    the same count without running it.
    """
    src_h, src_w = mask.shape
    if (src_h, src_w) == (side, side):
        return mask.astype(np.float32, copy=True)
    if side <= 1:
        return np.full((1, 1), float(mask.mean()), dtype=np.float32)
    y, x, y0, x0, y1, x1 = _sample_axes(src_h, src_w, side)
    wy = (y - y0)[:, None]
    wx = (x - x0)[None, :]
    top = mask[y0[:, None], x0[None, :]] * (1.0 - wx) + mask[y0[:, None], x1[None, :]] * wx
    bottom = mask[y1[:, None], x0[None, :]] * (1.0 - wx) + mask[y1[:, None], x1[None, :]] * wx
    return (top * (1.0 - wy) + bottom * wy).astype(np.float32)


def covered(mask: np.ndarray) -> np.ndarray | None:
    """``mask > 0`` for `covered_area`, or None when it cannot stand in for the resize.

    It cannot when *mask* has a value that is negative, not finite, or so
    close to zero that the resize could round it away (`SAFE_MIN`). No
    shipped brush has such a value, but a brush made from any image could.
    """
    if not np.isfinite(mask).all() or float(mask.min()) < 0.0:
        return None
    inside = mask > 0
    if inside.any() and float(mask[inside].min()) < SAFE_MIN:
        return None
    return inside


def covered_area(mask: np.ndarray, side: int, inside: np.ndarray | None) -> int:
    """How many texels `resize_bilinear(mask, side)` leaves above zero, without resizing.

    Each term of that resize is a texel of *mask* (never negative) times
    a weight. A weight is zero only where a sample lands exactly on a
    whole texel. So an output texel is above zero exactly when a texel it
    reads with a non-zero weight is above zero. The count is therefore a
    lookup of *inside* (what `covered` returned for *mask*) at the same
    sample positions. That costs a tenth of the float resize at 4K. When
    *inside* is None, the real resize is counted instead.
    """
    src_h, src_w = mask.shape
    if inside is None:
        return int(np.count_nonzero(resize_bilinear(mask, side) > 0))
    if (src_h, src_w) == (side, side):
        return int(np.count_nonzero(inside))
    if side <= 1:
        return int(np.float32(mask.mean()) > 0)
    y, x, y0, x0, y1, x1 = _sample_axes(src_h, src_w, side)
    rows = inside.take(y0, axis=0)
    rows |= inside.take(y1, axis=0) & (y != y0)[:, None]
    hit = rows.take(x0, axis=1)
    hit |= rows.take(x1, axis=1) & (x != x0)[None, :]
    return int(np.count_nonzero(hit))


class Areas:
    """The mean covered area of a set of brushes at each side, for `stamp_count`.

    `painter.brushes` keeps one per preset. So each mask goes through
    `covered` once per session, and a rebuild at a side already seen just
    looks its area up. The table holds one number per side asked for.
    """

    def __init__(self, masks):
        self._masks = list(masks)
        self._inside = [covered(mask) for mask in self._masks]
        self._means: dict[int, float] = {}

    def mean(self, side: int) -> float:
        """The covered area at *side*, averaged over the brushes. At least one texel."""
        area = self._means.get(side)
        if area is None:
            counts = [covered_area(mask, side, inside)
                      for mask, inside in zip(self._masks, self._inside)]
            area = self._means[side] = max(1.0, sum(counts) / len(counts))
        return area


def resize(mask: np.ndarray, side: int) -> np.ndarray:
    """*mask* resized to *side* for drawing, box-filtered first when it shrinks.

    A brush shrunk by a factor of two or more is first box-filtered by
    the whole part of that factor, so every input texel counts towards
    the stamp. Rows and columns left over by that division are trimmed
    evenly from both edges, which keeps the brush centred.

    The bilinear step matches `resize_bilinear`, but runs one axis at a
    time in single precision. That is four times as fast, and the
    difference is smaller than the half float the atlas is uploaded as
    can show.
    """
    src = mask.shape[0]
    factor = src // side
    if factor >= 2:
        kept = src // factor * factor
        start = (src - kept) // 2
        block = mask[start:start + kept, start:start + kept]
        # Strided sums instead of a reshaped mean, because the reshape
        # would copy the block first.
        rows = block[0::factor].copy()
        for offset in range(1, factor):
            rows += block[offset::factor]
        boxed = rows[:, 0::factor].copy()
        for offset in range(1, factor):
            boxed += rows[:, offset::factor]
        boxed *= np.float32(1.0 / (factor * factor))
        mask = boxed
    if mask.shape == (side, side):
        return mask.astype(np.float32, copy=True)
    if side <= 1:
        return np.full((1, 1), float(mask.mean()), dtype=np.float32)
    src_h, src_w = mask.shape
    y, x, y0, x0, y1, x1 = _sample_axes(src_h, src_w, side)
    wy = (y - y0.astype(np.float32))[:, None]
    wx = (x - x0.astype(np.float32))[None, :]
    rows = mask[y0] * (np.float32(1.0) - wy) + mask[y1] * wy
    return (rows[:, x0] * (np.float32(1.0) - wx) + rows[:, x1] * wx).astype(np.float32)


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


def jitter_hsv(colors: np.ndarray, shifts, jitter: np.ndarray) -> np.ndarray:
    """v2's `apply_color_shift`, applied per stamp with the seeded numbers.

    A shift of *s* moves hue by up to half of *s* turns either way. It
    moves saturation and value by up to half of *s*, clamped to 0..1.
    Alpha is unchanged. With no shift at all, *colors* is returned as is.
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

    Each cell has a one-texel transparent gutter, so a bilinear read at a
    cell's edge fades to nothing instead of reaching into the next brush.
    When a step's brushes would not fit in a texture of *limit* texels
    on a side, the cells are made smaller, and the stamp magnifies them.
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

    A stamp of *size* covers the same square v2 wrote it into, starting
    at ``x - size // 2`` and *size* texels wide, rotated about its centre.
    At angle zero its corners sit on texel edges and the atlas cell maps
    onto it texel for texel, so the stamp reproduces its brush.
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
