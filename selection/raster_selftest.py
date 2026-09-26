"""The GPU self-tests of selection masks (PS-091, PS-093).

A driver can compile the mask shaders and still draw them wrongly. So
before the first build of a session, `raster.self_test` renders two fixed
chains and compares chosen texels with float64 values from
`tests/selection_reference.py`. `view_raster.view_self_test` does the
same for `VIEW` ops, before the first build that holds one. A failure
gives the ops it covers `SELF_TEST` for the rest of the session. Both
memoise their result, and this module only renders and compares.

The view self-test cannot catch:

- dropping only one of the two depth slope terms
- dropping `REACH_PAD`
- using a smooth normal for facing instead of the geometric one
- in perspective, a missing `clip.w` in-front test, an eye vector used
  as the eye position for facing, or a missing perspective divide in
  the texel slope

`view_eye` runs on the CPU, so the tests check it instead.
"""
import logging
import math

import gpu
import numpy as np

from ..gpu_passes import core
from . import raster, view_raster

log = logging.getLogger(__name__)

SELF_TEST_SIZE = 64
"""Width and height of the self-test target, in texels."""

SELF_TEST_WIDE_SIZE = (140, 64)
"""Width and height of the second self-test target, in texels. It is
three span columns wide, so its lasso tables use more than one."""

SELF_TEST_TOLERANCE = 1e-4
"""Largest difference from the expected values the self-test accepts."""

SELF_TEST_TEXELS = 32
"""Width and height of the view self-test mask and texel map."""

SELF_TEST_REGION = (64, 64)
"""Region size of the self-test views, in pixels."""

FLOOR_TILT = -0.25
"""Slope of the self-test floor in z per unit x, so its depth has a screen slope."""


def _run_self_test(name: str, chains) -> bool | None:
    """Render each of *chains* and compare it with its expected texels.

    Each chain is `(label, render, expected)`, with *expected* mapping
    (x, y) to a value. Returns True when every texel is within
    `SELF_TEST_TOLERANCE`. Returns False when one is not, or when a render
    raised anything but `RuntimeError`. Returns None when a render raised
    `RuntimeError`. `MaskUnavailable` propagates.
    """
    failed = {}
    try:
        for label, draw, expected in chains:
            got = draw()
            for (x, y), value in expected.items():
                off = abs(float(got[y, x]) - value)
                if not off <= SELF_TEST_TOLERANCE:
                    failed[f"({x}, {y}) of {label}"] = off
    except raster.MaskUnavailable:
        raise
    except RuntimeError as error:
        # gpu.types raises RuntimeError when no GPU context is active,
        # as in a load_post handler on Blender 5.3, or when an allocation
        # fails. Neither shows that the GPU draws wrongly, so the result
        # is not memoised and the next build runs the self-test again.
        log.warning("The %s self-test could not run and will be retried: %s", name, str(error))
        return None
    except Exception:
        log.exception("The %s self-test could not run", name)
        return False
    if failed:
        log.error("The %s self-test failed on %s: texels off by more than %g: %s",
                  name, gpu.platform.renderer_get(), SELF_TEST_TOLERANCE, failed)
    return not failed


# ── Masks drawn in UV space ──────────────────────────────────────────

SELF_TEST_OPS = (
    raster.OpSpec('BOX', 'REPLACE', 8.0, True, [(0.25, 0.125), (0.75, 0.5)]),
    raster.OpSpec('ELLIPSE', 'ADD', 16.0, True, [(20.25 / 64, 36.25 / 64), (44.75 / 64, 60.75 / 64)]),
    raster.OpSpec('LASSO', 'SUBTRACT', 4.0, True,
                  [(28.25 / 64, 4.25 / 64), (60.25 / 64, 4.25 / 64), (60.25 / 64, 36.25 / 64)]),
    raster.OpSpec('LASSO', 'ADD', 0.0, False,
                  [(2 / 64, 40 / 64), (12 / 64, 40 / 64), (12 / 64, 62 / 64), (2 / 64, 62 / 64)]),
    raster.OpSpec('INVERT', 'ADD'),
    raster.OpSpec('BOX', 'INTERSECT', 0.0, True, [(0.0, 0.0), (1.0, 0.96875)]),
)
"""A 64 x 64 chain of six ops.

In order: a box feathered by 8 texels, an added circle feathered by 16,
a subtracted triangle lasso feathered by 4, an added hard square lasso,
an inversion, and an intersected anti-aliased box.

`SELF_TEST_EXPECTED` checks the soft edge profile, the ellipse's early
out and its root, the lasso distance tables over a 2 x 2 grid of
32-texel cells, the hard branch of the lasso pass, all four modes,
`INVERT`, and an `ADD` of fractional coverage to a fractional mask
(where `max` differs from a sum). Every lasso table fits in one span
column and one data texture row."""

