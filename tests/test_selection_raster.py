"""Selection masks are built on the GPU from the ops (PS-091).

Every mask here is compared with `selection_reference.py`, which evaluates
the same coverage formulas in float64 on the CPU. Soft edges agree within
1e-5 for boxes and lassos, and for ellipses within 1e-4 or 6e-7 of the
longer radius in texels, whichever is larger. The float32 ellipse root is
off by about 3e-7 of that radius in distance, and which texels come
closest to the bound differs between backends. These bounds hold for a
half width of at least 0.25 texels, that is anti-alias on or a feather of
0.5 or more. Below that they grow as 0.25 / half width, because the
coverage slope 1.5 / (2 * half width) amplifies the float32 distance
error. Hard edges agree exactly on every texel further than 1e-3 from the
outline.

These need a GPU context. Blender 5.2 added `gpu.init()`, which builds one
in background mode from EGL without a display, so they run in the ordinary
headless job there. Background 4.2 to 5.1 have no way to get a context and
every GPU test here skips; that coverage comes from the windowed job.
"""
import logging
import math
import os
import sys
import time

import bpy
import gpu
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selection_reference as reference  # noqa: E402
from harness import check, finish, guarded, import_from, register_addon, section, skip  # noqa: E402

register_addon()
core = import_from("gpu_passes.core")
raster = import_from("selection.raster")

TREE = "PS Selection Raster Tree"
SIZE = (1024, 1024)


def tree(name=TREE):
    """A Paint System tree, made once per name."""
    existing = bpy.data.node_groups.get(name)
    if existing is not None:
        return existing
    made = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    made.initialize()
    return made


def fresh_selection(name=TREE):
    got = tree(name).selection
    got.clear()
    return got


def add(selection, op):
    """Append an op dict of the reference's form with `add_op`."""
    values = {key: op[key] for key in ('points', 'feather', 'antialias') if key in op}
    return selection.add_op(op['kind'], op.get('mode', 'REPLACE'), **values)


def spec(op):
    return raster.OpSpec(op['kind'], op.get('mode', 'REPLACE'), op.get('feather', 0.0),
                         op.get('antialias', True), op.get('points', ()))


def worst(got, want):
    return float(np.abs(got.astype(np.float64) - want).max())


def ellipse_tolerance(ops, width, height):
    """1e-4, or 6e-7 of the longest ellipse radius in texels when that is larger.

    1e-5 when no op is an ellipse.
    """
    longest = 0.0
    for op in ops:
        if op['kind'] == 'ELLIPSE':
            (u0, v0), (u1, v1) = op['points']
            longest = max(longest, 0.5 * abs(u1 - u0) * width, 0.5 * abs(v1 - v0) * height)
    return max(1e-4, 6e-7 * longest) if longest else 1e-5


def available():
    if core.gpu_available():
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


def renderer():
    try:
        return f"{gpu.platform.backend_type_get()} {gpu.platform.renderer_get()}"
    except SystemError:
        return "unknown"


def held_textures(error):
    """Locals holding a GPU texture in the frames *error* and its causes keep alive.

    Each as ``function.name``. A texture counts directly or as an item of a
    list, tuple or dict.
    """
    held = []
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        pending += [current.__cause__, current.__context__]
        entry = current.__traceback__
        while entry is not None:
            frame = entry.tb_frame
            for name, value in frame.f_locals.items():
                if isinstance(value, dict):
                    items = value.values()
                elif isinstance(value, (list, tuple)):
                    items = value
                else:
                    items = (value,)
                if any(isinstance(item, gpu.types.GPUTexture) for item in items):
                    held.append(f"{frame.f_code.co_name}.{name}")
            entry = entry.tb_next
    return held


def test_availability():
    section("whether this session can build masks")
    ready = core.gpu_available()
    expected = not bpy.app.background or hasattr(gpu, 'init')
    check(ready == expected,
          f"gpu_available() is {ready}, expected {expected} on "
          f"{bpy.app.version_string} background={bpy.app.background}")
    sel = fresh_selection()
    check(raster.get_mask(sel, size=SIZE) is None and raster.peek_mask(sel, size=SIZE) is None
          and raster.availability(sel, size=SIZE) == "",
          "an empty selection has no mask and nothing to report")
    if ready:
        # CI sets this on the Vulkan job: without an ICD Blender falls back
        # to OpenGL quietly, and the job would pass without Vulkan coverage.
        expected_backend = os.environ.get("PS_EXPECT_GPU_BACKEND")
        if expected_backend:
            check(gpu.platform.backend_type_get() == expected_backend,
                  f"the GPU backend is {gpu.platform.backend_type_get()}, expected {expected_backend}")
        return
    sel.add_op('ALL')
    check(raster.availability(sel, size=SIZE)
          == "This Blender session has no GPU context, so the selection cannot be built",
          f"availability explains it: {raster.availability(sel, size=SIZE)!r}")
    try:
        raster.get_mask(sel, size=SIZE)
        check(False, "get_mask raises NO_GPU")
    except raster.MaskUnavailable as error:
        check(error.reason == 'NO_GPU' and error.op_index == -1, f"get_mask raises NO_GPU ({error.reason})")
    check(raster.peek_mask(sel, size=SIZE) is None, "peek_mask returns None rather than raising")


def test_availability_does_not_probe():
    section("availability answers without starting a GPU context")

    class Probed(Exception):
        pass

    def probe():
        raise Probed()

    sel = fresh_selection()
    sel.add_op('ALL')
    expected = ("" if not bpy.app.background or hasattr(gpu, 'init')
                else "This Blender session has no GPU context, so the selection cannot be built")
    gpu_available = core.gpu_available
    started = core._available
    # As in a background session whose context nobody has asked for yet.
    core._available = None
    core.gpu_available = probe
    try:
        try:
            answer = raster.availability(sel, size=SIZE)
        except Probed:
            answer = None
        check(answer == expected, f"availability answers {answer!r} without calling gpu_available()")
        try:
            raster.get_mask(sel, size=SIZE)
            probed = False
        except Probed:
            probed = True
        except raster.MaskUnavailable:
            probed = False
        check(probed, "get_mask still calls gpu_available()")
    finally:
        core.gpu_available = gpu_available
        core._available = started


