# SPDX-License-Identifier: GPL-3.0-or-later
"""How a brush is resized to a step's size, and what it covers then (PS-053).

`resize` is the resize the stamps are drawn with. `resize_bilinear` is
v2's own resize, which v2 took its stamp counts on. `covered_area`
counts what that resize leaves above zero without running it, and
`Areas` keeps those counts per side for `plan.schedule`.

This is plain numpy, like `plan`, so tests hold it to v2's arithmetic
without a GPU.
"""
from __future__ import annotations

import numpy as np

# Smallest non-zero brush value that `covered_area` can count without
# running the resize. A smaller value could round to zero inside the
# resize, and the count would no longer match v2's.
SAFE_MIN = 1e-20


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
    """The mean covered area of a set of brushes at each side, for `plan.stamp_count`.

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