SELF_TEST_EXPECTED = {
    (12, 16): 0.98876953125,
    (17, 16): 0.23193359375,
    (20, 16): 0.0,
    (20, 40): 0.6986395918352175,
    (47, 48): 0.7476577758789062,
    (43, 57): 0.6803087713210262,
    (40, 60): 0.6986395918352175,
    (30, 10): 0.09228515625,
    (44, 12): 1.0,
    (58, 30): 1.0,
    (5, 45): 0.0,
    (12, 50): 0.9997371090224711,
    (1, 50): 1.0,
    (32, 62): 0.0,
    (32, 61): 0.5701065063476562,
    (63, 0): 1.0,
    (33, 33): 0.7504059740217613,
}
"""Texel (x, y) to the value of `SELF_TEST_OPS` there, computed in float64
by `tests/selection_reference.py`. At (33, 33) the circle adds coverage
0.2496 to the box's 0.2319."""


def _self_test_comb() -> list[tuple[float, float]]:
    """The outline of the comb lasso in `SELF_TEST_WIDE_OPS`, in UV.

    33 teeth, each 2 texels wide, one every 4 texels from x = 3.3. Each
    tooth runs from below the bottom row to above the top row, and the
    teeth are joined below the target. The 133 points cross each of the
    64 rows 66 times.
    """
    width, height = SELF_TEST_WIDE_SIZE
    teeth = 33
    points = [(3.3, -2.0)]
    for tooth in range(teeth):
        left = 3.3 + 4.0 * tooth
        points += [(left, 66.0), (left + 2.0, 66.0), (left + 2.0, -1.0)]
        points.append((left + 4.0, -1.0) if tooth < teeth - 1 else (left + 2.0, -2.0))
    return [(x / width, y / height) for x, y in points]


SELF_TEST_WIDE_OPS = (
    raster.OpSpec('ALL'),
    raster.OpSpec('BOX', 'SUBTRACT', 0.0, False, [(20.25 / 140, 10.25 / 64), (60.75 / 140, 30.75 / 64)]),
    raster.OpSpec('LASSO', 'INTERSECT', 0.0, False, _self_test_comb()),
)
"""A chain on `SELF_TEST_WIDE_SIZE`: everything selected, then a
subtracted hard box and an intersected hard comb lasso.

`SELF_TEST_WIDE_EXPECTED` checks `ALL`, the hard branch of the edge
profile, and the lasso parity tables past one span column and one data
texture row. The comb's 4224 crossings fill two rows of the key texture.
Its teeth run through all three span columns and straddle their
boundaries, so both the span column lookup and the parity carried into a
span are used."""

SELF_TEST_WIDE_EXPECTED = {
    (5, 1): 0.0, (7, 1): 1.0, (29, 1): 0.0, (31, 1): 1.0, (69, 1): 0.0,
    (71, 1): 1.0, (101, 1): 0.0, (103, 1): 1.0, (133, 1): 0.0, (135, 1): 0.0,
    (5, 20): 0.0, (7, 20): 1.0, (29, 20): 0.0, (31, 20): 0.0, (69, 20): 0.0,
    (71, 20): 1.0, (101, 20): 0.0, (103, 20): 1.0, (133, 20): 0.0, (135, 20): 0.0,
    (5, 63): 0.0, (7, 63): 1.0, (29, 63): 0.0, (31, 63): 1.0, (69, 63): 0.0,
    (71, 63): 1.0, (101, 63): 0.0, (103, 63): 1.0, (133, 63): 0.0, (135, 63): 0.0,
}
"""Texel (x, y) to the value of `SELF_TEST_WIDE_OPS` there, computed in
float64 by `tests/selection_reference.py`."""


def run_mask_test() -> bool | None:
    """Render `SELF_TEST_OPS` and `SELF_TEST_WIDE_OPS` and check their expected texels, as `_run_self_test`."""
    chains = [(f"{width} x {height}",
               lambda specs=specs, width=width, height=height: raster.render(specs, width, height),
               expected)
              for specs, (width, height), expected in (
                  (SELF_TEST_OPS, (SELF_TEST_SIZE, SELF_TEST_SIZE), SELF_TEST_EXPECTED),
                  (SELF_TEST_WIDE_OPS, SELF_TEST_WIDE_SIZE, SELF_TEST_WIDE_EXPECTED))]
    return _run_self_test("selection", chains)