def test_self_test():
    section("the self-test chains match the reference")
    if not available():
        return
    check(raster.self_test() is True, f"self_test() passes on {renderer()}")
    chains = (("64x64", raster.SELF_TEST_OPS, (raster.SELF_TEST_SIZE, raster.SELF_TEST_SIZE),
               raster.SELF_TEST_EXPECTED),
              ("wide", raster.SELF_TEST_WIDE_OPS, raster.SELF_TEST_WIDE_SIZE, raster.SELF_TEST_WIDE_EXPECTED))
    for label, specs, (width, height), expected in chains:
        ops = [dict(kind=s.kind, mode=s.mode, feather=s.feather, antialias=s.antialias, points=s.points.tolist())
               for s in specs]
        want = reference.chain(ops, width, height)
        stored = max(abs(want[y, x] - value) for (x, y), value in expected.items())
        check(stored < 1e-12, f"{label}: the stored expected values are the reference's (off by {stored:.1e})")
        got = raster.render(specs, width, height)
        check(worst(got, want) < 1e-5, f"{label}: every texel within 1e-5 (worst {worst(got, want):.2e})")

    outline = import_from("selection.outline")
    comb = raster.SELF_TEST_WIDE_OPS[-1].points * raster.SELF_TEST_WIDE_SIZE
    spans, keys = outline.parity_tables(*outline.closed_outline(comb), *raster.SELF_TEST_WIDE_SIZE)
    check(len(keys) == 2 * outline.DATA_WIDTH and spans.shape[1] == 3 and bool((spans[:, 1:, 1] % 2 == 1).any()),
          f"the wide comb fills two key rows and three span columns and carries parity into a span "
          f"({len(keys) // outline.DATA_WIDTH} rows, {spans.shape[1]} columns)")

    # The same chain with ADD as a saturating sum rather than max must miss
    # an expected value, so a driver that gets max wrong fails.
    summed = np.zeros((raster.SELF_TEST_SIZE, raster.SELF_TEST_SIZE))
    for s in raster.SELF_TEST_OPS:
        if s.kind == 'INVERT':
            summed = 1.0 - summed
            continue
        op = dict(kind=s.kind, feather=s.feather, antialias=s.antialias, points=s.points.tolist())
        covered = reference.coverage(op, raster.SELF_TEST_SIZE, raster.SELF_TEST_SIZE)
        if s.mode == 'REPLACE':
            summed = covered
        elif s.mode == 'ADD':
            summed = np.minimum(summed + covered, 1.0)
        elif s.mode == 'SUBTRACT':
            summed = np.minimum(summed, 1.0 - covered)
        else:
            summed = np.minimum(summed, covered)
    missed = [(x, y) for (x, y), value in raster.SELF_TEST_EXPECTED.items()
              if abs(summed[y, x] - value) > raster.SELF_TEST_TOLERANCE]
    check(bool(missed), f"ADD as a saturating sum misses the expected values at {missed}")


def test_self_test_failure():
    section("a GPU that fails the self-test builds no masks")
    if not available():
        return
    message = "The GPU failed the selection self-test, so selections are disabled in this session"
    tables = (("SELF_TEST_EXPECTED", raster.SELF_TEST_EXPECTED, (63, 0)),
              ("SELF_TEST_WIDE_EXPECTED", raster.SELF_TEST_WIDE_EXPECTED, (7, 1)))
    saved = [dict(table) for _, table, _ in tables]

    def restore():
        for (_, table, _), values in zip(tables, saved):
            table.clear()
            table.update(values)
        raster._self_test_result = None
        raster.invalidate()

    records = []
    handler = logging.Handler()
    handler.emit = records.append
    raster.log.addHandler(handler)
    sel = fresh_selection()
    sel.add_op('ALL')
    try:
        for name, table, (x, y) in tables:
            restore()
            table[x, y] = 0.5
            records.clear()
            try:
                raster.get_mask(sel, size=(64, 64))
                check(False, f"with {name} wrong at ({x}, {y}), get_mask raises SELF_TEST")
            except raster.MaskUnavailable as error:
                check(error.reason == 'SELF_TEST' and error.op_index == -1,
                      f"with {name} wrong at ({x}, {y}), get_mask raises SELF_TEST "
                      f"({error.reason}, {error.op_index})")
            answer = raster.availability(sel, size=(64, 64))
            check(raster.self_test() is False and answer == message, f"and availability reports it: {answer!r}")
            check(any(record.levelno == logging.ERROR and f"({x}, {y}) of" in record.getMessage()
                      for record in records), "and the error log names the texel")
    finally:
        raster.log.removeHandler(handler)
        restore()
    check(raster.get_mask(sel, size=(64, 64)) is not None and raster.self_test() is True,
          "with the expected values restored, get_mask builds again")


def test_feathered_box():
    section("a feathered box rises from 0 to 1 over the feather width")
    if not available():
        return
    sel = fresh_selection()
    sel.add_op('BOX', points=[(0.25, 0.25), (0.75, 0.5)], feather=16.0)
    row = raster.get_mask(sel, size=(256, 256)).read()[96]
    want = {55: 0.0, 56: 0.00286865234375, 57: 0.02471923828125, 63: 0.45318603515625,
            64: 0.54681396484375, 71: 0.99713134765625, 72: 1.0}
    got = {x: float(row[x]) for x in want}
    check(got == want, f"row 96 is exact {got}")
    check(bool(np.all(np.diff(row[55:73]) > 0.0)), "and strictly rising from column 55 to 72")


def test_read_back_and_state():
    section("reads back as floats and bytes, and leaves gpu.state as it was")
    if not available():
        return
    sel = fresh_selection()
    sel.add_op('BOX', points=[(0.05, 0.05), (0.97, 0.9)], feather=40.0)
    sel.add_op('LASSO', 'SUBTRACT', points=reference.star(120, 0.55, 0.55, 0.42, 0.25, 5), feather=24.0)
    before = (gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get())
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(True)
    # Neither has a getter, so the passes force them rather than restore them.
    gpu.state.face_culling_set('FRONT')
    gpu.state.color_mask_set(False, False, False, False)
    try:
        raster.invalidate()
        mask = raster.get_mask(sel, size=(2048, 2000))
        values = mask.read()
        quantised = mask.read_bytes()
        after = (gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get())
    finally:
        gpu.state.blend_set(before[0])
        gpu.state.depth_test_set(before[1])
        gpu.state.depth_mask_set(before[2])
        gpu.state.face_culling_set('NONE')
        gpu.state.color_mask_set(True, True, True, True)
    check(after == ('ALPHA', 'LESS_EQUAL', True), f"blend, depth test and depth write restored {after}")
    rendered = raster.render([raster.OpSpec.from_op(op) for op in sel.ops], 2048, 2000)
    check(np.array_equal(values, rendered), "front-face culling and a closed colour mask do not change the mask")
    check(values.dtype == np.float32 and values.shape == (2000, 2048), f"read() is float32 {values.shape}")
    check(quantised.dtype == np.uint8 and quantised.shape == (2000, 2048), f"read_bytes() is uint8 {quantised.shape}")
    # The GPU rounds in float32, so a texel within float32 error of a half
    # step may land on either side of it.
    scaled = values.astype(np.float64) * 255.0 + 0.5
    boundary = np.abs(scaled - np.round(scaled)) < 1e-4
    off = quantised.astype(np.int64) - np.floor(scaled).astype(np.int64)
    check(int(np.abs(off).max()) <= 1 and not np.any(off[~boundary]),
          f"read_bytes() is floor(read() * 255 + 0.5), off by one on {int(np.count_nonzero(off))} texels "
          f"all within 1e-4 of a half step ({int(boundary.sum())} such texels)")


