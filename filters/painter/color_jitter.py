# SPDX-License-Identifier: GPL-3.0-or-later
"""A stamp's random colour shift, in HSV (PS-053).

`plan.stamps` calls `jitter_hsv` with the numbers `plan.draws` drew for
each stamp. The conversions work on whole arrays of colours, because
`colorsys` converts one colour at a time.
"""
from __future__ import annotations

import numpy as np


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
