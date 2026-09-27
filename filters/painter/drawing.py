# SPDX-License-Identifier: GPL-3.0-or-later
"""The arrays the stamps are drawn from (PS-053).

`atlas` lays a step's brushes out in one image, and `quads` turns the
stamps `plan.stamps` kept into vertices that read from it and carry the
island each stamp keeps to.
`painter.build` uploads and draws both. This is plain numpy, so tests
check the geometry without a GPU.
"""
from __future__ import annotations

from math import ceil, sqrt

import numpy as np

from .plan import Stamps
from .resizing import resize


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
    """Vertex positions, atlas coordinates, colours, islands and indices for *stamps*.

    A stamp of *size* covers the same square v2 wrote it into, starting
    at ``x - size // 2`` and *size* texels wide, rotated about its centre.
    At angle zero its corners sit on texel edges and the atlas cell maps
    onto it texel for texel, so the stamp reproduces its brush. Every
    corner carries the stamp's island as a float, which is exact for any
    island number the island map can hold.
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
    islands = np.repeat(stamps.owner.astype(np.float32), 4)
    base = (np.arange(count, dtype=np.int32) * 4)[:, None]
    indices = np.concatenate([base + (0, 1, 2), base + (0, 2, 3)], axis=1)
    return (positions.reshape(-1, 2), coords.reshape(-1, 2), colors.reshape(-1, 4), islands,
            indices.reshape(-1, 3).astype(np.int32))
