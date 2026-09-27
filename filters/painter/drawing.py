# SPDX-License-Identifier: GPL-3.0-or-later
"""The arrays the stamps are drawn from (PS-053).

`atlas` lays a step's brushes out in one image. `quads` places each
stamp on the image and in the atlas, and `geometry` turns the stamps and
the pieces `seams.pieces` carries across seams into the vertices and
triangles of one draw. `painter.build` uploads and draws them. This is
plain numpy, so tests check the geometry without a GPU.
"""
from __future__ import annotations

from math import ceil, sqrt

import numpy as np

from .plan import Stamps
from .resizing import resize
from .seams import CORNERS, Pieces


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


def quads(stamps: Stamps, size: int, origins: np.ndarray, cell: int) -> tuple[np.ndarray, np.ndarray]:
    """The corners of each stamp on the image and in the atlas, as ``(n, 4)`` complex arrays.

    A point is x + iy. A stamp of *size* covers the same square v2 wrote
    it into, starting at ``x - size // 2`` and *size* texels wide,
    rotated about its centre. At angle zero its corners sit on texel
    edges and the atlas cell maps onto it texel for texel, so the stamp
    reproduces its brush.
    """
    half = size / 2.0
    centre = (stamps.x - size // 2 + half) + 1j * (stamps.y - size // 2 + half)
    turn = np.cos(stamps.angle) + 1j * np.sin(stamps.angle)
    corners = centre[:, None] + np.array([-1 - 1j, 1 - 1j, 1 + 1j, -1 + 1j]) * half * turn[:, None]
    origin = origins[stamps.brush].astype(np.float64)
    coords = (origin[:, 0] + 1j * origin[:, 1])[:, None] + np.array([0, 1, 1 + 1j, 1j]) * cell
    return corners, coords


def geometry(corners: np.ndarray, coords: np.ndarray, colors: np.ndarray, owners: np.ndarray,
             pieces: Pieces):
    """Positions, atlas coordinates, colours, islands, sources and triangles for one draw.

    *corners* and *coords* come from `quads`, *colors* and *owners* from
    the same stamps, and *pieces* from `seams.pieces`. The triangles go
    stamp by stamp in planned order: each stamp's quad, then its pieces,
    nearest crossing first. The GPU blends in that order, so a later
    stamp covers an earlier one, exactly as in v2's loop.

    Every vertex of stamp k of n has the depth ``1 - 2 (k + 1) / (n + 1)``,
    so the first of a stamp's parts to paint a texel keeps it, and its
    other parts cannot paint it again. The next stamp is nearer, so it
    still paints over. Each vertex has two islands: the one its part
    paints, then the one a piece leaves, or 0 for a quad. The stamp
    shader keeps a quad to its island and texels of none, and a piece to
    its island, where on the island's own texels the point of the stamp
    it carries, its source, must be off the island it leaves. A quad's
    source is its own corner. A float holds any island number the island
    map can.
    """
    count = len(corners)
    depth = (1.0 - 2.0 * (np.arange(count) + 1) / (count + 1)).astype(np.float32)
    used = np.arange(CORNERS) < pieces.count[:, None]
    points = np.concatenate([corners.ravel(), pieces.points[used]])
    atlas_points = np.concatenate([coords.ravel(), pieces.coords[used]])
    source_points = np.concatenate([corners.ravel(), pieces.sources[used]])
    stamp_of = np.concatenate([np.repeat(np.arange(count), 4), np.repeat(pieces.stamp, pieces.count)])
    positions = np.stack([points.real, points.imag, depth[stamp_of]], axis=1).astype(np.float32)
    uvs = np.stack([atlas_points.real, atlas_points.imag], axis=1).astype(np.float32)
    sources = np.stack([source_points.real, source_points.imag], axis=1).astype(np.float32)
    quad_islands = np.stack([owners, np.zeros_like(owners)], axis=1)
    piece_islands = np.stack([pieces.island, owners[pieces.stamp]], axis=1)
    islands = np.concatenate([np.repeat(quad_islands, 4, axis=0),
                              np.repeat(piece_islands, pieces.count, axis=0)]).astype(np.float32)

    base = (np.arange(count) * 4)[:, None]
    quad_triangles = np.concatenate([base + (0, 1, 2), base + (0, 2, 3)], axis=1).reshape(-1, 3)
    # Each piece is convex, so it is a fan from its first corner.
    fans = pieces.count - 2
    piece_of = np.repeat(np.arange(len(fans)), fans)
    step = np.arange(fans.sum()) - np.repeat(np.cumsum(fans) - fans, fans)
    first = (4 * count + np.cumsum(pieces.count) - pieces.count)[piece_of]
    fan_triangles = np.stack([first, first + step + 1, first + step + 2], axis=1)
    # A stable sort keeps each quad before its pieces and the pieces in order.
    order = np.argsort(np.concatenate([np.repeat(np.arange(count), 2) * 2,
                                       pieces.stamp[piece_of] * 2 + 1]), kind='stable')
    triangles = np.concatenate([quad_triangles, fan_triangles])[order]
    return positions, uvs, colors[stamp_of], islands, sources, triangles.astype(np.int32)
