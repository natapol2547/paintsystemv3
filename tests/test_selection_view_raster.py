"""Selections drawn in the 3D view are rasterised into UV space (PS-093).

`selection.view_raster` draws a `VIEW` op through the texel map of the
object it was drawn on. The self-test chains are compared with
`selection_reference.view_chain`, which evaluates the same stages in
float64 on the CPU; the rest builds masks on real meshes and checks
them against what the view shows: which faces a box reaches with and
without Through, where texels project, and that the selection stays on
its texels when the object moves.

These need a GPU context, which background Blender only has from 5.2
(`gpu.init()`); 4.2 to 5.1 run them windowed under `tests/run.sh --ui`.
The `view_eye` checks run on the CPU everywhere.
"""
import math
import os
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace

import bmesh
import bpy
import numpy as np
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Euler, Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selection_reference as reference  # noqa: E402
from harness import check, finish, guarded, import_from, read_texel_map, register_addon, section, skip  # noqa: E402

register_addon()
core = import_from("gpu_passes.core")
surface = import_from("gpu_passes.surface")
texel_map = import_from("gpu_passes.texel_map")
raster = import_from("selection.raster")
view_raster = import_from("selection.view_raster")
session = import_from("selection.session")

TREE = "PS View Raster Tree"
REGION = (800, 600)
SIZE = 512
EYE = (4.0, -3.5, 3.0)


def available():
    if core.gpu_available():
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


def cube():
    return bpy.data.objects["Cube"]


def fresh_selection():
    made = bpy.data.node_groups.get(TREE)
    if made is None:
        made = bpy.data.node_groups.new(TREE, 'PaintSystemNodeTree')
        made.initialize()
    made.selection.clear()
    return made.selection


def look_at(eye, target=(0.0, 0.0, 0.0), up=(0.0, 0.0, 1.0)):
    """A world-to-view matrix, float64 rows first."""
    eye, target, up = (np.asarray(value, dtype=np.float64) for value in (eye, target, up))
    forward = target - eye
    forward /= np.linalg.norm(forward)
    side = np.cross(forward, up)
    side /= np.linalg.norm(side)
    matrix = np.eye(4)
    matrix[0, :3], matrix[1, :3], matrix[2, :3] = side, np.cross(side, forward), -forward
    matrix[:3, 3] = -matrix[:3, :3] @ eye
    return matrix


def perspective(fov_y=math.radians(40.0), aspect=REGION[0] / REGION[1], near=0.1, far=100.0):
    f = 1.0 / math.tan(fov_y / 2.0)
    matrix = np.zeros((4, 4))
    matrix[0, 0], matrix[1, 1] = f / aspect, f
    matrix[2, 2], matrix[2, 3] = (far + near) / (near - far), 2.0 * far * near / (near - far)
    matrix[3, 2] = -1.0
    return matrix


def orthographic(half_height=2.0, aspect=REGION[0] / REGION[1], near=-50.0, far=50.0):
    matrix = np.eye(4)
    matrix[0, 0], matrix[1, 1] = 1.0 / (half_height * aspect), 1.0 / half_height
    matrix[2, 2], matrix[2, 3] = -2.0 / (far - near), -(far + near) / (far - near)
    return matrix


def flat(matrix):
    """A float64 rows-first matrix as a `subtype='MATRIX'` property takes it, column by column."""
    return np.asarray(matrix, dtype=np.float64).T.ravel().tolist()


def add_view_op(selection, obj, kind, points, view, projection, mode='REPLACE', region=REGION, through=False,
                feather=0.0, antialias=True, uv_map="UVMap"):
    """Append a `VIEW` op as a tool would commit it: the view stored object-to-view."""
    stored = np.asarray(view) @ np.array(obj.matrix_world)
    op = selection.add_op(kind, mode, space='VIEW', points=points, region_size=region, view_matrix=flat(stored),
                          projection_matrix=flat(projection), object=obj, uv_map=uv_map, through=through)
    op.feather = feather
    op.antialias = antialias
    return op


def render(obj, kind, points, view, projection, through=False, region=REGION, size=SIZE, feather=0.0,
           antialias=True):
    """One `VIEW` op drawn from an empty mask, without the cache."""
    spec = raster.OpSpec(kind, 'REPLACE', feather, antialias, points,
                         view=view_raster.ViewSpec(view_raster.ObjectSurface(obj, "UVMap"), region, view,
                                                   projection, through))
    return raster.render([spec], size, size)


def full_box(region=REGION):
    return [(-10.3, -10.1), (region[0] + 10.7, region[1] + 10.2)]