def test_set_operations():
    section("modes combine as the soft set operations")
    if not available():
        return
    width, height = 128, 140
    cases = {
        'REPLACE': [dict(kind='ELLIPSE', points=[(0.1, 0.2), (0.8, 0.7)], feather=6.0)],
        'ADD': [dict(kind='BOX', points=[(0.1, 0.1), (0.5, 0.5)], feather=4.0),
                dict(kind='ELLIPSE', mode='ADD', points=[(0.3, 0.3), (0.9, 0.8)], feather=10.0)],
        'SUBTRACT': [dict(kind='ALL'),
                     dict(kind='LASSO', mode='SUBTRACT', points=reference.pentagram(0.5, 0.5, 0.4), feather=3.0)],
        'INTERSECT': [dict(kind='BOX', points=[(0.05, 0.05), (0.7, 0.9)], feather=12.0),
                      dict(kind='LASSO', mode='INTERSECT', points=reference.star(90, 0.5, 0.5, 0.45, 0.2, 5),
                           feather=5.0)],
        'INVERT': [dict(kind='ELLIPSE', points=[(0.2, 0.2), (0.6, 0.9)], feather=2.0), dict(kind='INVERT')],
        'leading ADD': [dict(kind='BOX', mode='ADD', points=[(0.2, 0.2), (0.6, 0.6)])],
        'leading INVERT': [dict(kind='INVERT'), dict(kind='BOX', mode='SUBTRACT', points=[(0.2, 0.2), (0.6, 0.6)])],
    }
    for name, ops in cases.items():
        sel = fresh_selection()
        for op in ops:
            add(sel, op)
        got = raster.get_mask(sel, size=(width, height)).read()
        tolerance = ellipse_tolerance(ops, width, height)
        error = worst(got, reference.chain(ops, width, height))
        check(error <= tolerance, f"{name}: within {tolerance:g} (worst {error:.2e})")

    box = dict(kind='BOX', points=[(0.2, 0.25), (0.6, 0.7)], feather=4.0)
    sel = fresh_selection()
    add(sel, box)
    raw = sel.ops.add()
    raw.kind = 'INVERT'
    error = worst(raster.get_mask(sel, size=(width, height)).read(),
                  reference.chain([box, dict(kind='INVERT')], width, height))
    check(raw.mode == 'REPLACE' and error <= 1e-5,
          f"an INVERT added past add_op keeps mode REPLACE and still inverts (worst {error:.2e})")

    raster.invalidate()
    sel = fresh_selection()
    written = sel.ops.add()
    written.kind = 'LASSO'
    written['points'] = [0, 0, 1, 0, 0, 1]
    error = worst(raster.get_mask(sel, size=(64, 64)).read(),
                  reference.chain([dict(kind='LASSO', points=[(0, 0), (1, 0), (0, 1)])], 64, 64))
    check(written['points'].typecode == 'i' and error <= 1e-5,
          f"points written as integers build like floats (worst {error:.2e})")


def test_hard_lasso_is_exact():
    section("a hard lasso fills exactly the even-odd interior")
    if not available():
        return
    width, height = 509, 263
    for name, points in (("pentagram", reference.pentagram(0.5, 0.5, 0.45)),
                         ("double loop", reference.double_loop(0.5, 0.5, 0.4, 0.25)),
                         ("star of 300 points", reference.star(300, 0.5, 0.5, 0.45, 0.2, 9, seed=3))):
        op = dict(kind='LASSO', points=points, antialias=False)
        got = raster.render([spec(op)], width, height)
        signed, inside = reference.outline_distance(op, width, height)
        clear = np.abs(signed) > 1e-3
        mismatches = int((got[clear] != inside[clear].astype(np.float32)).sum())
        check(mismatches == 0 and bool(np.isin(got, (0.0, 1.0)).all()),
              f"{name}: {mismatches} mismatches, {int((~clear).sum())} texels on the outline not compared")


