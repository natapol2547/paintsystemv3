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


# ── Selections drawn in the 3D view (PS-093) ─────────────────────────

REACH_PAD = 1.5
DEPTH_REL_BIAS = 1e-5
DEPTH_ABS_BIAS = 1e-6
TEXEL_SLOPE_CAP = 0.03
MARGIN_ALPHA = 0.75
"""Copies of the `selection/view_raster.py` constants, which the tests compare."""


def is_orthographic(projection):
    return bool(np.allclose(projection[3], (0.0, 0.0, 0.0, 1.0)))


def view_eye(view, projection):
    """World eye position (w 1) in perspective, unit direction to the viewer (w 0) in orthographic."""
    inverse = np.linalg.inv(view)
    if is_orthographic(projection):
        direction = inverse[:3, 2]
        return np.append(direction / np.linalg.norm(direction), 0.0)
    return np.append(inverse[:3, 3], 1.0)


def _homogeneous(points):
    return np.concatenate([points, np.ones((len(points), 1))], axis=1)


def _depth_q(points, view, orthographic):
    depth = -(_homogeneous(points) @ view.T)[:, 2]
    return depth if orthographic else -1.0 / depth


def _screen(points, view, projection, region):
    clip = _homogeneous(points) @ (projection @ view).T
    return (clip[:, :2] / clip[:, 3:4] * 0.5 + 0.5) * region, clip


def bilinear(grid, s):
    """*grid* `(h, w)` read at pixel coordinates *s* `(n, 2)`, edge texels clamped."""
    height, width = grid.shape
    f = s - 0.5
    base = np.floor(f)
    t = f - base
    i0 = base.astype(int)

    def at(dx, dy):
        return grid[np.clip(i0[:, 1] + dy, 0, height - 1), np.clip(i0[:, 0] + dx, 0, width - 1)]

    return ((at(0, 0) * (1 - t[:, 0]) + at(1, 0) * t[:, 0]) * (1 - t[:, 1])
            + (at(0, 1) * (1 - t[:, 0]) + at(1, 1) * t[:, 0]) * t[:, 1])


def depth_buffer(triangles, view, projection, region):
    """The depth pass: `(q, slope)` per region pixel, nearest q at each covered pixel centre.

    q is `d` in orthographic and `-1/d` in perspective, both affine in
    screen space over a triangle, so the slope `max(|dq/dx|, |dq/dy|)` is
    exact. Uncovered pixels hold q 3e38 and slope 0.
    """
    width, height = region
    orthographic = is_orthographic(projection)
    q = np.full((height, width), 3.0e38)
    slope = np.zeros((height, width))
    xs, ys = texel_centres(width, height)
    for k in range(0, len(triangles), 3):
        corner = np.asarray(triangles[k:k + 3], dtype=np.float64)
        s, _ = _screen(corner, view, projection, np.asarray(region, dtype=np.float64))
        coef = np.linalg.solve(np.c_[s, np.ones(3)], _depth_q(corner, view, orthographic))
        plane = coef[0] * xs + coef[1] * ys + coef[2]
        area = (s[1, 0] - s[0, 0]) * (s[2, 1] - s[0, 1]) - (s[1, 1] - s[0, 1]) * (s[2, 0] - s[0, 0])
        inside = np.ones(xs.shape, dtype=bool)
        for i in range(3):
            p0, p1 = s[i], s[(i + 1) % 3]
            inside &= ((p1[0] - p0[0]) * (ys - p0[1]) - (p1[1] - p0[1]) * (xs - p0[0])) * np.sign(area) > 0
        take = inside & (plane < q)
        q = np.where(take, plane, q)
        slope = np.where(take, max(abs(coef[0]), abs(coef[1])), slope)
    return q, slope


def surface_steps(positions, normals):
    """World steps to the next texel along x and y, as the texel pass picks them."""
    padded = np.pad(positions, ((1, 1), (1, 1), (0, 0)))
    p = positions[..., :3]
    n = normals[..., :3]

    def neighbour(di, dj):
        return padded[1 + dj:padded.shape[0] - 1 + dj, 1 + di:padded.shape[1] - 1 + di]

    def pick(ahead, behind):
        forward = ahead[..., :3] - p
        backward = p - behind[..., :3]
        both = (ahead[..., 3] > MARGIN_ALPHA) & (behind[..., 3] > MARGIN_ALPHA)
        leaves_less = (np.abs(np.einsum('...i,...i', backward, n)) * np.linalg.norm(forward, axis=-1)
                       < np.abs(np.einsum('...i,...i', forward, n)) * np.linalg.norm(backward, axis=-1))
        return np.where((both & leaves_less)[..., None], backward,
                        np.where((ahead[..., 3] > MARGIN_ALPHA)[..., None], forward,
                                 np.where((behind[..., 3] > MARGIN_ALPHA)[..., None], backward,
                                          np.where((ahead[..., 3] > 0.0)[..., None], forward,
                                                   np.where((behind[..., 3] > 0.0)[..., None], backward, 0.0)))))

    return pick(neighbour(1, 0), neighbour(-1, 0)), pick(neighbour(0, 1), neighbour(0, -1))