# ── Masks drawn in the 3D view ───────────────────────────────────────

def self_test_scene(perspective: bool) -> dict:
    """The self-test surface and view, in float64.

    The texels form four strips, and each row is one island:

    - a floor tilted by `FLOOR_TILT`
    - an occluder above the middle third of the floor
    - a quad whose smooth normal faces away
    - a margin strip (coverage 0.5) under the floor and the occluder

    `triangles` is the depth soup (the floor, the occluder and the back
    quad). The orthographic view looks down -z, with the scene square
    filling the region. The perspective view is rotated and 1.50 to 2.27
    units from the islands.

    Returns `positions` and `normals` `(32, 32, 4)` with coverage in
    alpha, `triangles` `(n, 3)`, `view`, `projection`, and `labels`, the
    island name of each texel ('' for none).
    """
    n = SELF_TEST_TEXELS
    positions = np.zeros((n, n, 4))
    normals = np.zeros((n, n, 4))
    labels = np.full((n, n), '', dtype=object)
    column = np.arange(n)

    def strip(rows, xs, ys, z, normal_z, alpha, label):
        for index, row in enumerate(rows):
            positions[row, :, 0] = xs
            positions[row, :, 1] = ys[index]
            positions[row, :, 2] = z
            positions[row, :, 3] = alpha
            normals[row, :, 2] = normal_z
            normals[row, :, 3] = alpha
            labels[row, :] = label

    # Orthographic screen x is 2 * column + 1.25, so the bilinear reads mix 3:1.
    rows = range(0, 14)
    xs = (2 * column + 1.25) / 64
    strip(rows, xs, [(2 * row + 1.25) / 64 for row in rows], FLOOR_TILT * xs, 1.0, 1.0, 'floor')
    rows = range(15, 21)
    strip(rows, 1 / 3 + (column + 0.5) / 96, [(row - 15 + 0.5) / 6 * 0.4375 for row in rows], 0.5, 1.0, 1.0,
          'occluder')
    rows = range(22, 28)
    strip(rows, xs, [0.5 + (row - 22 + 0.5) / 6 * 0.25 for row in rows], 0.0, -1.0, 1.0, 'back')
    rows = range(29, 32)
    strip(rows, xs, [0.2 + (row - 29) * 0.01 for row in rows], 0.0, 1.0, 0.5, 'margin')

    def quad(x0, y0, x1, y1, z, tilt=0.0):
        return [(x, y, z + tilt * x) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1))]

    triangles = np.array(quad(0.0, 0.0, 1.0, 0.45, 0.0, FLOOR_TILT) + quad(1 / 3, 0.0, 2 / 3, 0.45, 0.5)
                         + quad(0.0, 0.5, 1.0, 0.75, 0.0))
    if perspective:
        angle, tilt, distance = 0.3, 0.12, 2.0
        near, far = 0.5, 20.0
        spin = np.eye(4)
        spin[:2, :2] = ((math.cos(angle), -math.sin(angle)), (math.sin(angle), math.cos(angle)))
        pitch = np.eye(4)
        pitch[1:3, 1:3] = ((math.cos(tilt), -math.sin(tilt)), (math.sin(tilt), math.cos(tilt)))
        centre = np.eye(4)
        centre[:3, 3] = (-0.5, -0.4, 0.0)
        back = np.eye(4)
        back[2, 3] = -distance
        view = back @ pitch @ spin @ centre
        projection = np.zeros((4, 4))
        projection[0, 0] = projection[1, 1] = 3.2
        projection[2, 2] = (far + near) / (near - far)
        projection[2, 3] = 2.0 * far * near / (near - far)
        projection[3, 2] = -1.0
    else:
        near, far = 1.0, 20.0
        view = np.eye(4)
        view[2, 3] = -10.0
        projection = np.eye(4)
        projection[0, 0] = projection[1, 1] = 2.0
        projection[0, 3] = projection[1, 3] = -1.0
        projection[2, 2] = -2.0 / (far - near)
        projection[2, 3] = -(far + near) / (far - near)
    return dict(positions=positions, normals=normals, triangles=triangles, view=view, projection=projection,
                labels=labels)