def test_precision():
    section("float32 passes stay within tolerance of float64")
    if not available():
        return
    pentagram = reference.pentagram(0.5, 0.5, 0.45)
    star = reference.star(200, 0.5, 0.5, 0.45, 0.2, 7)
    suite = {
        "box anti-aliased": [dict(kind='BOX', points=[(0.1, 0.2), (0.7, 0.55)])],
        "box hard": [dict(kind='BOX', points=[(0.1, 0.2), (0.7, 0.55)], antialias=False)],
        "box feather 12": [dict(kind='BOX', points=[(0.7, 0.55), (0.1, 0.2)], feather=12.0)],
        "tiny box feather 64": [dict(kind='BOX', points=[(0.49, 0.49), (0.51, 0.52)], feather=64.0)],
        "ellipse anti-aliased": [dict(kind='ELLIPSE', points=[(0.2, 0.3), (0.9, 0.8)])],
        "ellipse hard": [dict(kind='ELLIPSE', points=[(0.2, 0.3), (0.9, 0.8)], antialias=False)],
        "ellipse feather 20": [dict(kind='ELLIPSE', points=[(0.2, 0.3), (0.9, 0.8)], feather=20.0)],
        "needle ellipse feather 64": [dict(kind='ELLIPSE', points=[(0.05, 0.4990), (0.95, 0.5010)], feather=64.0)],
        "centred circle feather 3": [dict(kind='ELLIPSE', points=[(0.5 - 60 / 256, 0.5 - 60 / 256),
                                                                 (0.5 + 60 / 256 + 1 / 512, 0.5 + 60 / 256 + 1 / 512)],
                                          feather=3.0)],
        "pentagram anti-aliased": [dict(kind='LASSO', points=pentagram)],
        "pentagram hard": [dict(kind='LASSO', points=pentagram, antialias=False)],
        "double loop feather 6": [dict(kind='LASSO', points=reference.double_loop(0.5, 0.5, 0.4, 0.2), feather=6.0)],
        "star feather 8": [dict(kind='LASSO', points=star, feather=8.0)],
        "lasso off the image feather 10": [dict(kind='LASSO', points=[(-0.2, -0.1), (0.6, 0.3), (1.3, 1.2), (0.2, 0.9)],
                                                feather=10.0)],
        # A texel near a cell corner whose nearest edge is not the one
        # nearest the cell centre: the list must reach past that one.
        "strip past a cell corner feather 8": [dict(kind='LASSO', feather=8.0, points=[
            (x / 256, y / 256) for x, y in ((0, 32), (32, 0), (65.9, 0), (0, 65.9))])],
        # Distance cells of 64 and 128 texels, up to the widest feather.
        "star feather 40": [dict(kind='LASSO', points=star, feather=40.0)],
        "pentagram feather 128.2": [dict(kind='LASSO', points=pentagram, feather=128.2)],
        "star feather 1024": [dict(kind='LASSO', points=star, feather=1024.0)],
    }
    for name, ops in suite.items():
        got = raster.render([spec(op) for op in ops], 256, 256)
        op = ops[-1]
        if reference.half_width(op.get('feather', 0.0), op.get('antialias', True)) == 0.0:
            signed, inside = reference.outline_distance(op, 256, 256)
            clear = np.abs(signed) > 1e-3
            mismatches = int((got[clear] != inside[clear].astype(np.float32)).sum())
            check(mismatches == 0, f"256 {name}: {mismatches} mismatches")
        else:
            tolerance = ellipse_tolerance(ops, 256, 256)
            error = worst(got, reference.chain(ops, 256, 256))
            check(error <= tolerance, f"256 {name}: within {tolerance:g} (worst {error:.2e})")
    error = 0.0
    for seed in range(40):
        rng = np.random.default_rng(seed)
        op = dict(kind='LASSO', points=rng.uniform(-0.1, 1.1, size=(int(rng.integers(3, 13)), 2)).tolist(),
                  feather=float(rng.choice([2.0, 8.0, 16.0])))
        error = max(error, worst(raster.render([spec(op)], 96, 96), reference.chain([op], 96, 96)))
    check(error <= 1e-5, f"96x96 random lassos of 3 to 12 points, 40 seeds: within 1e-5 (worst {error:.2e})")
    # Boxes are clamped to the target plus the soft edge before upload.
    for size, op in (((96, 48), dict(kind='BOX', points=[(-0.2, -0.3), (1.3, 0.6)], feather=16.0)),
                     ((48, 96), dict(kind='BOX', points=[(-0.3, -0.2), (0.6, 1.3)], feather=16.0))):
        error = worst(raster.render([spec(op)], *size), reference.chain([op], *size))
        check(error <= 1e-5, f"{size[0]}x{size[1]} box overhanging three edges, feather 16: within 1e-5 "
                             f"(worst {error:.2e})")

    def windows(ops, width, height, centres, label):
        got = raster.render([spec(op) for op in ops], width, height)
        tolerance = ellipse_tolerance(ops, width, height)
        error = 0.0
        for cx, cy in centres:
            x0 = int(min(max(0, cx - 24), width - 48))
            y0 = int(min(max(0, cy - 24), height - 48))
            window = (x0, y0, x0 + 48, y0 + 48)
            want = reference.chain(ops, width, height, window=window)
            error = max(error, worst(got[y0:y0 + 48, x0:x0 + 48], want))
        check(error <= tolerance, f"{label}: within {tolerance:.2g} (worst {error:.2e})")

    tall = [dict(kind='ELLIPSE', points=[(0.1, 0.05), (0.9, 0.95)])]
    windows(tall, 148, 5200, [(72, 284), (72, 4916), (130, 2600)], "148x5200 ellipse anti-aliased")
    size = 4096

    def ellipse(cx, cy, a, b, feather=0.0):
        return [dict(kind='ELLIPSE', points=[((cx - a) / size, (cy - b) / size), ((cx + a) / size, (cy + b) / size)],
                     feather=feather)]

    windows(ellipse(2048.37, 2047.81, 1500.6, 900.2), size, size,
            [(3546, 2048), (600, 2048), (2048 + 1050, 2048 + 630), (2048, 2948)], "4K ellipse 1500x900 anti-aliased")
    windows(ellipse(2048.37, 2047.50003, 2000.0, 0.35), size, size,
            [(4045, 2048), (4028, 2048), (53, 2048)], "4K needle ellipse 2000x0.35 anti-aliased")
    windows(ellipse(2048.3, -27000.0, 30000.0, 30000.0), size, size,
            [(2048, 3000), (500, 2950), (3600, 2950)], "4K arc of radius 30000 anti-aliased")
    for tiles in (2.0, 10.0):
        box = [dict(kind='BOX', points=[(-tiles - 0.3, -tiles + 0.3), (0.50013, tiles + 0.9)], feather=4.0)]
        windows(box, size, size, [(2048, 1000), (2048, 3000)], f"4K box reaching {tiles:g} tiles outside, feather 4")
        corner = np.array((0.61234, 0.4321))
        triangle = [(-tiles, -tiles + 0.1), tuple(corner), (-tiles + 0.2, tiles + 1.0)]
        centres = []
        for other in (triangle[0], triangle[2]):
            point = (corner + 0.25 / (tiles + 1.0) * (np.array(other) - corner)) * size
            centres.append((int(point[0]), int(point[1])))
        windows([dict(kind='LASSO', points=triangle, feather=4.0)], size, size, centres,
                f"4K lasso reaching {tiles:g} tiles outside, feather 4")
    quad = [(0.0123457, 0.0234567), (0.9876543, 0.1111111), (0.9345679, 0.9765432), (0.0432099, 0.8888889)]
    centres = []
    for index in range(4):
        (x0, y0), (x1, y1) = quad[index], quad[(index + 1) % 4]
        for share in (0.3, 0.5):
            centres.append((int((x0 + share * (x1 - x0)) * size), int((y0 + share * (y1 - y0)) * size)))
    windows([dict(kind='LASSO', points=quad, feather=4.0)], size, size, centres,
            "4K quadrilateral of 4000-texel edges, feather 4")
    windows([dict(kind='BOX', points=[quad[0], quad[2]], feather=4.0)], size, size,
            [(2048, int(0.0234567 * size)), (int(0.9345679 * size), 2048)], "4K box, feather 4")

    # Every texel of an inscribed circle's anti-aliased edge, not a window:
    # the float32 root misses by most on a few scattered texels.
    op = dict(kind='ELLIPSE', points=[(0.37 / size, 0.61 / size), ((size - 0.29) / size, (size - 0.53) / size)])
    got = raster.render([spec(op)], size, size)
    corners = np.array(op['points']) * size
    centre = 0.5 * (corners[0] + corners[1])
    radii = 0.5 * (corners[1] - corners[0])
    half = reference.half_width(0.0, True)
    columns = np.arange(size) + 0.5
    xs, ys = [], []
    for first in range(0, size, 512):
        rows = np.arange(first, first + 512) + 0.5
        rho = np.hypot((columns[None, :] - centre[0]) / radii[0], (rows[:, None] - centre[1]) / radii[1])
        y, x = np.nonzero(np.abs(rho - 1.0) * radii.min() < half + 1e-3)
        xs.append(x)
        ys.append(y + first)
    xs, ys = np.concatenate(xs), np.concatenate(ys)
    want = reference.profile(reference.ellipse_signed_distance(xs + 0.5, ys + 0.5, centre, radii), half)
    tolerance = ellipse_tolerance([op], size, size)
    error = worst(got[ys, xs], want)
    check(error <= tolerance, f"4K inscribed circle anti-aliased, all {len(xs)} edge texels: within {tolerance:.2g} "
                              f"(worst {error:.2e})")


