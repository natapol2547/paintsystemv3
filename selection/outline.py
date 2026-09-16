"""Lookup tables that let one fragment pass fill and feather a lasso (PS-091).

A lasso outline can have thousands of points, and a fragment shader
cannot loop over all of them per texel at 4K. This module does the
per-outline work once, on the CPU with numpy, and packs the result into
float arrays the pass in `raster.py` uploads as textures. Nothing here
touches `bpy` or `gpu`.

Two tables are built, both in texel space with texel centres at
``(i + 0.5, j + 0.5)``:

- **Parity.** For every texel row, the columns at which the outline
  crosses the row's centre line, sorted. A texel is inside when an odd
  number of crossings lie at or left of its column. The crossings of a
  row are split into spans of `SPAN_WIDTH` columns, so the shader starts
  from the span its column falls in and reads a handful of keys rather
  than the whole row. The rule is the even-odd test of a ray to the left
  of the texel centre, with an edge counted when its end points lie on
  opposite sides of the centre line, one of them possibly on it.
- **Distance.** Only a feathered or anti-aliased lasso needs it. The
  image is divided into square cells, and each cell lists the segments
  that can be the nearest one to some texel in it, sorted by their
  distance from the cell centre. The shader walks the list and stops
  once the next segment cannot be closer than the best so far. Segment
  end points are stored relative to the cell and clipped to the cell
  grown by the half width and a texel, which keeps them small enough
  that float32 loses nothing that matters.

Every limit below raises `OutlineTooComplex` instead of allocating
without bound: an outline that crosses millions of rows or cells is not
something a selection tool produces, and failing is better than
stalling Blender.
"""
import math

import numpy as np

DATA_WIDTH = 4096
"""Width of the one-dimensional data textures (keys, entries, bounds).
Their height grows with the data."""

SPAN_WIDTH = 64
"""Columns covered by one parity span."""

MAX_CELL = 128
"""Largest distance cell, in texels, used from a half width above 64."""

MARGIN = 0.01
"""Slack in texels on every distance comparison that prunes a segment, so
float rounding cannot drop the nearest one."""

MAX_KEYS = 1 << 22
"""Most row crossings an outline may produce."""

MAX_PAIRS = 1 << 25
"""Most segment and cell candidates the distance table may examine."""

MAX_ENTRIES = 1 << 22
"""Most segment entries the distance table may keep."""

CHUNK = 1 << 20
"""Rows of intermediate arrays processed at once, to bound peak memory."""


class OutlineTooComplex(ValueError):
    """The outline would exceed one of the limits in this module."""


def closed_outline(points) -> tuple[np.ndarray, np.ndarray] | None:
    """The edges of the closed polygon through *points*, in texels.

    Returns ``(a, b)``, two ``(n, 2)`` float64 arrays where edge ``k``
    runs from ``a[k]`` to ``b[k]`` and the last edge closes the outline.
    Consecutive duplicate points, including a last point equal to the
    first, are dropped first. None when fewer than three points remain.
    Points that repeat non-consecutively, such as ``(a, b, a, b)``, are
    kept, and like any collinear outline they draw a soft line when
    feathered or anti-aliased.
    """
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(points):
        points = points[np.any(points != np.roll(points, -1, axis=0), axis=1)]
    if len(points) < 3:
        return None
    return points, np.roll(points, -1, axis=0)


