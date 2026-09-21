"""Outline geometry for the 3D view selection tools, in region pixels (PS-093).

Pure Python with no `bpy`, so the tests check it without Blender state.
Screen y points up, as in `Event.mouse_region_y`.
"""
import math

LASSO_MAX_POINTS = 4096
"""Points a lasso keeps while it is drawn; past it every other point is dropped and the step doubles."""


def box_corners(start, end, square: bool, centre: bool) -> tuple[tuple[float, float], tuple[float, float]]:
    """Two opposite corners of the box dragged from *start* to *end*.

    *square* makes both sides as long as the longer one, keeping the
    direction of the drag. *centre* makes *start* the middle of the box
    rather than a corner.
    """
    sx, sy = start
    dx, dy = end[0] - sx, end[1] - sy
    if square:
        side = max(abs(dx), abs(dy))
        dx = math.copysign(side, dx)
        dy = math.copysign(side, dy)
    if centre:
        return (sx - dx, sy - dy), (sx + dx, sy + dy)
    return (sx, sy), (sx + dx, sy + dy)


def box_outline(low, high) -> list[tuple[float, float]]:
    """The four corners of the box with opposite corners *low* and *high*."""
    return [tuple(low), (high[0], low[1]), tuple(high), (low[0], high[1])]


def ellipse_outline(low, high, segments: int = 64) -> list[tuple[float, float]]:
    """*segments* points on the ellipse inscribed in the box with opposite corners *low* and *high*."""
    cx, cy = (low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0
    rx, ry = abs(high[0] - low[0]) / 2.0, abs(high[1] - low[1]) / 2.0
    return [(cx + rx * math.cos(2.0 * math.pi * i / segments), cy + ry * math.sin(2.0 * math.pi * i / segments))
            for i in range(segments)]


def lasso_append(points: list, point, step: float) -> float:
    """Append *point* to a lasso when it is at least *step* from the last point; the step for the next one.

    Past `LASSO_MAX_POINTS` every other point is dropped, keeping the
    first and the one just appended, so a long drag stays cheap to draw
    and to rasterise at a coarser spacing. The returned step is then
    doubled, so later points are spaced like the kept ones and the start
    of the lasso is not thinned again on every later overflow.
    """
    point = (float(point[0]), float(point[1]))
    if points and math.hypot(point[0] - points[-1][0], point[1] - points[-1][1]) < step:
        return step
    points.append(point)
    if len(points) > LASSO_MAX_POINTS:
        kept = points[::2]
        if kept[-1] != point:
            kept.append(point)
        points[:] = kept
        return step * 2.0
    return step


def degenerate(kind: str, points) -> bool:
    """Whether an outline of *kind* encloses no area.

    A box or ellipse needs two corners with a non-zero width and height.
    A lasso needs three distinct points that are not all on one line.
    """
    points = [(float(x), float(y)) for x, y in points]
    if kind in ('BOX', 'ELLIPSE'):
        if len(points) < 2:
            return True
        (x0, y0), (x1, y1) = points[:2]
        return x0 == x1 or y0 == y1
    distinct = list(dict.fromkeys(points))
    if len(distinct) < 3:
        return True
    ax, ay = distinct[0]
    bx, by = distinct[1]
    return all((bx - ax) * (y - ay) - (by - ay) * (x - ax) == 0.0 for x, y in distinct[2:])


def signed_area(points) -> float:
    """Shoelace area of a closed outline: positive when it runs counter-clockwise with y up."""
    points = list(points)
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))


def clockwise(points) -> list[tuple[float, float]]:
    """*points* as a list running clockwise on screen, with y up.

    The overlay's ants crawl clockwise around a selection, so a preview
    drawn the same way crawls in step with them.
    """
    points = [tuple(point) for point in points]
    if signed_area(points) > 0.0:
        points.reverse()
    return points


def quad_strip(points, width: float) -> tuple[list, list]:
    """TRIS vertices for a closed outline drawn *width* pixels wide: positions and per-vertex tangents.

    The last point joins back to the first. Each segment is a quad along
    its unit tangent, extended by half the width at both ends so corners
    are filled. Zero-length segments are skipped.
    """
    half = width / 2.0
    points = list(points)
    positions, tangents = [], []
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        length = math.hypot(bx - ax, by - ay)
        if length == 0.0:
            continue
        tx, ty = (bx - ax) / length, (by - ay) / length
        nx, ny = -ty * half, tx * half
        ax, ay, bx, by = ax - tx * half, ay - ty * half, bx + tx * half, by + ty * half
        quad = ((ax + nx, ay + ny), (ax - nx, ay - ny), (bx - nx, by - ny), (bx + nx, by + ny))
        positions += [quad[0], quad[1], quad[2], quad[0], quad[2], quad[3]]
        tangents += [(tx, ty)] * 6
    return positions, tangents