def test_degenerate_shapes():
    section("shapes that enclose nothing cover nothing")
    if not available():
        return
    cases = (
        ("zero-width box intersected",
         [raster.OpSpec('ALL'), raster.OpSpec('BOX', 'INTERSECT', points=[(0.3, 0.3), (0.3, 0.8)])], 0.0),
        ("flat ellipse", [raster.OpSpec('ELLIPSE', points=[(0.2, 0.5), (0.8, 0.5)], feather=8.0)], 0.0),
        ("ellipse thinner than MIN_RADIUS",
         [raster.OpSpec('ELLIPSE', points=[(0.2, 0.5), (0.8, 0.5 + 1e-3 / 64)], feather=8.0)], 0.0),
        ("box with one corner", [raster.OpSpec('BOX', points=[(0.5, 0.5)])], 0.0),
        ("lasso with two distinct points subtracted",
         [raster.OpSpec('ALL'), raster.OpSpec('LASSO', 'SUBTRACT', points=[(0.1, 0.1), (0.9, 0.9), (0.9, 0.9)])], 1.0),
        ("lasso with a NaN point subtracted",
         [raster.OpSpec('ALL'),
          raster.OpSpec('LASSO', 'SUBTRACT', points=[(math.nan, 0.1), (0.9, 0.1), (0.5, 0.9)])], 1.0),
    )
    for name, specs, value in cases:
        got = raster.render(specs, 64, 64)
        check(bool((got == value).all()), f"{name}: every texel is {value}")
    op = dict(kind='LASSO', points=reference.pentagram(0.5, 0.5, 0.45), feather=math.nan)
    error = worst(raster.render([spec(op)], 64, 64), reference.chain([op], 64, 64))
    check(error <= 1e-5, f"a NaN feather draws as no feather: within 1e-5 (worst {error:.2e})")
    wide = raster.render([raster.OpSpec('ELLIPSE', points=[(0.2, 0.2), (0.8, 0.8)], feather=5000.0)], 256, 256)
    clamped = raster.render([raster.OpSpec('ELLIPSE', points=[(0.2, 0.2), (0.8, 0.8)], feather=1024.0)], 256, 256)
    check(np.array_equal(wide, clamped), "a feather over 1024 draws as 1024")


def test_bands():
    section("tall targets are drawn in bands without seams")
    if not available():
        return
    got = raster.render([raster.OpSpec('ALL')], 37, 1300)
    check(got.shape == (1300, 37) and bool((got == 1.0).all()), "37x1300 ALL is one on every texel")
    op = dict(kind='LASSO', points=reference.star(90, 0.5, 0.5, 0.48, 0.3, 5), feather=6.0)
    error = worst(raster.render([spec(op)], 61, 1100), reference.chain([op], 61, 1100))
    check(error <= 1e-5, f"a feathered lasso across three bands is within 1e-5 (worst {error:.2e})")


def test_udim_tiles():
    section("a UDIM tile maps its own UV square onto the mask")
    if not available():
        return
    sel = fresh_selection()
    sel.add_op('BOX', points=[(1.25, 2.25), (1.75, 2.75)])
    mask = raster.get_mask(sel, size=(64, 64), tile=1022)
    check(mask.tile == 1022 and mask.key == sel.prefix_digests(64, 64, 1022)[-1],
          "the mask records its tile and its key is the tile's last prefix digest")
    got = mask.read()
    check(got[32, 32] == 1.0 and got[5, 5] == 0.0 and float(got.sum()) == 32 * 32,
          f"tile 1022 holds the box shifted by (-1, -2), sum {float(got.sum())}")
    check(raster.peek_mask(sel, size=(64, 64), tile=1001) is None, "another tile is another mask")

    image = bpy.data.images.new("PS Selection Tiles", 64, 32, tiled=True)
    image.tiles.new(tile_number=1002)
    sizes = [raster.image_size(image, tile=number) for number in (1001, 1002, 1005)]
    check(sizes == [(64, 32), (0, 0), (0, 0)],
          f"the size is the tile's, and (0, 0) for an empty or missing tile {sizes}")
    check(raster.availability(sel, raster.image_size(image, 1002), tile=1002)
          == "The selection has no image with pixels to take its size from",
          "an empty tile reports NO_SIZE")
    bpy.data.images.remove(image)


def test_cache():
    section("the cache runs only the passes that changed")
    if not available():
        return
    raster.invalidate()
    sel = fresh_selection()
    sel.add_op('BOX', points=[(0.1, 0.1), (0.6, 0.7)], feather=24.0)
    sel.add_op('LASSO', 'ADD', points=reference.star(200, 0.55, 0.5, 0.4, 0.2, 7), feather=64.0)
    sel.add_op('ELLIPSE', 'SUBTRACT', points=[(0.3, 0.3), (0.5, 0.6)], feather=12.0)
    raster.reset_stats()
    first = raster.get_mask(sel, size=SIZE)
    stats = raster.stats()
    check(stats["passes"] == 3 and stats["cached"] == 2, f"three ops from scratch: 3 passes, 2 masks cached {stats}")
    check(first.key == sel.prefix_digests(*SIZE, 1001)[-1] and first.tile == 1001,
          "the mask's key is the last prefix digest")

    sel.add_op('INVERT')
    raster.get_mask(sel, size=SIZE)
    check(raster.stats()["passes"] == 4, f"appending INVERT runs one pass {raster.stats()}")
    raster.get_mask(sel, size=SIZE)
    check(raster.stats()["passes"] == 4 and raster.stats()["hits"] == 1, f"asking again is a hit {raster.stats()}")

    sel.ops.remove(len(sel.ops) - 1)
    check(raster.get_mask(sel, size=SIZE) is first and raster.stats()["passes"] == 4,
          "removing the last op gives back the mask before it without a pass")

    inverted = sel.add_op('INVERT', 'REPLACE')
    check(len(sel.ops) == 4 and inverted.mode == 'ADD', "INVERT with REPLACE keeps the ops and is stored as ADD")
    held = raster.get_mask(sel, size=SIZE)
    check(raster.stats()["passes"] == 4, f"and is the same mask as INVERT with ADD {raster.stats()}")

    other = fresh_selection("PS Selection Raster Other")
    for op in sel.ops:
        added = other.add_op(op.kind, op.mode, feather=op.feather, antialias=op.antialias)
        if op.get("points") is not None:
            added.set_points(op.get_points())
    check(raster.get_mask(other, size=SIZE) is held and raster.stats()["passes"] == 4,
          "another tree with the same ops shares the mask")
    other.clear()

    sel.add_op('LASSO', 'INTERSECT', points=reference.star(300, 0.5, 0.5, 0.45, 0.3, 11, seed=2))
    incremental = raster.get_mask(sel, size=SIZE).read()
    check(raster.stats()["passes"] == 5, f"one more op, one more pass {raster.stats()}")

    raster.invalidate()
    check(not held.alive, "invalidate() kills masks callers still hold")
    try:
        held.texture
        check(False, "a dead mask's texture raises ReferenceError")
    except ReferenceError:
        check(True, "a dead mask's texture raises ReferenceError")
    raster.reset_stats()
    scratch = raster.get_mask(sel, size=SIZE).read()
    check(np.array_equal(incremental, scratch) and raster.stats()["passes"] == 5,
          f"a rebuild from scratch is bit-identical to the incremental build ({raster.stats()['passes']} passes)")

    raster.reset_stats()
    sel.ops[2].feather = 12.5
    raster.get_mask(sel, size=SIZE)
    check(raster.stats()["passes"] == 5, f"editing op 2 of 5 rebuilds from the start {raster.stats()}")

    raster.reset_stats()
    replace = sel.ops.add()
    replace.kind = 'BOX'
    replace.set_points([(0.0, 0.0), (0.5, 0.5)])
    check(sel.chain_start() == 5, f"an op added past add_op still starts the chain ({sel.chain_start()})")
    raster.get_mask(sel, size=SIZE)
    check(raster.stats()["passes"] == 1, f"so a REPLACE after five ops runs one pass {raster.stats()}")

    raster.invalidate()
    sel = fresh_selection()
    ops = [dict(kind='BOX', points=[(0.12, 0.13), (0.61, 0.72)], feather=6.0),
           dict(kind='ELLIPSE', mode='ADD', points=[(0.31, 0.27), (0.93, 0.81)], feather=10.0),
           dict(kind='LASSO', mode='SUBTRACT', points=reference.pentagram(0.4, 0.4, 0.3), feather=3.0)]
    for op in ops:
        add(sel, op)
    raster.reset_stats()
    raster.get_mask(sel, size=(256, 200))
    sel.ops.remove(2)
    got = raster.get_mask(sel, size=(256, 200)).read()
    error = worst(got, reference.chain(ops[:2], 256, 200))
    tolerance = ellipse_tolerance(ops[:2], 256, 200)
    check(raster.stats()["passes"] == 3 and error <= tolerance,
          f"removing the last op after a 3-pass build returns the cached mask before it, "
          f"within {tolerance:g} (worst {error:.2e})")