def surface_texels(obj, size=SIZE):
    """World positions and normals of *obj*'s texel map, `(size * size, 4)` float64 each."""
    found = texel_map.get_texel_map(obj, "UVMap", (size, size))
    return (read_texel_map(found).reshape(-1, 4).astype(np.float64),
            read_texel_map(found, 1).reshape(-1, 4).astype(np.float64))


def project(points, view, projection, region=REGION):
    clip = np.c_[points, np.ones(len(points))] @ (projection @ view).T
    return (clip[:, :2] / clip[:, 3:4] * 0.5 + 0.5) * np.asarray(region, dtype=np.float64)


def new_object(name, mesh=None):
    mesh = mesh or cube().data.copy()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def remove_object(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def cancel_session_tick():
    if bpy.app.timers.is_registered(session._tick):
        bpy.app.timers.unregister(session._tick)


def failure(selection, size=(SIZE, SIZE)):
    """`(reason, op index)` of what `get_mask` raises for *selection*, or None when it builds."""
    try:
        raster.get_mask(selection, size)
    except raster.MaskUnavailable as error:
        return error.reason, error.op_index
    return None


def test_constants():
    section("the reference uses the module's constants")
    names = ("REACH_PAD", "DEPTH_REL_BIAS", "DEPTH_ABS_BIAS", "TEXEL_SLOPE_CAP", "MARGIN_ALPHA")
    check(all(getattr(reference, name) == getattr(view_raster, name) for name in names),
          f"{', '.join(names)} agree")


def test_view_eye():
    section("the eye stays on the side it was on when the object is scaled after commit")
    rng = np.random.default_rng(93)

    def rotation():
        q, r = np.linalg.qr(rng.standard_normal((3, 3)))
        q = q * np.sign(np.diag(r))
        if np.linalg.det(q) < 0.0:
            q[:, 0] = -q[:, 0]
        return q

    def transform(scale):
        matrix = np.eye(4)
        matrix[:3, :3] = rotation() * scale
        matrix[:3, 3] = rng.uniform(-3.0, 3.0, 3)
        return matrix

    for name, projection in (("orthographic", orthographic()), ("perspective", perspective())):
        mismatches = row_mismatches = compared = 0
        for index in range(1000):
            view = np.eye(4)
            view[:3, :3] = rotation()
            view[:3, 3] = (0.0, 0.0, -10.0) + rng.uniform(-1.0, 1.0, 3)
            placed = transform(np.ones(3))
            stored = view @ placed
            scale = rng.uniform(0.3, 3.0, 3)
            if index % 2:
                scale[rng.integers(3)] *= -1.0
            moved = transform(scale)
            point = rng.uniform(-1.0, 1.0, 3)
            normal = rng.standard_normal(3)

            eye = view_raster.view_eye(view, projection)
            world_point = (placed @ np.append(point, 1.0))[:3]
            world_normal = placed[:3, :3] @ normal
            before = world_normal @ (eye[:3] - world_point * eye[3])
            now_view = stored @ np.linalg.inv(moved)
            eye = view_raster.view_eye(now_view, projection)
            world_point = (moved @ np.append(point, 1.0))[:3]
            world_normal = np.linalg.inv(moved[:3, :3]).T @ normal
            after = world_normal @ (eye[:3] - world_point * eye[3])
            if abs(before) < 1e-3 * np.linalg.norm(normal) * max(1.0, np.linalg.norm(eye[:3] - world_point * eye[3])):
                continue
            compared += 1
            mismatches += (before > 0.0) != (after > 0.0)
            if name == "orthographic":
                row = now_view[2, :3] / np.linalg.norm(now_view[2, :3])
                row_mismatches += (before > 0.0) != (world_normal @ row > 0.0)
        check(mismatches == 0 and compared > 900,
              f"{name}: the facing sign matches the one at commit for all {compared} views ({mismatches} differ)")
        if name == "orthographic":
            check(row_mismatches > 0, f"where row 2 of the view as the eye would flip {row_mismatches}")
    check(view_raster.is_orthographic(orthographic()) and not view_raster.is_orthographic(perspective()),
          "is_orthographic tells the projections apart")


def test_self_test():
    section("the view self-test chains match the reference")
    if not available():
        return
    check(view_raster.view_self_test() is True, "view_self_test() passes")
    for perspective_view in (False, True):
        scene = view_raster.self_test_scene(perspective_view)
        for through in (False, True):
            label = f"{'perspective' if perspective_view else 'orthographic'}, through {through}"
            want = reference.view_chain(scene, view_raster.self_test_ops(), through, view_raster.SELF_TEST_REGION)
            expected = {(x, y): value for (in_perspective, with_through, x, y), value
                        in view_raster.SELF_TEST_VIEW_EXPECTED.items() if (in_perspective, with_through)
                        == (perspective_view, through)}
            stored = max(abs(want[y, x] - value) for (x, y), value in expected.items())
            check(len(expected) >= 5 and stored < 1e-12,
                  f"{label}: the {len(expected)} stored values are the reference's (off by {stored:.1e})")
            got = view_raster.self_test_chain(through, perspective_view)
            error = float(np.abs(got.astype(np.float64) - want).max())
            check(error <= 1e-4, f"{label}: every texel within 1e-4 (worst {error:.2e})")
            wide = view_raster.self_test_chain(through, perspective_view, band_rows=512)
            check(np.array_equal(got, wide), f"{label}: bands of 16 and 512 rows agree")
        if perspective_view:
            positions = scene["positions"][scene["positions"][..., 3] > 0.0][:, :3]
            depth = -(np.c_[positions, np.ones(len(positions))] @ scene["view"].T)[:, 2]
            check(not view_raster.is_orthographic(scene["projection"])
                  and 1.4 <= float(depth.min()) and float(depth.max()) <= 2.3,
                  f"the perspective scene is {float(depth.min()):.3f} to {float(depth.max()):.3f} from the eye")


def test_self_test_failure():
    section("a GPU that fails the view self-test builds no VIEW masks, and UV masks still")
    if not available():
        return
    expected = view_raster.SELF_TEST_VIEW_EXPECTED
    saved = dict(expected)
    view = look_at(EYE)
    view_selection = fresh_selection()
    add_view_op(view_selection, cube(), 'BOX', full_box(), view, perspective())
    try:
        expected[(True, False, 17, 15)] = 0.5
        view_raster._view_self_test_result = None
        raster.invalidate()
        check(view_raster.view_self_test() is False, "with one expected value wrong, view_self_test() fails")
        check(failure(view_selection) == ('SELF_TEST', -1)
              and raster.availability(view_selection, (SIZE, SIZE)) == raster.MESSAGES['SELF_TEST'],
              f"a VIEW selection raises SELF_TEST and availability says so ({failure(view_selection)})")
        uv_selection = fresh_selection()
        uv_selection.add_op('BOX', points=[(0.1, 0.1), (0.6, 0.6)])
        uv_selection.add_op('ALL', 'ADD', space='VIEW')
        check(raster.availability(uv_selection, (SIZE, SIZE)) == ""
              and raster.get_mask(uv_selection, (SIZE, SIZE)) is not None,
              "a selection without an outlined VIEW op still builds")
    finally:
        expected.clear()
        expected.update(saved)
        view_raster._view_self_test_result = None
        raster.invalidate()
    check(view_raster.view_self_test() is True, "restored, it passes again")


def test_cube_through():
    section("a box over the whole view reaches the faces it should")
    if not available():
        return
    obj = cube()
    view = look_at(EYE)
    projection = perspective()
    positions, normals = surface_texels(obj)
    island = positions[:, 3] > view_raster.MARGIN_ALPHA
    axis = np.argmax(np.abs(normals[:, :3]), axis=1)
    sign = np.sign(normals[np.arange(len(normals)), axis])
    masks = {}
    for through in (False, True):
        mask = render(obj, 'BOX', full_box(), view, projection, through).ravel()
        masks[through] = mask
        faces = []
        for face_axis in range(3):
            for face_sign in (1.0, -1.0):
                on_face = island & (axis == face_axis) & (sign == face_sign)
                centre = np.zeros(3)
                centre[face_axis] = face_sign
                front = float(centre @ (np.asarray(EYE) - centre)) > 0.0
                if through or front:
                    faces.append(bool(on_face.any()) and bool((mask[on_face] > 0.5).all()))
                else:
                    faces.append(bool(on_face.any()) and bool((mask[on_face] == 0.0).all()))
        check(all(faces), f"through {through}: "
              + ("all six faces are selected" if through else "the three front faces are selected and the back "
                 "faces are 0") + f" {faces}")
    # The -x face seen at about 73 degrees, next to the hidden +y face: at
    # the edge of its UV island the texel beyond lies on the hidden face.
    grazing = look_at((-1.5, -5.0, 0.4))
    mask = render(obj, 'BOX', full_box(), grazing, orthographic(), False).ravel()
    facing = normals[:, :3] @ grazing[2, :3]
    seen = island & (facing > 0.1)
    missed = seen & (mask <= 0.5)
    check(bool(seen.any()) and not missed.any() and not (mask[island & (facing < -0.1)] > 0.0).any(),
          f"a face seen at a grazing angle is selected up to its edges ({int(missed.sum())} of {int(seen.sum())} "
          f"facing texels missed)")
    small = render(obj, 'BOX', [(380.2, 280.4), (420.6, 320.1)], view, projection, True).ravel()
    inside = project(positions[:, :3], view, projection)
    outside = island & ~((inside >= (379.2, 279.4)) & (inside <= (421.6, 321.1))).all(axis=1)
    check(not (small[outside] > 0.0).any(), f"a small box selects no island texel more than a pixel outside it "
                                            f"({int((small[outside] > 0.0).sum())})")

    bpy.ops.mesh.primitive_plane_add(size=6.0, location=(2.0, -1.75, 1.5), rotation=(0.9, 0.0, 0.8))
    plane = bpy.context.active_object
    bpy.context.view_layer.update()
    try:
        occluded = render(obj, 'BOX', full_box(), view, projection, False).ravel()
        check(np.array_equal(occluded, masks[False]),
              "a second object between the view and the cube changes nothing: only the painted object occludes")
    finally:
        remove_object(plane)
        bpy.context.view_layer.objects.active = obj

    margin = (positions[:, 3] > 0.0) & ~island
    grid = masks[False].reshape(SIZE, SIZE)
    selected = (grid > 0.5) & island.reshape(SIZE, SIZE)
    unselected = (grid <= 0.5) & island.reshape(SIZE, SIZE)

    def near(values):
        padded = np.pad(values, 4)
        out = np.zeros_like(values)
        for dy in range(9):
            for dx in range(9):
                out |= padded[dy:dy + SIZE, dx:dx + SIZE]
        return out.ravel()

    band = margin & near(selected) & ~near(unselected)
    check(int(band.sum()) > 1000 and bool((masks[False][band] > 0.5).all()),
          f"the margin next to a selected island is selected with it ({int(band.sum())} texels, "
          f"{int((masks[False][band] <= 0.5).sum())} not)")


def test_projection():
    section("texels land where the view projects them")
    if not available():
        return
    obj = cube()
    positions, _ = surface_texels(obj)
    region = SimpleNamespace(width=REGION[0], height=REGION[1])
    rng = np.random.default_rng(7)
    for name, view, projection in (("orthographic", look_at(EYE), orthographic(1.6)),
                                   ("perspective", look_at(EYE), perspective())):
        rv3d = SimpleNamespace(perspective_matrix=Matrix((projection @ view).tolist()))
        # A box edge through the middle of the region with the widest soft
        # edge: coverage rises smoothly across the whole region, so it can
        # be turned back into the screen coordinate of each texel.
        ramps = [render(obj, 'BOX', [(400.0, -5000.0), (5000.0, 5000.0)], view, projection, True,
                        feather=raster.FEATHER_MAX).ravel(),
                 render(obj, 'BOX', [(-5000.0, 300.0), (5000.0, 5000.0)], view, projection, True,
                        feather=raster.FEATHER_MAX).ravel()]
        expected = np.array([location_3d_to_region_2d(region, rv3d, point[:3]) for point in positions[:, :3]])
        usable = ((positions[:, 3] > view_raster.MARGIN_ALPHA) & (expected >= 2.0).all(axis=1)
                  & (expected <= np.subtract(REGION, 2.0)).all(axis=1))
        samples = rng.choice(np.flatnonzero(usable), 200, replace=False)
        half_width = 0.5 * raster.FEATHER_MAX
        got = np.empty((200, 2))
        for axis, (ramp, edge) in enumerate(zip(ramps, (400.0, 300.0))):
            coverage = ramp[samples].astype(np.float64)
            t = 0.5 - np.sin(np.arcsin(1.0 - 2.0 * coverage) / 3.0)
            got[:, axis] = edge - half_width + 2.0 * half_width * t
        error = float(np.abs(got - expected[samples]).max())
        check(error < 0.01, f"{name}: 200 texels within 0.01 px of location_3d_to_region_2d (worst {error:.2e})")

        clip = render(obj, 'BOX', [(-50.0, -50.0), (REGION[0] + 50.0, REGION[1] + 50.0)], view, orthographic(0.6),
                      True).ravel()
        screen = project(positions[:, :3], view, orthographic(0.6))
        island = positions[:, 3] > view_raster.MARGIN_ALPHA
        beyond = island & ((screen < -0.01) | (screen > np.add(REGION, 0.01))).any(axis=1)
        within = island & ((screen > 2.0) & (screen < np.subtract(REGION, 2.0))).all(axis=1)
        if name == "orthographic":
            check(beyond.any() and not clip[beyond].any() and within.any() and bool((clip[within] == 1.0).all()),
                  f"a box past the region's edges selects nothing that projects outside the region "
                  f"({int(beyond.sum())} texels outside, {int(within.sum())} inside)")


def test_chains():
    section("VIEW ops combine with UV ops and resume from cached prefixes")
    if not available():
        return
    obj = cube()
    view = look_at(EYE)
    projection = perspective()
    lasso = [(250.4, 120.2), (610.7, 180.9), (520.3, 520.6), (300.1, 430.5)]
    ellipse = [(350.2, 250.7), (470.9, 380.3)]
    size = (SIZE, SIZE)

    def single(add):
        selection = fresh_selection()
        add(selection, 'REPLACE')
        return raster.get_mask(selection, size).read().astype(np.float64)

    def uv_box(selection, mode):
        selection.add_op('BOX', mode, points=[(0.05, 0.1), (0.45, 0.7)], feather=6.0)

    def view_lasso(selection, mode):
        add_view_op(selection, obj, 'LASSO', lasso, view, projection, mode, feather=12.0)

    def view_ellipse(selection, mode):
        add_view_op(selection, obj, 'ELLIPSE', ellipse, view, projection, mode, feather=4.0)

    first, second, third = single(uv_box), single(view_lasso), single(view_ellipse)
    selection = fresh_selection()
    uv_box(selection, 'REPLACE')
    view_lasso(selection, 'ADD')
    view_ellipse(selection, 'SUBTRACT')
    got = raster.get_mask(selection, size).read()
    want = np.minimum(np.maximum(first, second), 1.0 - third)
    error = float(np.abs(got - want).max())
    check(error <= 1e-4 and float(second.max()) == 1.0 and float(third.max()) == 1.0,
          f"UV box, VIEW lasso added and VIEW ellipse subtracted match the single masks within 1e-4 "
          f"(worst {error:.2e})")
    raster.reset_stats()
    add_view_op(selection, obj, 'BOX', [(100.0, 100.0), (700.0, 500.0)], view, projection, 'INTERSECT',
                through=True)
    raster.get_mask(selection, size)
    check(raster.stats()["passes"] == 1, f"appending a VIEW op is one pass {raster.stats()}")

    regions = ((640, 480), (1024, 768), (300, 200))
    selection = fresh_selection()
    for index, region in enumerate(regions):
        add_view_op(selection, obj, 'BOX', [(10.0, 10.0), (region[0] - 10.0, region[1] - 10.0)], view,
                    perspective(aspect=region[0] / region[1]), 'REPLACE' if index == 0 else 'ADD', region=region)
    raster.get_mask(selection, size)
    formats = {targets["colour"].format for targets in view_raster._targets.values()}
    check(len(view_raster._targets) <= view_raster.TARGET_SETS and formats == {'RG32F'},
          f"ops from three region sizes keep {len(view_raster._targets)} target sets, colour {formats}")


def test_digests():
    section("the digest follows the surface's content, not its placement")
    if not available():
        return
    obj = new_object("PS View Digest")
    try:
        selection = fresh_selection()
        add_view_op(selection, obj, 'BOX', full_box(), look_at(EYE), perspective())

        def digest():
            return selection.prefix_digests(SIZE, SIZE, 1001, surface_key=raster.view_key)[-1]

        before = digest()
        obj.data.update()
        bpy.context.view_layer.update()
        check(digest() == before, "obj.data.update() with nothing changed keeps it")
        vertex = obj.data.vertices[0]
        original = tuple(vertex.co)
        vertex.co.z += 0.3
        obj.data.update()
        bpy.context.view_layer.update()
        moved = digest()
        check(moved != before, "a vertex move changes it")
        vertex.co = original
        obj.data.update()
        bpy.context.view_layer.update()
        check(digest() == before, "and moving it back restores it")
    finally:
        remove_object(obj)


def test_moved_object():
    section("a selection stays on its texels when the object moves")
    if not available():
        return
    bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.45, major_segments=48, minor_segments=24)
    obj = bpy.context.active_object
    placed = Matrix.LocRotScale((0.2, -0.1, 0.1), Euler((0.9, 0.2, 0.4)), (1.0, 1.0, 1.0))
    transforms = {
        "rigid": Matrix.LocRotScale((3.0, 1.0, -2.0), Euler((0.3, 1.1, -0.7)), (1.0, 1.0, 1.0)),
        "uniform scale": Matrix.LocRotScale((-1.0, 0.5, 0.3), Euler((0.9, 0.2, 0.4)), (2.5, 2.5, 2.5)),
        "non-uniform scale": Matrix.LocRotScale((0.5, 0.0, 0.2), Euler((0.2, -0.6, 1.3)), (1.0, 3.0, 0.4)),
        "mirrored": Matrix.LocRotScale((0.5, 0.0, 0.2), Euler((0.2, -0.6, 1.3)), (-1.5, 1.0, 0.6)),
    }
    limit = int(1e-4 * SIZE * SIZE)
    try:
        for name, projection in (("perspective", perspective()), ("orthographic", orthographic())):
            obj.matrix_world = placed
            bpy.context.view_layer.update()
            selection = fresh_selection()
            add_view_op(selection, obj, 'BOX', [(250.3, 150.2), (560.7, 470.4)], look_at(EYE), projection)
            before = selection.prefix_digests(SIZE, SIZE, 1001, surface_key=raster.view_key)[-1]
            base = raster.get_mask(selection, (SIZE, SIZE)).read()
            for label, matrix in transforms.items():
                obj.matrix_world = matrix
                bpy.context.view_layer.update()
                digest = selection.prefix_digests(SIZE, SIZE, 1001, surface_key=raster.view_key)[-1]
                raster.invalidate()
                mask = raster.get_mask(selection, (SIZE, SIZE)).read()
                differ = int((np.abs(mask - base) > 1e-3).sum())
                check(digest == before and differ <= limit and int((base > 0.5).sum()) > 10000,
                      f"{name}, {label}: same digest, {differ} texels differ by more than 1e-3 (at most {limit})")
    finally:
        remove_object(obj)
        bpy.context.view_layer.objects.active = cube()