def _expand(counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For ``counts[k]`` items per owner, each item's owner and index within it."""
    total = int(counts.sum())
    owner = np.repeat(np.arange(len(counts)), counts)
    starts = np.cumsum(counts) - counts
    local = np.arange(total) - np.repeat(starts, counts)
    return owner, local


def _chunks(counts: np.ndarray, limit: int):
    """``(start, stop)`` ranges of owners whose items add up to about *limit*.

    A single owner with more items than *limit* is a range on its own.
    """
    ends = np.cumsum(counts)
    start = 0
    while start < len(counts):
        base = int(ends[start - 1]) if start else 0
        stop = max(int(np.searchsorted(ends, base + limit, side='right')), start + 1)
        yield start, stop
        start = stop


def _padded(values: np.ndarray, channels: int) -> np.ndarray:
    """*values* zero-padded to a whole number of `DATA_WIDTH` rows."""
    length = -(-max(len(values), 1) // DATA_WIDTH) * DATA_WIDTH
    out = np.zeros((length, channels) if channels > 1 else length, dtype=np.float32)
    out[:len(values)] = values
    return out


def parity_tables(a: np.ndarray, b: np.ndarray, width: int,
                  height: int) -> tuple[np.ndarray, np.ndarray]:
    """The even-odd fill tables for edges *a* to *b* on a *width* x *height* image.

    Returns ``(spans, keys)``:

    - ``spans``, float32 ``(height, ceil(width / SPAN_WIDTH), 2)``: for the
      span holding columns ``s * SPAN_WIDTH`` up to the next span, channel
      0 is the index of its first key and channel 1 is ``2 * count +
      parity``, where ``count`` is the number of keys in the span and
      ``parity`` is 1 when an odd number of the row's keys lie in the
      spans before it.
    - ``keys``, float32 with a length that is a multiple of `DATA_WIDTH`:
      every row's crossing columns, rows in order and sorted within a row.
      A crossing at x has key ``clamp(floor(x - 0.5) + 1, 0, width)``, the
      first column whose centre lies strictly right of it, so texel ``i``
      counts it when ``key <= i``.

    Row ``j`` holds a crossing of every edge with ``ceil(ymin - 0.5) <= j <
    ceil(ymax - 0.5)``. That half-open range counts a vertex exactly on a
    centre line once for the edge above it, and never counts a horizontal
    edge.
    """
    ymin = np.minimum(a[:, 1], b[:, 1])
    ymax = np.maximum(a[:, 1], b[:, 1])
    first = np.clip(np.ceil(ymin - 0.5), 0, height).astype(np.int64)
    end = np.maximum(np.clip(np.ceil(ymax - 0.5), 0, height).astype(np.int64), first)
    counts = end - first
    if int(counts.sum()) > MAX_KEYS:
        raise OutlineTooComplex("the outline crosses too many rows")
    parts = [np.empty(0, dtype=np.int64)]
    for start, stop in _chunks(counts, CHUNK):
        segment, offset = _expand(counts[start:stop])
        segment += start
        row = first[segment] + offset
        ax = a[segment, 0]
        ay = a[segment, 1]
        crossing = ax + (row + 0.5 - ay) * (b[segment, 0] - ax) / (b[segment, 1] - ay)
        key = np.clip(np.floor(crossing - 0.5) + 1.0, 0.0, float(width)).astype(np.int64)
        parts.append(row * (width + 1) + key)
    composite = np.sort(np.concatenate(parts))
    row = composite // (width + 1)
    key = composite - row * (width + 1)

    # A key equal to `width` lies right of every texel and is never
    # counted; it falls in an extra span column that is dropped below.
    columns = -(-width // SPAN_WIDTH)
    slot = row * (columns + 1) + np.minimum(key // SPAN_WIDTH, columns)
    per_slot = np.bincount(slot, minlength=height * (columns + 1))
    starts = (np.cumsum(per_slot) - per_slot).reshape(height, columns + 1)
    per_slot = per_slot.reshape(height, columns + 1)
    parity = (starts - starts[:, :1]) % 2
    spans = np.empty((height, columns, 2), dtype=np.float32)
    spans[..., 0] = starts[:, :columns]
    spans[..., 1] = per_slot[:, :columns] * 2 + parity[:, :columns]
    return spans, _padded(key.astype(np.float32), 1)


def cell_size(half_width: float) -> int:
    """The distance cell size for a half width, in texels.

    Small cells keep the lists short for a thin edge; large cells keep
    their number down for a wide feather, where every cell near the
    outline lists much the same segments anyway.
    """
    if half_width <= 8.0:
        return 32
    if half_width <= 64.0:
        return 64
    return MAX_CELL


def _segment_distance(px, py, ax, ay, bx, by):
    """Distance from points *p* to segments *a* to *b*, element-wise."""
    abx = bx - ax
    aby = by - ay
    apx = px - ax
    apy = py - ay
    length2 = abx * abx + aby * aby
    t = np.where(length2 > 0.0, (apx * abx + apy * aby) / np.where(length2 > 0.0, length2, 1.0), 0.0)
    t = np.clip(t, 0.0, 1.0)
    return np.hypot(apx - abx * t, apy - aby * t)


def distance_tables(a: np.ndarray, b: np.ndarray, width: int, height: int,
                    half_width: float, cell: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The nearest-segment tables for edges *a* to *b*.

    Returns ``(cells, entries, bounds)``:

    - ``cells``, float32 ``(ceil(height / cell), ceil(width / cell), 2)``:
      the index of the cell's first entry and the number of entries.
    - ``entries``, float32 ``(n, 4)`` with ``n`` a multiple of
      `DATA_WIDTH`: segment start x, y and end x, y relative to the cell's
      lower-left corner, clipped to the cell grown by ``half_width + 1``.
    - ``bounds``, float32 ``(n,)``: each entry's distance from its cell
      centre, less `MARGIN`. Entries of a cell are sorted by it.

    A segment is listed for a cell when its distance ``d`` from the cell
    centre is below ``min(half_width + h, nearest + 2 * h) + MARGIN``,
    where ``h`` is half the cell diagonal and ``nearest`` is the smallest
    ``d`` of any segment in that cell. No texel in the cell is further
    than ``h`` from its centre, so a segment beyond that limit is either
    further than the half width from every texel, where coverage no
    longer depends on the distance, or further than the nearest segment
    is from all of them.
    """
    columns = -(-width // cell)
    rows = -(-height // cell)
    half_diagonal = cell * math.sqrt(0.5)
    reach = half_width + half_diagonal + MARGIN
    low = np.minimum(a, b) - reach
    high = np.maximum(a, b) + reach
    x0 = np.clip(np.floor(low[:, 0] / cell), 0, columns).astype(np.int64)
    x1 = np.clip(np.floor(high[:, 0] / cell) + 1, 0, columns).astype(np.int64)
    y0 = np.clip(np.floor(low[:, 1] / cell), 0, rows).astype(np.int64)
    y1 = np.clip(np.floor(high[:, 1] / cell) + 1, 0, rows).astype(np.int64)
    across = np.maximum(x1 - x0, 0)
    counts = across * np.maximum(y1 - y0, 0)
    if int(counts.sum()) > MAX_PAIRS:
        raise OutlineTooComplex("the outline covers too many cells")

    nearest = np.full(columns * rows, np.inf)
    kept_segments = [np.empty(0, dtype=np.int64)]
    kept_cells = [np.empty(0, dtype=np.int64)]
    kept_distances = [np.empty(0)]
    for start, stop in _chunks(counts, CHUNK):
        segment, offset = _expand(counts[start:stop])
        segment += start
        wide = np.maximum(across[segment], 1)
        ix = x0[segment] + offset % wide
        iy = y0[segment] + offset // wide
        distance = _segment_distance((ix + 0.5) * cell, (iy + 0.5) * cell,
                                     a[segment, 0], a[segment, 1], b[segment, 0], b[segment, 1])
        keep = distance < reach
        cell_id = (iy * columns + ix)[keep]
        np.minimum.at(nearest, cell_id, distance[keep])
        kept_segments.append(segment[keep])
        kept_cells.append(cell_id)
        kept_distances.append(distance[keep])
    segment = np.concatenate(kept_segments)
    cell_id = np.concatenate(kept_cells)
    distance = np.concatenate(kept_distances)

    limit = np.minimum(half_width + half_diagonal, nearest + 2.0 * half_diagonal) + MARGIN
    keep = distance < limit[cell_id]
    segment, cell_id, distance = segment[keep], cell_id[keep], distance[keep]
    if len(segment) > MAX_ENTRIES:
        raise OutlineTooComplex("the outline needs too many distance entries")
    # One sort by cell, then by distance within the cell: every distance
    # is below `reach`, so a power of two above it separates the cells.
    order = np.argsort(cell_id * 2.0 ** math.ceil(math.log2(reach + 2.0)) + distance, kind='stable')
    segment, cell_id, distance = segment[order], cell_id[order], distance[order]

    origin = np.stack(((cell_id % columns) * cell, (cell_id // columns) * cell), axis=1).astype(np.float64)
    start_point = a[segment] - origin
    end_point = b[segment] - origin
    # Clip every entry to its cell grown by the half width and a texel. A
    # point of the segment outside that box is further than the half width
    # from every texel centre in the cell, so the distances that matter are
    # unchanged, and the stored end points stay small enough for float32.
    box_low = -(half_width + 1.0)
    box_high = cell + half_width + 1.0
    outside = ((np.minimum(start_point, end_point) < box_low)
               | (np.maximum(start_point, end_point) > box_high)).any(axis=1)
    keep = np.ones(len(segment), dtype=bool)
    if outside.any():
        index = np.flatnonzero(outside)
        p = start_point[index]
        delta = end_point[index] - p
        lower = np.zeros(len(index))
        upper = np.ones(len(index))
        with np.errstate(divide='ignore', invalid='ignore'):
            for axis in (0, 1):
                d = delta[:, axis]
                t_low = (box_low - p[:, axis]) / d
                t_high = (box_high - p[:, axis]) / d
                flat = d == 0.0
                lower = np.where(flat, lower, np.maximum(lower, np.minimum(t_low, t_high)))
                upper = np.where(flat, upper, np.minimum(upper, np.maximum(t_low, t_high)))
                missed = flat & ((p[:, axis] < box_low) | (p[:, axis] > box_high))
                upper = np.where(missed, -1.0, upper)
        start_point[index] = p + lower[:, None] * delta
        end_point[index] = p + upper[:, None] * delta
        keep[index] = lower <= upper
    cell_id, distance = cell_id[keep], distance[keep]
    start_point, end_point = start_point[keep], end_point[keep]

    per_cell = np.bincount(cell_id, minlength=columns * rows)
    cells = np.empty((rows, columns, 2), dtype=np.float32)
    cells[..., 0] = (np.cumsum(per_cell) - per_cell).reshape(rows, columns)
    cells[..., 1] = per_cell.reshape(rows, columns)
    entries = _padded(np.concatenate((start_point, end_point), axis=1).astype(np.float32), 4)
    bounds = _padded((distance - MARGIN).astype(np.float32), 1)
    return cells, entries, bounds