def test_cache_budget():
    section("the cache stays inside its video memory budget")
    if not available():
        return
    raster.invalidate()
    raster.reset_stats()
    budget = raster.CACHE_BUDGET
    raster.CACHE_BUDGET = 3 * SIZE[0] * SIZE[1] * 4
    try:
        sel = fresh_selection()
        op = sel.add_op('BOX', points=[(0.1, 0.1), (0.2, 0.2)])
        masks = []
        for corner in (0.2, 0.3, 0.4):
            op.set_points([(0.1, 0.1), (corner, corner)])
            masks.append(raster.get_mask(sel, size=SIZE))
        stats = raster.stats()
        check(stats["cached"] == 3 and stats["evictions"] == 0, f"three masks fit a budget of three {stats}")
        allocations = stats["allocations"]
        op.set_points([(0.1, 0.1), (0.2, 0.2)])
        check(raster.get_mask(sel, size=SIZE) is masks[0], "a hit returns the cached mask")
        op.set_points([(0.1, 0.1), (0.5, 0.5)])
        newest = raster.get_mask(sel, size=SIZE)
        stats = raster.stats()
        check(stats["evictions"] == 1 and stats["allocations"] == allocations
              and masks[0].alive and not masks[1].alive and masks[2].alive,
              f"a fourth evicts the least recently used, not the oldest, and reuses its texture {stats}")
        check(float(newest.read()[300, 300]) == 1.0, "the newest mask reads back")
        try:
            masks[1].read()
            check(False, "reading an evicted mask raises ReferenceError")
        except ReferenceError:
            check(True, "reading an evicted mask raises ReferenceError")

        # A build that resumes from a cached mask while the budget is full.
        raster.invalidate()
        raster.CACHE_BUDGET = 2 * 256 * 256 * 4
        a_ops = [dict(kind='BOX', points=[(0.15, 0.1), (0.65, 0.6)], feather=8.0),
                 dict(kind='LASSO', mode='SUBTRACT', points=reference.pentagram(0.4, 0.35, 0.25), feather=5.0)]
        sel_a = fresh_selection()
        add(sel_a, a_ops[0])
        raster.get_mask(sel_a, size=(256, 256))
        sel_b = fresh_selection("PS Selection Raster Other")
        sel_b.add_op('ELLIPSE', points=[(0.4, 0.4), (0.9, 0.9)])
        held_b = raster.get_mask(sel_b, size=(256, 256))
        add(sel_a, a_ops[1])
        raster.reset_stats()
        got = raster.get_mask(sel_a, size=(256, 256)).read()
        error = worst(got, reference.chain(a_ops, 256, 256))
        check(raster.stats()["passes"] == 1 and not held_b.alive and error <= 1e-5
              and raster.stats()["allocations"] == 0 and raster.stats()["pooled"] == 0,
              f"appending evicts the other mask, keeps the one it resumes from and is exact "
              f"(worst {error:.2e}) {raster.stats()}")
        sel_a.ops.remove(1)
        got = raster.get_mask(sel_a, size=(256, 256)).read()
        error = worst(got, reference.chain(a_ops[:1], 256, 256))
        check(raster.stats()["passes"] == 1 and error <= 1e-5,
              f"and the mask it resumed from still holds its own content (worst {error:.2e})")
        sel_b.clear()
    finally:
        raster.CACHE_BUDGET = budget
        raster.invalidate()