def self_test_ops() -> list[tuple]:
    """The self-test chain as (kind, mode, feather, antialias, points in region pixels).

    In order: a box feathered by 8 that runs past the region's top and
    bottom, a subtracted anti-aliased ellipse and an added lasso feathered
    by 3.
    """
    return [
        ('BOX', 'REPLACE', 8.0, True, [(9.7, -30.0), (55.1, 80.0)]),
        ('ELLIPSE', 'SUBTRACT', 0.0, True, [(38.3, 3.1), (50.9, 22.7)]),
        ('LASSO', 'ADD', 3.0, True, [(3.3, 18.7), (29.6, 25.2), (15.9, 58.4)]),
    ]


def self_test_chain(through: bool, perspective: bool, band_rows: int = 16) -> np.ndarray:
    """Render the self-test chain from an empty mask, float32 `(32, 32)`."""
    scene = self_test_scene(perspective)
    surface = view_raster.SyntheticSurface(scene["positions"], scene["normals"], scene["triangles"])
    view = view_raster.ViewSpec(surface, SELF_TEST_REGION, scene["view"], scene["projection"], through)
    specs = [raster.OpSpec(kind, mode, feather, antialias, points, view=view)
             for kind, mode, feather, antialias, points in self_test_ops()]
    saved = core.BAND_ROWS
    core.BAND_ROWS = band_rows
    try:
        return raster.render(specs, SELF_TEST_TEXELS, SELF_TEST_TEXELS)
    finally:
        core.BAND_ROWS = saved
        # A raised error must not keep the textures alive (`raster._run_chain`).
        surface = view = specs = None


SELF_TEST_VIEW_EXPECTED = {
    (False, False, 10, 0): 0.25,
    (False, False, 21, 0): 0.75,
    (False, False, 25, 6): 0.9466509384707502,
    (False, False, 24, 9): 0.32307476618676945,
    (False, False, 25, 17): 0.5319478811371541,
    (False, False, 3, 22): 0.0,
    (False, False, 4, 22): 0.0,
    (False, False, 27, 29): 0.47188818359375023,
    (False, True, 19, 3): 0.7749872597643838,
    (False, True, 19, 8): 0.13065546209896384,
    (False, True, 25, 17): 0.5319478811371541,
    (False, True, 27, 22): 0.47188818359375023,
    (False, True, 27, 29): 0.47188818359375023,
    (True, False, 7, 0): 0.4816929503255505,
    (True, False, 24, 0): 0.6957518555167337,
    (True, False, 7, 8): 0.8943933631298719,
    (True, False, 17, 15): 0.9041868346355607,
    (True, False, 0, 22): 0.0,
    (True, False, 1, 22): 0.0,
    (True, False, 8, 29): 1.0,
    (True, False, 9, 29): 1.0,
    (True, False, 29, 31): 0.4280356519898755,
    (True, True, 24, 0): 0.6957518555167337,
    (True, True, 17, 15): 0.9041868346355607,
    (True, True, 0, 22): 0.32114357833544815,
    (True, True, 1, 22): 0.907121219365551,
    (True, True, 29, 31): 0.4280356519898755,
}
"""The expected `self_test_chain` values, keyed by (perspective, through, x, y).

Each value is the chain's value at texel (x, y), computed in float64 by
`tests/selection_reference.py`. Rows 0 to 13 are the floor, 15 to 20
the occluder, 22 to 27 the back quad and 29 to 31 the margin. Each chain
has at least two texels that change by more than 1e-3 under any of
these bugs:

- a wrong bilinear read, smoothstep, mode, projection flip or distance
  sign
- with Through off, also a wrong facing, margin rule, depth tap, depth
  bias or slope
- in perspective, also `d` used for `-1/d` in either pass, or a missing
  perspective divide
- with Through on, the Through flag ignored
"""


def run_view_test() -> bool | None:
    """Render `self_test_chain` for both scenes, Through off and on, and check it, as `_run_self_test`."""
    chains = []
    for perspective in (False, True):
        for through in (False, True):
            expected = {(x, y): value for (in_perspective, with_through, x, y), value
                        in SELF_TEST_VIEW_EXPECTED.items()
                        if (in_perspective, with_through) == (perspective, through)}
            label = f"the {'perspective' if perspective else 'orthographic'} view, through {through}"
            chains.append((label, lambda through=through, perspective=perspective:
                           self_test_chain(through, perspective), expected))
    return _run_self_test("view selection", chains)