def two_quads(scale, gap):
    """A 2 x 2 quad facing +z at z 0 and a 1 x 1 quad *gap* behind it, as separate UV islands, times *scale*."""
    mesh = bpy.data.meshes.new("PS View Quads")
    built = bmesh.new()
    uv = built.loops.layers.uv.new("UVMap")
    for half, z, u0 in ((1.0, 0.0, 0.05), (0.5, -gap, 0.55)):
        corners = [built.verts.new((x * half * scale, y * half * scale, z * scale))
                   for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        face = built.faces.new(corners)
        for loop, (a, b) in zip(face.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
            loop[uv].uv = (u0 + 0.4 * a, 0.05 + 0.4 * b)
    built.to_mesh(mesh)
    built.free()
    return new_object("PS View Quads", mesh)


def test_depth_bias_at_scale():
    section("occlusion holds at large scene scales")
    if not available():
        return
    obj = two_quads(100.0, 0.002)
    try:
        view = look_at((0.0, 0.0, 500.0), up=(0.0, 1.0, 0.0))
        projection = perspective(near=1.0, far=5000.0)
        positions, _ = surface_texels(obj)
        island = positions[:, 3] > view_raster.MARGIN_ALPHA
        columns = np.tile((np.arange(SIZE) + 0.5) / SIZE, SIZE)
        mask = render(obj, 'BOX', full_box(), view, projection).ravel()
        front, back = island & (columns < 0.5), island & (columns > 0.5)
        check(bool((mask[front] > 0.5).all()) and not (mask[back] > 0.5).any(),
              f"at 100x, a quad 0.2 units behind the visible one from 500 away is not selected "
              f"({int((mask[back] > 0.5).sum())} of {int(back.sum())} selected)")
    finally:
        remove_object(obj)
    for scale in (1.0, 100.0, 1000.0):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=scale, segments=64, ring_count=32)
        sphere = bpy.context.active_object
        bpy.context.view_layer.update()
        try:
            eye = np.array((0.0, -5.0 * scale, 1.5 * scale))
            mask = render(sphere, 'BOX', full_box(), look_at(eye), perspective(near=0.01 * scale, far=50.0 * scale),
                          ).ravel()
            positions, normals = surface_texels(sphere)
            island = positions[:, 3] > view_raster.MARGIN_ALPHA
            to_eye = eye - positions[:, :3]
            cosine = np.einsum('ij,ij->i', normals[:, :3], to_eye) / np.linalg.norm(to_eye, axis=1)
            holes = int((island & (cosine > 0.2) & (mask <= 0.5)).sum())
            behind = int((island & (cosine < -0.2) & (mask > 0.5)).sum())
            check(holes == 0 and behind == 0,
                  f"a sphere of radius {scale:g} has {holes} holes and {behind} texels selected on its far side")
        finally:
            remove_object(sphere)
    bpy.context.view_layer.objects.active = cube()


def setup_session():
    obj = cube()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    # Each test calls this; the cube is set up by the first one.
    if obj.active_material is None or obj.active_material.paint_system.tree is None:
        bpy.ops.paint_system.setup_material()
    bpy.ops.paint_system.add_layer(layer_type='IMAGE', resolution='1024')
    obj.active_material.paint_system.tree.nodes.active.image = bpy.data.images.new("PS View Layer", 256, 256)
    bpy.context.view_layer.update()
    return obj.active_material.paint_system.tree


def test_reasons():
    section("a VIEW op whose surface or view cannot be used says why, and is tried again once fixed")
    if not available():
        return
    tree = setup_session()
    obj = new_object("PS View Reasons")
    other_scene = bpy.data.scenes.new("PS View Other Scene")
    selection = tree.selection
    selection.clear()
    selection.add_op('ALL')
    op = add_view_op(selection, obj, 'BOX', full_box(), look_at(EYE), perspective(), 'SUBTRACT')
    session.forget_failures()

    def expect(reason, label):
        state = session.sync()
        got = failure(selection, (256, 256))
        check(got == (reason, 1) and state.reason == reason and session.label(state) == label
              and state.digest not in session._failures and raster.peek_mask(selection, (256, 256)) is None,
              f"{label!r}: get_mask raises {reason} at op 1, peek_mask finds nothing, and the session shows it "
              f"without remembering it ({got}, {state.reason})")

    def fixed(what):
        state = session.sync()
        check(state.reason == "" and state.active, f"{what} clears it on the next sync ({state.reason})")

    try:
        fixed("a usable surface")
        with bpy.context.temp_override(selected_objects=[obj], selected_editable_objects=[obj], active_object=obj,
                                       object=obj):
            bpy.ops.object.delete()
        check(op.object == obj and obj.name not in bpy.context.view_layer.objects,
              "the op still points at the deleted object")
        expect('SURFACE', "Selection's object or UV map is gone")
        cancel_session_tick()
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        check(bpy.app.timers.is_registered(session._tick), "linking it back notifies the session")
        fixed("linking it back")

        bpy.context.scene.collection.objects.unlink(obj)
        other_scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        expect('SURFACE', "Selection's object or UV map is gone")
        other_scene.collection.objects.unlink(obj)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        fixed("linking it to this scene")

        modifier = obj.modifiers.new("PS Remesh", 'REMESH')
        bpy.context.view_layer.update()
        expect('SURFACE', "Selection's object or UV map is gone")
        obj.modifiers.remove(modifier)
        bpy.context.view_layer.update()
        fixed("removing the modifier")

        obj.data.uv_layers["UVMap"].name = "PS Renamed"
        bpy.context.view_layer.update()
        expect('SURFACE', "Selection's object or UV map is gone")
        obj.data.uv_layers["PS Renamed"].name = "UVMap"
        bpy.context.view_layer.update()
        fixed("renaming the UV map back")

        op.region_size = (0, 0)
        expect('VIEW', "Selection's view is invalid")
        op.region_size = REGION
        fixed("a region with pixels")

        obj.scale = (0.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        expect('VIEW', "Selection's view is invalid")
        cancel_session_tick()
        obj.scale = (1.0, 1.0, 1.0)
        bpy.context.view_layer.update()
        check(bpy.app.timers.is_registered(session._tick),
              "scaling it back, a transform-only update, notifies the session")
        fixed("scaling it back")

        # Drawn while the object was flat: the stored object-to-view matrix
        # has no inverse even once the object is scaled back.
        stored = np.array(op.view_matrix, dtype=np.float64)
        op.view_matrix = flat(stored @ np.diag((1.0, 1.0, 0.0, 1.0)))
        try:
            got = failure(selection, (256, 256))
        except np.linalg.LinAlgError as error:
            got = error
        check(got == ('VIEW', 1), f"a stored view without an inverse raises VIEW at op 1 ({got!r})")
        expect('VIEW', "Selection's view is invalid")
        op.view_matrix = flat(stored)
        fixed("a stored view with an inverse")

        op.object = cube()
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            got = failure(selection, (256, 256))
            check(got == ('EDIT_MODE', 1), f"Edit Mode raises EDIT_MODE at op 1 ({got})")
            check(session.label(session.State(selected=True, reason='EDIT_MODE'))
                  == "Leave Edit Mode to use the selection", "with its label")
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
        fixed("leaving Edit Mode")

        # A linked duplicate puts the shared mesh in Edit Mode while the
        # op's own object stays in its mode.
        twin = bpy.data.objects.new("PS View Twin", cube().data)
        bpy.context.scene.collection.objects.link(twin)
        view_layer = bpy.context.view_layer
        view_layer.update()
        view_layer.objects.active = twin
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            try:
                got = failure(selection, (256, 256))
            except KeyError as error:
                got = error
            check(cube().mode != 'EDIT' and got == ('EDIT_MODE', 1),
                  f"a linked duplicate in Edit Mode raises EDIT_MODE at op 1 ({got!r})")
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
            view_layer.objects.active = cube()
            bpy.data.objects.remove(twin)
        fixed("the linked duplicate leaving Edit Mode")
    finally:
        selection.clear()
        session.sync(force=True)
        remove_object(obj)
        bpy.data.scenes.remove(other_scene)


def test_linked_object():
    section("a VIEW op on a linked object is found in the view layer by identity, not by name")
    directory = tempfile.mkdtemp(prefix="ps_view_link_")
    path = os.path.join(directory, "library.blend")
    local = new_object("PS View Shared Name")
    bpy.data.libraries.write(path, {local})
    with bpy.data.libraries.load(path, link=True) as (_, linked_data):
        linked_data.objects = [local.name]
    linked = linked_data.objects[0]
    library = linked.library
    bpy.context.scene.collection.objects.link(linked)
    bpy.context.view_layer.update()
    selection = fresh_selection()
    try:
        op = add_view_op(selection, linked, 'BOX', full_box(), look_at(EYE), perspective())
        check(linked.library is not None and linked.name == local.name
              and bpy.context.view_layer.objects.get(linked.name) == local,
              "a linked object shares its name with a local one, which a name lookup finds")
        problem = raster._view_problem(op, raster.view_key)
        check(problem is None, f"the op on the linked object has no problem ({problem})")
    finally:
        selection.clear()
        remove_object(linked)
        bpy.data.libraries.remove(library)
        remove_object(local)
        shutil.rmtree(directory, ignore_errors=True)


def test_frame_change():
    section("an animated surface changes the session's digest when the frame changes")
    if not available():
        return
    tree = setup_session()
    scene = bpy.context.scene
    obj = new_object("PS View Animated")
    modifier = obj.modifiers.new("PS Displace", 'DISPLACE')
    modifier.strength = 0.0
    modifier.keyframe_insert("strength", frame=1)
    modifier.strength = 0.5
    modifier.keyframe_insert("strength", frame=10)
    try:
        scene.frame_set(1)
        tree.selection.clear()
        add_view_op(tree.selection, obj, 'BOX', full_box(), look_at(EYE), perspective())
        first = session.sync()
        scene.frame_set(10)
        later = session.sync()
        scene.frame_set(1)
        back = session.sync()
        check(first.active and later.active and later.digest != first.digest and back.digest == first.digest,
              "frame 10 has another digest than frame 1, and frame 1 again the first")
    finally:
        tree.selection.clear()
        session.sync(force=True)
        remove_object(obj)
        scene.frame_set(1)


def test_cost():
    section("cost of a 4K VIEW mask")
    if not available():
        return
    selection = fresh_selection()
    add_view_op(selection, cube(), 'BOX', [(120.4, 80.2), (690.7, 540.3)], look_at(EYE), perspective(), feather=8.0)
    raster.get_mask(selection, (4096, 4096))
    raster.invalidate()
    start = time.perf_counter()
    raster.get_mask(selection, (4096, 4096)).read_bytes()
    built = 1000.0 * (time.perf_counter() - start)
    print(f"  4096x4096 VIEW box on the cube, warm, with an 8-bit read back: {built:.0f} ms")
    check(built < 2000.0, f"under 2 s ({built:.0f} ms)")
    raster.invalidate()
    texel_map.invalidate()


def test_release():
    section("release gives everything back")
    if not available():
        return
    raster.release()
    check(not view_raster._targets and not view_raster._gpu and view_raster._view_self_test_result is None,
          "no region targets, shaders or self-test result are held")


for test in (test_constants,
             test_view_eye,
             test_self_test,
             test_self_test_failure,
             test_cube_through,
             test_projection,
             test_chains,
             test_digests,
             test_moved_object,
             test_depth_bias_at_scale,
             test_reasons,
             test_linked_object,
             test_frame_change,
             test_cost,
             test_release):
    guarded(test)

# GPU objects still referenced when Python exits are freed after the GPU context (see test_selection_raster).
session.release()
raster.release()
texel_map.release()
surface.release()
finish("SELECTION VIEW RASTER TEST")