def test_problems():
    section("a mask that cannot be built says why")
    if not available():
        return
    raster.invalidate()
    sel = fresh_selection()
    faces = sel.ops.add()
    faces.kind = 'FACES'
    sel.add_op('BOX', 'ADD', points=[(0.1, 0.1), (0.4, 0.4)])
    sel.ops[1].mode = 'REPLACE'
    check(raster.availability(sel, size=SIZE) == "" and raster.get_mask(sel, size=SIZE) is not None,
          "an unsupported op before the chain start is never read")

    sel.add_op('FACES', 'SUBTRACT')
    message = "Selections with a faces operation cannot be built yet"
    check(raster.availability(sel, size=SIZE) == message,
          f"availability names it: {raster.availability(sel, size=SIZE)!r}")
    check(raster.peek_mask(sel, size=SIZE) is None, "peek_mask returns None rather than raising")
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    raster.log.addHandler(handler)
    try:
        for _ in range(2):
            try:
                raster.get_mask(sel, size=SIZE)
                check(False, "get_mask raises UNSUPPORTED")
            except raster.MaskUnavailable as error:
                check(error.reason == 'UNSUPPORTED' and error.op_index == 2 and str(error) == message,
                      f"get_mask raises UNSUPPORTED at op 2 ({error.reason}, {error.op_index})")
    finally:
        raster.log.removeHandler(handler)
    check(len([record for record in records if record.levelno == logging.WARNING]) == 1,
          f"and logs one warning for two calls ({len(records)} records)")

    sel = fresh_selection()
    sel.add_op('ALL', space='VIEW')
    sel.add_op('BOX', 'SUBTRACT', space='VIEW', points=[(0.1, 0.1), (0.4, 0.4)])
    try:
        raster.get_mask(sel, size=SIZE)
        check(False, "a VIEW box raises UNSUPPORTED")
    except raster.MaskUnavailable as error:
        check(error.reason == 'UNSUPPORTED' and error.op_index == 1
              and str(error) == "Selections drawn in the 3D view cannot be built yet",
              f"a VIEW box raises UNSUPPORTED at op 1 and a VIEW ALL does not ({error.op_index})")

    message = "A selection operation has malformed points"
    for label, points in (("a list of pairs", [(0.1, 0.1), (0.5, 0.2), (0.3, 0.6)]),
                          ("an odd length", [0.1, 0.1, 0.9, 0.2, 0.5, 0.9, 0.5])):
        sel = fresh_selection()
        sel.add_op('ALL')
        written = sel.ops.add()
        written.kind = 'LASSO'
        written.mode = 'SUBTRACT'
        written['points'] = points
        check(raster.peek_mask(sel, size=SIZE) is None, f"{label}: peek_mask returns None rather than raising")
        answer = raster.availability(sel, size=SIZE)
        check(answer == message, f"{label}: availability names it: {answer!r}")
        try:
            raster.get_mask(sel, size=SIZE)
            check(False, f"{label}: get_mask raises UNSUPPORTED")
        except raster.MaskUnavailable as error:
            check(error.reason == 'UNSUPPORTED' and error.op_index == 1 and str(error) == message,
                  f"{label}: get_mask raises UNSUPPORTED at op 1 ({error.reason}, {error.op_index})")

    sel = fresh_selection()
    sel.add_op('ALL')
    for label, size, reason in (("0x0", (0, 0), 'NO_SIZE'),
                                ("9000x9000", (9000, 9000), 'TOO_LARGE')):
        try:
            raster.get_mask(sel, size=size)
            check(False, f"{label} raises {reason}")
        except raster.MaskUnavailable as error:
            check(error.reason == reason and str(error) == raster.MESSAGES[reason],
                  f"{label} raises {reason} with the public message ({error.reason})")
    missing = bpy.data.images.new("PS Selection Missing", 4, 4)
    missing.source = 'FILE'
    missing.filepath = "//no_such_selection_image.png"
    check(raster.image_size(missing) == (0, 0) and raster.availability(sel, raster.image_size(missing))
          == "The selection has no image with pixels to take its size from",
          f"an image whose file is missing reports NO_SIZE {tuple(missing.size)}")
    generated = bpy.data.images.new("PS Selection Generated", 300, 200)
    check(raster.image_size(generated) == (300, 200)
          and raster.get_mask(sel, raster.image_size(generated)).size == (300, 200),
          "a mask built at the image's size has that size")
    bpy.data.images.remove(missing)
    bpy.data.images.remove(generated)


def test_outline_limits():
    section("an outline past the table limits raises TOO_COMPLEX")
    if not available():
        return
    outline = import_from("selection.outline")
    star = reference.star(400, 0.5, 0.5, 0.45, 0.2, 9)
    saved = outline.MAX_KEYS

    def too_complex(build):
        """What *build* raises with the key limit lowered, as (reason, op index, textures its traceback holds)."""
        outline.MAX_KEYS = 1000
        try:
            build()
        except raster.MaskUnavailable as error:
            return error.reason, error.op_index, held_textures(error)
        finally:
            outline.MAX_KEYS = saved
        return None, None, []

    raster.invalidate()
    sel = fresh_selection()
    sel.add_op('ALL')
    sel.add_op('LASSO', 'SUBTRACT', points=star)
    reason, index, held = too_complex(lambda: raster.get_mask(sel, size=SIZE))
    check(reason == 'TOO_COMPLEX' and index == 1, f"get_mask raises TOO_COMPLEX at op 1 ({reason}, {index})")
    check(not held, f"and nothing its traceback keeps holds a texture {held}")
    raster.reset_stats()
    check(raster.get_mask(sel, size=SIZE) is not None and raster.stats()["allocations"] == 0,
          f"the failed build's textures are reused {raster.stats()}")
    sel.ops.remove(1)
    check(bool((raster.get_mask(sel, size=SIZE).read() == 1.0).all()) and raster.stats()["passes"] == 2,
          f"and the rebuild's mask before the last op is the ALL mask {raster.stats()}")

    specs = [raster.OpSpec('ALL'), raster.OpSpec('LASSO', 'SUBTRACT', points=star)]
    reason, index, held = too_complex(lambda: raster.render(specs, *SIZE))
    check(reason == 'TOO_COMPLEX' and index == 1 and not held,
          f"render raises TOO_COMPLEX at spec 1 and its traceback holds no texture ({reason}, {index}) {held}")

    raster.invalidate()
    sel = fresh_selection()
    sel.add_op('ALL')
    sel.add_op('BOX', 'ADD', points=[(0.1, 0.1), (0.4, 0.4)])
    raster.get_mask(sel, size=SIZE)
    sel.add_op('LASSO', 'SUBTRACT', points=star)
    reason, index, held = too_complex(lambda: raster.get_mask(sel, size=SIZE))
    check(reason == 'TOO_COMPLEX' and index == 2,
          f"a build resumed from a cached prefix reports the op's index in selection.ops ({reason}, {index})")
    check(not held, f"and its traceback holds neither the mask it resumed from nor a target {held}")


