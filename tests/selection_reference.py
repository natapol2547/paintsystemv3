"""Float64 reference for the selection rasteriser (PS-091). Not a test itself.

The GPU passes in `selection/raster.py` are checked against these
functions, which evaluate the same coverage formulas per texel centre in
float64 with numpy. Nothing here imports `bpy`, so the module also runs in
a plain Python with numpy.
"""
import math

import numpy as np

FEATHER_MAX = 1024.0
MIN_RADIUS = 1e-3


def half_width(feather, antialias):
    """Half the width of the soft edge, in texels.

    The feather is clamped to ``0..FEATHER_MAX`` first, and a NaN feather
    counts as 0, as in the shader.
    """
    feather = float(feather)
    feather = min(max(feather, 0.0), FEATHER_MAX) if feather == feather else 0.0
    return 0.5 * max(feather, 1.0 if antialias else 0.0)


def profile(signed_distance, half):
    """Coverage from a signed distance, positive inside."""
    if half <= 0.0:
        return (signed_distance > 0.0).astype(np.float64)
    t = np.clip((signed_distance + half) / (2.0 * half), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def texel_centres(width, height, window=None):
    """Texel centre coordinates, row 0 at the bottom, shaped (rows, columns).

    *window* is ``(x0, y0, x1, y1)``, half-open, to evaluate part of the
    image only; the default is all of it.
    """
    x0, y0, x1, y1 = window if window is not None else (0, 0, width, height)
    return np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)


def _texels(points, width, height, tile):
    index = tile - 1001
    offset = (float(index % 10), float(index // 10))
    return (np.asarray(points, dtype=np.float64).reshape(-1, 2) - offset) * (width, height)


def box_signed_distance(xs, ys, low, high):
    centre = 0.5 * (low + high)
    extent = 0.5 * (high - low)
    qx = np.abs(xs - centre[0]) - extent[0]
    qy = np.abs(ys - centre[1]) - extent[1]
    outside = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0)) + np.minimum(np.maximum(qx, qy), 0.0)
    return -outside


def ellipse_signed_distance(xs, ys, centre, radii):
    """Signed distance to an axis-aligned ellipse, positive inside.

    Eberly's method with the root bracketed and bisected in u = s + 1 until
    the midpoint no longer moves, which float64 reaches in well under the
    2200 iterations allowed.
    """
    yx = np.abs(xs - centre[0])
    yy = np.abs(ys - centre[1])
    if radii[0] >= radii[1]:
        e0, e1, y0, y1 = radii[0], radii[1], yx, yy
    else:
        e0, e1, y0, y1 = radii[1], radii[0], yy, yx
    z0 = y0 / e0
    z1 = y1 / e1
    g = z0 * z0 + z1 * z1 - 1.0
    d = np.zeros_like(y0)
    r0_minus_1 = (e0 - e1) * (e0 + e1) / (e1 * e1)
    general = (y1 > 0.0) & (y0 > 0.0) & (g != 0.0)
    n0 = (r0_minus_1 + 1.0) * z0[general]
    w1 = z1[general]
    u0 = w1.copy()
    u1 = np.where(g[general] < 0.0, 1.0, np.hypot(n0, w1))
    u = u0.copy()
    for _ in range(2200):
        u = 0.5 * (u0 + u1)
        done = (u == u0) | (u == u1)
        if done.all():
            break
        value = (n0 / (u + r0_minus_1)) ** 2 + (w1 / u) ** 2 - 1.0
        exact = ~done & (value == 0.0)
        u0 = np.where((~done & (value > 0.0)) | exact, u, u0)
        u1 = np.where((~done & (value < 0.0)) | exact, u, u1)
    d[general] = np.abs(1.0 - u) * np.hypot(y0[general] / (u + r0_minus_1), y1[general] / u)
    on_minor = (y1 > 0.0) & (y0 == 0.0)
    d[on_minor] = np.abs(y1[on_minor] - e1)
    on_major = y1 == 0.0
    numer0 = e0 * y0
    denom0 = (e0 - e1) * (e0 + e1)
    near = on_major & (numer0 < denom0)
    xde0 = numer0[near] / denom0
    d[near] = np.hypot(e0 * xde0 - y0[near], e1 * np.sqrt(1.0 - xde0 * xde0))
    far = on_major & ~(numer0 < denom0)
    d[far] = np.abs(y0[far] - e0)
    return np.where(g < 0.0, d, -d)