def view_chain(scene, ops, through, region=(64, 64)):
    """The mask the `VIEW` ops *ops* combine to over a texel map, from an empty mask.

    *scene* has `positions` and `normals` `(rows, columns, 4)` with
    coverage in alpha, `triangles` `(n, 3)` for the depth pass, and float64
    4 x 4 `view` and `projection`. *ops* are
    `(kind, mode, feather, antialias, points in region pixels)`.
    """
    positions, normals = scene["positions"], scene["normals"]
    view, projection = scene["view"], scene["projection"]
    region = np.asarray(region, dtype=np.float64)
    orthographic = is_orthographic(projection)
    shape = positions.shape[:2]
    alpha = positions[..., 3].ravel()
    p = positions[..., :3].reshape(-1, 3)
    s, clip = _screen(p, view, projection, region)
    ndc_z = clip[:, 2] / clip[:, 3]
    in_view = ((orthographic | (clip[:, 3] > 0.0)) & (np.abs(ndc_z) <= 1.0)
               & (s >= 0.0).all(axis=1) & (s <= region).all(axis=1))

    dx, dy = surface_steps(positions, normals)
    dx, dy = dx.reshape(-1, 3), dy.reshape(-1, 3)
    cross = np.cross(dx, dy)
    length = np.linalg.norm(cross, axis=1)
    smooth = normals[..., :3].reshape(-1, 3)
    normal = np.where((length > 1e-12)[:, None], cross / np.maximum(length, 1e-300)[:, None], smooth)
    normal = np.where((np.einsum('ij,ij->i', normal, smooth) < 0.0)[:, None], -normal, normal)
    eye = view_eye(view, projection)
    facing = np.einsum('ij,ij->i', normal, eye[None, :3] - p * eye[3]) > 0.0

    q_buffer, slope_buffer = depth_buffer(scene["triangles"], view, projection, region.astype(int))
    q = _depth_q(p, view, orthographic)
    su = _screen(p + dx, view, projection, region)[0] - s
    sv = _screen(p + dy, view, projection, region)[0] - s
    qu = _depth_q(p + dx, view, orthographic) - q
    qv = _depth_q(p + dy, view, orthographic) - q
    det = su[:, 0] * sv[:, 1] - su[:, 1] * sv[:, 0]
    with np.errstate(divide='ignore', invalid='ignore'):
        gx = (qu * sv[:, 1] - qv * su[:, 1]) / det
        gy = (qv * su[:, 0] - qu * sv[:, 0]) / det
    texel_slope = np.where(np.abs(det) < 1e-12, 3.0e38, np.maximum(np.abs(gx), np.abs(gy)))
    texel_slope = np.minimum(texel_slope, TEXEL_SLOPE_CAP * np.abs(q))
    absolute_bias = DEPTH_ABS_BIAS if orthographic else 0.0
    f = s - 0.5
    base = np.floor(f)
    t = f - base
    i0 = base.astype(int)
    visible = np.zeros(len(p))
    for tap_y in (0, 1):
        for tap_x in (0, 1):
            ax = np.clip(i0[:, 0] + tap_x, 0, int(region[0]) - 1)
            ay = np.clip(i0[:, 1] + tap_y, 0, int(region[1]) - 1)
            stored = q_buffer[ay, ax]
            offset = np.abs(ax + 0.5 - s[:, 0]) + np.abs(ay + 0.5 - s[:, 1])
            allowed = (stored + (slope_buffer[ay, ax] + texel_slope) * offset
                       + DEPTH_REL_BIAS * np.abs(stored) + absolute_bias)
            weight = (1 - t[:, 0] if tap_x == 0 else t[:, 0]) * (1 - t[:, 1] if tap_y == 0 else t[:, 1])
            visible += np.where(q <= allowed, weight, 0.0)
    visible = np.where(alpha <= MARGIN_ALPHA, 1.0, visible)
    terms = np.ones(len(p)) if through else facing * visible

    width, height = (int(value) for value in region)
    mask = np.zeros(len(p))
    for kind, mode, feather, antialias, points in ops:
        half = half_width(feather, antialias)
        reach = half + REACH_PAD
        op = dict(kind=kind, points=[(x / width, y / height) for x, y in points])
        found = outline_distance(op, width, height)
        distance = np.full((height, width), -reach) if found is None else np.clip(found[0], -reach, reach)
        covered = np.where(in_view & (alpha > 0.0), profile(bilinear(distance, s), half), 0.0) * terms
        if mode == 'REPLACE':
            mask = covered
        elif mode == 'ADD':
            mask = np.maximum(mask, covered)
        elif mode == 'SUBTRACT':
            mask = np.minimum(mask, 1.0 - covered)
        else:
            mask = np.minimum(mask, covered)
    return mask.reshape(shape)


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