def test_gpu_errors():
    section("a GPU that raises during a build gives GPU_ERROR, and the next build tries again")
    if not available():
        return
    message = "The GPU could not build the selection right now; try again"
    size = (128, 128)

    def no_context(*args):
        raise RuntimeError("No active GPU context found")

    def failure(build):
        """(reason, op index, message, textures its traceback holds) of what *build* raises."""
        try:
            build()
        except raster.MaskUnavailable as error:
            return error.reason, error.op_index, str(error), held_textures(error)
        return None, None, None, []

    ops = [dict(kind='BOX', points=[(0.1, 0.2), (0.6, 0.7)], feather=6.0),
           dict(kind='LASSO', mode='SUBTRACT', points=reference.pentagram(0.4, 0.45, 0.3), feather=4.0)]
    sel = fresh_selection()
    for op in ops:
        add(sel, op)

    raster.invalidate()
    raster._self_test_result = None
    render = raster.render
    raster.render = no_context
    try:
        passed = raster.self_test()
        remembered = raster._self_test_result
        reason, index, text, _ = failure(lambda: raster.get_mask(sel, size=size))
    finally:
        raster.render = render
    check(passed is None and remembered is None, f"a self-test that cannot run returns None and is not remembered "
                                                 f"({passed}, {remembered})")
    check(reason == 'GPU_ERROR' and index == -1 and text == message,
          f"and get_mask raises GPU_ERROR ({reason}, {index})")
    check(raster.availability(sel, size=size) == "", "availability does not report a failed self-test")
    got = raster.get_mask(sel, size=size).read()
    error = worst(got, reference.chain(ops, *size))
    check(raster._self_test_result is True and error <= 1e-5,
          f"the next get_mask runs the self-test again and builds (worst {error:.2e})")

    raster.invalidate()
    raster.reset_stats()
    run_pass = raster._run_pass
    raster._run_pass = no_context
    try:
        reason, index, text, held = failure(lambda: raster.get_mask(sel, size=size))
    finally:
        raster._run_pass = run_pass
    stats = raster.stats()
    check(reason == 'GPU_ERROR' and index == -1 and text == message and not held,
          f"a RuntimeError in a pass raises GPU_ERROR, holding no texture ({reason}, {index}) {held}")
    check(stats["pooled"] == 2 and stats["cached"] == 0, f"and both targets go back to the pool {stats}")
    raster.reset_stats()
    got = raster.get_mask(sel, size=size).read()
    error = worst(got, reference.chain(ops, *size))
    check(raster.stats()["allocations"] == 0 and raster.stats()["passes"] == 2 and error <= 1e-5,
          f"the next build reuses them and is exact (worst {error:.2e}) {raster.stats()}")

    raster.invalidate()
    acquire = raster._acquire
    calls = []

    def acquire_once(width, height):
        calls.append((width, height))
        if len(calls) > 1:
            raise RuntimeError("GPUTexture: texture creation failed")
        return acquire(width, height)

    raster._acquire = acquire_once
    try:
        reason, index, text, held = failure(lambda: raster.get_mask(sel, size=size))
    finally:
        raster._acquire = acquire
    stats = raster.stats()
    check(reason == 'GPU_ERROR' and not held and stats["pooled"] == 1 and stats["cached"] == 0,
          f"a failed allocation raises GPU_ERROR and pools the target already taken ({reason}) {stats}")
    raster.invalidate()


def test_peek():
    section("peek_mask only looks")
    if not available():
        return
    raster.invalidate()
    sel = fresh_selection()
    sel.add_op('ELLIPSE', points=[(0.2, 0.2), (0.7, 0.9)], feather=4.0)
    check(raster.peek_mask(sel, size=(256, 256)) is None, "nothing is cached before a build")
    built = raster.get_mask(sel, size=(256, 256))
    check(raster.peek_mask(sel, size=(256, 256)) is built, "the built mask afterwards")
    check(raster.peek_mask(sel, size=(128, 128)) is None, "and nothing at another size")


def test_undo():
    section("undo and redo find their masks in the cache")
    if not available():
        return
    raster.invalidate()
    sel = fresh_selection()
    sel.add_op('BOX', points=[(0.1, 0.1), (0.6, 0.7)], feather=8.0)
    sel.add_op('ELLIPSE', 'ADD', points=[(0.3, 0.3), (0.9, 0.8)], feather=8.0)
    sel.add_op('INVERT')
    bpy.ops.ed.undo_push(message="three ops")
    size = (512, 512)
    three = raster.get_mask(sel, size=size).read()
    tree().selection.add_op('BOX', 'SUBTRACT', points=[(0.0, 0.0), (0.2, 0.2)])
    bpy.ops.ed.undo_push(message="four ops")
    raster.reset_stats()
    four = raster.get_mask(tree().selection, size=size).read()
    check(raster.stats()["passes"] == 1, f"the fourth op resumes from the cached three {raster.stats()}")

    bpy.ops.ed.undo()
    restored = bpy.data.node_groups[TREE].selection
    got = raster.get_mask(restored, size=size).read()
    check(len(restored.ops) == 3 and np.array_equal(got, three) and raster.stats()["passes"] == 1,
          f"undo restores the previous mask exactly, without a pass {raster.stats()}")
    bpy.ops.ed.redo()
    restored = bpy.data.node_groups[TREE].selection
    got = raster.get_mask(restored, size=size).read()
    check(len(restored.ops) == 4 and np.array_equal(got, four) and raster.stats()["passes"] == 1,
          f"redo puts the four-op mask back, without a pass {raster.stats()}")


def test_cost():
    section("cost of a 4K mask")
    if not available():
        return
    raster.invalidate()
    sel = fresh_selection()
    sel.add_op('BOX', points=[(0.1, 0.1), (0.6, 0.7)], feather=24.0)
    sel.add_op('LASSO', 'ADD', points=reference.star(200, 0.55, 0.5, 0.4, 0.2, 7), feather=64.0)
    start = time.perf_counter()
    raster.get_mask(sel, size=(4096, 4096)).read_bytes()
    built = 1000 * (time.perf_counter() - start)
    sel.add_op('LASSO', 'SUBTRACT', points=reference.star(200, 0.3, 0.3, 0.2, 0.1, 5), feather=8.0)
    raster.reset_stats()
    start = time.perf_counter()
    raster.get_mask(sel, size=(4096, 4096)).read_bytes()
    appended = 1000 * (time.perf_counter() - start)
    print(f"  4096x4096 on {renderer()}: box and feathered 200-point lasso {built:.0f} ms, "
          f"one more lasso {appended:.0f} ms, each with an 8-bit read back")
    check(raster.stats()["passes"] == 1, "the appended lasso is one pass")
    check(built < 2000 and appended < 2000, f"both take under 2 s ({built:.0f} and {appended:.0f} ms)")


def test_release():
    section("release gives everything back")
    if not available():
        return
    raster.release()
    stats = raster.stats()
    check(stats["cached"] == 0 and stats["pooled"] == 0 and stats["video_memory"] == 0, f"nothing held {stats}")


for test in (test_availability,
             test_availability_does_not_probe,
             test_self_test,
             test_self_test_failure,
             test_feathered_box,
             test_read_back_and_state,
             test_set_operations,
             test_hard_lasso_is_exact,
             test_precision,
             test_degenerate_shapes,
             test_bands,
             test_udim_tiles,
             test_cache,
             test_cache_budget,
             test_problems,
             test_outline_limits,
             test_gpu_errors,
             test_peek,
             test_undo,
             test_cost,
             test_release):
    guarded(test)

# Give the textures back while the GPU context is still up. Python frees
# them at interpreter shutdown otherwise, which is after the context has
# gone, and freeing a texture there segfaults Blender.
raster.release()

finish("SELECTION RASTER TEST")