def lasso_signed_distance(xs, ys, texels):
    """(signed distance, inside, unsigned distance) for a closed outline.

    Inside is even-odd: an edge counts when it straddles the texel centre's
    row, `(ay > y) != (by > y)`, and crosses it strictly left of the centre.
    """
    a = texels
    b = np.roll(texels, -1, axis=0)
    d = np.full(xs.shape, np.inf)
    inside = np.zeros(xs.shape, dtype=bool)
    for k in range(len(a)):
        abx, aby = b[k, 0] - a[k, 0], b[k, 1] - a[k, 1]
        apx, apy = xs - a[k, 0], ys - a[k, 1]
        length2 = abx * abx + aby * aby
        t = np.clip((apx * abx + apy * aby) / length2, 0.0, 1.0) if length2 > 0.0 else 0.0
        d = np.minimum(d, np.hypot(apx - abx * t, apy - aby * t))
        straddle = (a[k, 1] > ys) != (b[k, 1] > ys)
        with np.errstate(divide='ignore', invalid='ignore'):
            crossing = a[k, 0] + (ys - a[k, 1]) * abx / aby
        inside ^= straddle & (crossing < xs)
    return np.where(inside, d, -d), inside, d


def outline_distance(op, width, height, tile=1001, window=None):
    """``(signed distance, inside)`` for a BOX, ELLIPSE or LASSO op over *window*.

    The distance is in texels and positive inside. None when the op
    encloses nothing: a box or ellipse without two finite corners, a box
    with no area, an ellipse with a radius under `MIN_RADIUS`, or a lasso
    with a non-finite point or fewer than three points after dropping
    consecutive duplicates.
    """
    xs, ys = texel_centres(width, height, window)
    texels = _texels(op.get('points', ()), width, height, tile)
    kind = op['kind']
    if kind in ('BOX', 'ELLIPSE'):
        if len(texels) < 2 or not np.isfinite(texels[:2]).all():
            return None
        low = np.minimum(texels[0], texels[1])
        high = np.maximum(texels[0], texels[1])
        if kind == 'BOX':
            if np.any(high <= low):
                return None
            signed = box_signed_distance(xs, ys, low, high)
        else:
            radii = 0.5 * (high - low)
            if np.any(radii < MIN_RADIUS):
                return None
            signed = ellipse_signed_distance(xs, ys, 0.5 * (low + high), radii)
        return signed, signed > 0.0
    if kind == 'LASSO':
        if not np.isfinite(texels).all():
            return None
        if len(texels):
            texels = texels[np.any(texels != np.roll(texels, -1, axis=0), axis=1)]
        if len(texels) < 3:
            return None
        signed, inside, _ = lasso_signed_distance(xs, ys, texels)
        return signed, inside
    raise ValueError(f"{kind} has no outline")


def coverage(op, width, height, tile=1001, window=None):
    """Coverage of one op before its mode is applied, over *window*."""
    xs, _ = texel_centres(width, height, window)
    if op['kind'] == 'ALL':
        return np.ones(xs.shape)
    found = outline_distance(op, width, height, tile, window)
    if found is None:
        return np.zeros(xs.shape)
    signed, inside = found
    half = half_width(op.get('feather', 0.0), op.get('antialias', True))
    return inside.astype(np.float64) if half <= 0.0 else profile(signed, half)


def chain(ops, width, height, tile=1001, window=None):
    """The mask a list of op dicts combines to, from the first op on, over *window*.

    An op dict has ``kind`` and optionally ``mode``, ``feather``,
    ``antialias`` and ``points`` in UV, with the defaults of an `OpSpec`.
    """
    xs, _ = texel_centres(width, height, window)
    mask = np.zeros(xs.shape)
    for op in ops:
        if op['kind'] == 'INVERT':
            mask = 1.0 - mask
            continue
        covered = coverage(op, width, height, tile, window)
        mode = op.get('mode', 'REPLACE')
        if mode == 'REPLACE':
            mask = covered
        elif mode == 'ADD':
            mask = np.maximum(mask, covered)
        elif mode == 'SUBTRACT':
            mask = np.minimum(mask, 1.0 - covered)
        else:
            mask = np.minimum(mask, covered)
    return mask


def star(count, cx, cy, outer, inner, lobes, seed=0):
    """A lobed outline with 3% radial noise, in UV."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0.0, 2.0 * math.pi, count, endpoint=False)
    r = inner + (outer - inner) * (0.5 + 0.5 * np.cos(lobes * t))
    r = r * (1.0 + 0.03 * rng.standard_normal(count))
    return [(cx + rr * math.cos(tt), cy + rr * math.sin(tt)) for rr, tt in zip(r, t)]


def pentagram(cx, cy, r):
    """A self-intersecting five-point star, in UV."""
    return [(cx + r * math.cos(math.pi / 2 + k * 4 * math.pi / 5),
             cy + r * math.sin(math.pi / 2 + k * 4 * math.pi / 5)) for k in range(5)]


def double_loop(cx, cy, r1, r2, count=48):
    """Two concentric circles joined into one outline, in UV."""
    points = []
    for r in (r1, r2):
        for i in range(count):
            t = 2.0 * math.pi * i / count
            points.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return points
