"""The painter's GPU passes, and a Painterly layer built with them (PS-053).

`filters.painter.plan` decides every stamp in numpy and has its own test,
`tests/test_painter_plan.py`. What is left for the GPU is checked here at
two levels. The passes run over textures the test builds, against numpy
written out here: the luma, the Sobel gradient with its edges clamped and
its y pointing up, the reduction to the strongest edge, and the gather
that reads both at every stamp centre. Then stamps are drawn one or two
at a time, which is where a turned quad or a blend order is easy to get
backwards and still look like paint, and over an island map that a
stamp must keep to. A stamp and the pieces of it carried across seams
paint each texel once, a piece paints the far island's own texels only
where the part of the stamp it carries is off the stamp's own island,
and a stamp that crosses a seam between two quads runs on into the far
quad as the unfolded mesh shows it, whether the far quad's UVs are
turned, mirrored or scaled.

Last, whole layers are built on a plane whose UV map covers the image,
so there are no seams to cross. They are held to what has to be true of
any painting rather than to one: a flat picture paints to itself, a
transparent one stays transparent, the same settings paint the same
pixels twice, keeping strokes to the plane's one island changes nothing,
and the 4K build with the default settings stays inside the ticket's ten
seconds. Two quads apart in UV check that no stroke paints one quad's
texels from the other, that a build whose mesh enters Edit Mode
halfway paints what it would have painted anyway, and that the islands
come from the UV map the layers below use. Suzanne checks the seams:
painted, a smooth picture changes about as much across them as within
an island, which it does not when strokes are only kept to their
islands, and each island's colour is carried across onto the islands it
joins and onto no other.

These need a GPU context, as `tests/test_filter_build.py` explains.
"""
import os
import sys
import time
import traceback
from math import pi

import bmesh
import bpy
import gpu
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_plane, check, finish, import_from,  # noqa: E402
                     read_texel_map, register_addon, section, skip, use_tree)

register_addon()
gpu_core = import_from("gpu_passes.core")
core = import_from("compiler.core")
filters_core = import_from("filters.core")
layer_build = import_from("filters.layer_build")
layer_plan = import_from("filters.layer_plan")
painter_build = import_from("filters.painter.build")
plan = import_from("filters.painter.plan")
drawing = import_from("filters.painter.drawing")
seams = import_from("filters.painter.seams")
texel_map = import_from("gpu_passes.texel_map")
create_managed_image = import_from("compiler.bake").create_managed_image
real_draw_islands = texel_map.draw_islands

IMAGE = 'PaintSystemImageLayerNode'
FILTER = 'PaintSystemFilterLayerNode'

# The passes here run in RGBA32F, so only the shader's float arithmetic
# is in the way.
TOL = 1e-4
# A stamp is drawn into the pool's RGBA16F canvas from an R16F atlas.
HALF_TOL = 2e-3
BYTE_TOL = 2.0 / 255.0
SIZE = 1024
# Suzanne is painted at the size the seams were measured at.
SEAM_SIZE = 2048
# The ticket's budget for a 4K build at the default settings, scaled as
# `tests/test_perf.py` scales its own.
BUDGET_4K = 10.0


def perf_scale():
    raw = os.environ.get("PS_PERF_SCALE", "").strip()
    if raw:
        return float(raw)
    return 5.0 if os.environ.get("CI") else 1.0


def available():
    if gpu_core.gpu_available():
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


def upload(values, texture_format='RGBA32F'):
    rows, cols = values.shape[:2]
    array = np.ascontiguousarray(values, dtype=np.float32)
    return gpu.types.GPUTexture(
        (cols, rows), format=texture_format,
        data=gpu.types.Buffer('FLOAT', array.size, array.ravel()))


def run_spec(spec, values):
    """*values*, a ``(rows, cols, 4)`` array, through one pass of *spec*."""
    rows, cols = values.shape[:2]
    framebuffer, _texture = filters_core.run_pass(spec, upload(values))
    return gpu_core.read_color(framebuffer, cols, rows)


def worst(got, want):
    return float(np.abs(np.asarray(got, np.float64) - np.asarray(want, np.float64)).max())


def sobel(luma):
    """v2's Sobel with its edge padding, on rows that run bottom-up."""
    p = np.pad(luma.astype(np.float64), 1, mode='edge')
    below, middle, above = p[:-2], p[1:-1], p[2:]
    gx = (below[:, 2:] + 2 * middle[:, 2:] + above[:, 2:]
          - below[:, :-2] - 2 * middle[:, :-2] - above[:, :-2])
    gy = (above[:, :-2] + 2 * above[:, 1:-1] + above[:, 2:]
          - below[:, :-2] - 2 * below[:, 1:-1] - below[:, 2:])
    return gx, gy


def canvas(side, depth=True):
    """An empty canvas and a framebuffer over it, with the depth texture a build draws with.

    The textures are returned together, because a framebuffer does not
    keep them alive.
    """
    texture = upload(np.zeros((side, side, 4), dtype=np.float32), 'RGBA16F')
    if not depth:
        return (texture,), gpu.types.GPUFrameBuffer(color_slots=(texture,))
    slots = gpu.types.GPUTexture((side, side), format='DEPTH_COMPONENT32F')
    return (texture, slots), gpu.types.GPUFrameBuffer(color_slots=(texture,), depth_slot=slots)


NO_PIECES = seams.Pieces(stamp=np.zeros(0, np.int64), points=np.zeros((0, seams.CORNERS), complex),
                         coords=np.zeros((0, seams.CORNERS), complex), sources=np.zeros((0, seams.CORNERS), complex),
                         count=np.zeros(0, np.int64), island=np.zeros(0, np.int32))


def draw(framebuffer, side, masks, stamps, size, cell, islands=None, crossings=None, pieces=NO_PIECES):
    """*stamps* drawn over *framebuffer*, keeping to *islands*, a ``(side, side)`` array.

    Without *islands*, no texel is on an island, so a stamp paints
    wherever it lands. With *crossings*, the parts of the stamps that run
    over them are carried across as a build carries them. Otherwise the
    stamps are drawn with *pieces*.
    """
    image, origins = drawing.atlas(masks, cell, *drawing.atlas_layout(len(masks), cell, 16384)[:2])
    atlas = painter_build._upload(image)
    island_map = upload(np.zeros((side, side)) if islands is None else islands, 'R32F')
    corners, coords = drawing.quads(stamps, size, origins, cell)
    if crossings is not None:
        stamp, crossing = seams.candidates(crossings, corners.mean(axis=1), stamps.owner, size)
        pieces = seams.pieces(crossings, corners, coords, stamp, crossing)
    painter_build._draw_stamps(framebuffer, (side, side), atlas, island_map,
                               drawing.geometry(corners, coords, stamps.color, stamps.owner, pieces))
    return gpu_core.read_color(framebuffer, side, side)


def bilinear(image, x, y):
    """*image* at the points ``(x, y)``, in texels, filtered as the stamp shader filters its atlas."""
    x, y = x - 0.5, y - 0.5
    low_x, low_y = np.floor(x).astype(int), np.floor(y).astype(int)
    # One weight per point, for every channel of it.
    fx, fy = ((each - low).reshape(each.shape + (1,) * (image.ndim - 2)) for each, low in ((x, low_x), (y, low_y)))
    rows, columns = image.shape[:2]

    def at(dx, dy):
        return image[np.clip(low_y + dy, 0, rows - 1), np.clip(low_x + dx, 0, columns - 1)]

    return ((at(0, 0) * (1 - fx) + at(1, 0) * fx) * (1 - fy)
            + (at(0, 1) * (1 - fx) + at(1, 1) * fx) * fy)


def seam_pair(name, tree, turn, flip, offset):
    """Two quads sharing an edge, as a mesh that uses *tree*.

    A's UVs are its vertices' x and y, scaled into the lower left of the
    image as ``0.05 + 0.05i + 0.2 z``. B's UVs are what those would be,
    carried by ``offset + turn * uv + flip * conj(uv)``. So B lies in UV
    as the unfolded mesh would, seen through that map.
    """
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0), (1, 1, 0), (2, 1, 0)], [],
                     [(0, 1, 4, 3), (1, 2, 5, 4)])
    z = np.array([complex(*mesh.vertices[loop.vertex_index].co[:2]) for loop in mesh.loops])
    uv = (0.05 + 0.05j) + 0.2 * z
    uv[4:] = offset + turn * uv[4:] + flip * np.conj(uv[4:])
    mesh.uv_layers.new(name="UVMap").data.foreach_set(
        'uv', np.stack([uv.real, uv.imag], axis=1).astype(np.float32).ravel())
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    use_tree(obj, tree)
    return obj


def seam_samples(mesh, uv_map, side, distance):
    """Pairs of points either side of every seam of *mesh*, *distance* texels in from the edge.

    Found by walking the mesh in bmesh, apart from `seams`. Each seam edge
    gives 64 points along it on each side, moved *distance* texels into
    that side's face. Returns ``(here, there, further, ratio)``: the
    points on one side, the matching points on the other, the points
    twice as far in again on the first side, all as ``(n, 2)`` texels,
    and how much longer the edge is in UV on the other side.
    """
    bm = bmesh.new()
    bm.from_mesh(mesh)
    layer = bm.loops.layers.uv[uv_map]
    along = np.linspace(0.05, 0.95, 64)[:, None]
    here, there, further, ratio = [], [], [], []

    def inward(start, end, face):
        normal = np.array([start[1] - end[1], end[0] - start[0]])
        normal /= np.linalg.norm(normal)
        centre = np.mean([np.array(loop[layer].uv) * side for loop in face.loops], axis=0)
        return normal if np.dot(centre - start, normal) >= 0 else -normal

    for edge in bm.edges:
        if len(edge.link_faces) != 2:
            continue
        a, b = edge.link_loops
        b_start, b_end = (b, b.link_loop_next) if b.vert == a.vert else (b.link_loop_next, b)
        ends = [np.array(loop[layer].uv) * side for loop in (a, a.link_loop_next, b_start, b_end)]
        if max(np.abs(ends[0] - ends[2]).max(), np.abs(ends[1] - ends[3]).max()) <= 1e-6 * side:
            continue
        for (p0, p1, near), (q0, q1, far) in (((ends[0], ends[1], a.face), (ends[2], ends[3], b.face)),
                                              ((ends[2], ends[3], b.face), (ends[0], ends[1], a.face))):
            into_near, into_far = inward(p0, p1, near), inward(q0, q1, far)
            point = p0 + along * (p1 - p0) + distance * into_near
            here.append(point)
            there.append(q0 + along * (q1 - q0) + distance * into_far)
            further.append(point + 2 * distance * into_near)
            ratio.append(np.full(64, np.linalg.norm(q1 - q0) / np.linalg.norm(p1 - p0)))
    bm.free()
    return tuple(np.concatenate(each) for each in (here, there, further, ratio))


def continuity(painted, samples, covered):
    """How much colours change across the seams against within the islands, per distance.

    Returns ``{distance: (all edges, uneven edges)}``: the mean colour
    difference between the two sides of a seam, over the mean difference
    between two points as far apart within one island. Uneven edges are
    more than twice as long on one side as on the other. Only points
    whose filtered reads are all on real texels count.
    """
    ratios = {}
    for distance, (here, there, further, ratio) in samples.items():
        kept = on_texels(covered, here, there, further)
        colour = [bilinear(painted[..., :3], points[kept, 0], points[kept, 1]) for points in (here, there, further)]
        across = np.abs(colour[0] - colour[1]).mean(axis=1)
        within = np.abs(colour[0] - colour[2]).mean(axis=1)
        uneven = (ratio[kept] < 0.5) | (ratio[kept] > 2.0)
        ratios[distance] = (across.mean() / within.mean(), across[uneven].mean() / within[uneven].mean())
    return ratios


def on_texels(covered, *points):
    """Whether all four texels a filtered read of each of *points* takes are real, in every set."""
    kept = np.ones(len(points[0]), bool)
    for each in points:
        for dx, dy in ((-0.5, -0.5), (0.5, -0.5), (-0.5, 0.5), (0.5, 0.5)):
            x = np.clip(np.floor(each[:, 0] + dx).astype(int), 0, covered.shape[1] - 1)
            y = np.clip(np.floor(each[:, 1] + dy).astype(int), 0, covered.shape[0] - 1)
            kept &= covered[y, x] >= 1.0
    return kept


def ratios_text(ratios):
    return ", ".join(f"{all_edges:.2f} and {uneven:.2f} at {distance:g}"
                     for distance, (all_edges, uneven) in ratios.items())


def stamps_at(x, y, angle, colors, owners=None):
    count = len(x)
    return plan.Stamps(x=np.asarray(x), y=np.asarray(y), brush=np.zeros(count, dtype=np.int64),
                       angle=np.asarray(angle, dtype=np.float64),
                       color=np.asarray(colors, dtype=np.float32),
                       owner=np.zeros(count, dtype=np.int32) if owners is None else np.asarray(owners))


def build_pixels(tree, node):
    return pixels(layer_build.build_layer(bpy.context, tree, node)).copy()


def without_islands(target, uv, tri_corners, tri_island, progress, margin=texel_map.MARGIN):
    """`texel_map.draw_islands` with every triangle left out, so no stamp is kept to an island."""
    return real_draw_islands(target, uv, tri_corners, np.zeros_like(tri_island), progress, margin)


def pair_mesh(name, tree, side):
    """Two separate quads, three texels apart in UV at *side*, as a mesh that uses *tree*.

    The left quad's texels end at column 460 and the right quad's start
    at column 464.
    """
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                      (2, 0, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0)], [], [(0, 1, 2, 3), (4, 5, 6, 7)])
    low, high, left, right = 0.05, 0.95, 461 / side, 464 / side
    uvs = [(low, low), (left, low), (left, high), (low, high),
           (right, low), (high, low), (high, high), (right, high)]
    mesh.uv_layers.new(name="UVMap").data.foreach_set('uv', np.array(uvs, np.float32).ravel())
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    use_tree(obj, tree)
    return obj


def index_of(snap):
    """The `seams.Index` of *snap*, with every unit run."""
    steps = seams.index_of(snap, None)
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return done.value


def island_texels(obj, tree, side):
    """The island map a build of *side* draws for *obj*, as a ``(side, side)`` array."""
    snap = seams.snapshot(obj, "UVMap", tree, bpy.context.evaluated_depsgraph_get())
    index = index_of(snap)
    target = gpu.types.GPUTexture((side, side), format='R32F')
    for _unit in real_draw_islands(target, snap.uv, snap.tri_corners,
                                   seams.triangle_islands(snap, index), None):
        pass
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))
    return gpu_core.read_color(framebuffer, side, side, channels=1)[..., 0]


def pixels(image):
    values = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(values)
    return values.reshape(image.size[1], image.size[0], 4)


def picture(side):
    """A smooth ramp with two hard-edged discs on it, opaque."""
    y, x = np.mgrid[0:side, 0:side] / side
    rgba = np.ones((side, side, 4), dtype=np.float32)
    rgba[..., 0] = x
    rgba[..., 1] = y
    rgba[..., 2] = 0.5
    for cx, cy, r, colour in ((0.3, 0.3, 0.18, (0.9, 0.2, 0.1)),
                              (0.7, 0.6, 0.22, (0.1, 0.3, 0.9))):
        rgba[(x - cx) ** 2 + (y - cy) ** 2 < r * r, :3] = colour
    return rgba


if available():
    try:
        rng = np.random.default_rng(3)

        section("the luma the direction is taken from")
        values = rng.random((24, 40, 4)).astype(np.float32)
        got = run_spec(painter_build.LUMA, values)
        luma = values[..., :3] @ np.array([0.2126, 0.7152, 0.0722])
        check(worst(got[..., 0], luma) < TOL and worst(got[..., 3], 1.0) == 0.0,
              "v2's weights, and opaque, so the blur after it is a plain one")

        section("the gradient")
        luma = rng.random((40, 56)).astype(np.float32)
        field = np.zeros((40, 56, 4), dtype=np.float32)
        field[..., 0] = luma
        got = run_spec(painter_build.SOBEL, field)
        gx, gy = sobel(luma)
        check(worst(got[..., 0], gx) < TOL and worst(got[..., 1], gy) < TOL,
              "the Sobel pass is v2's, edges clamped, with y pointing up the image")
        check(worst(got[..., 2], np.hypot(gx, gy)) < TOL, "and its magnitude beside it")

        y, x = np.mgrid[0:32, 0:32]
        ramp = np.zeros((32, 32, 4), dtype=np.float32)
        ramp[..., 0] = (x + y) / 64.0
        got = run_spec(painter_build.SOBEL, ramp)[8:24, 8:24]
        angle = np.arctan2(got[..., 1], got[..., 0])
        check(worst(angle, pi / 4) < 1e-4,
              "a picture brightening towards the upper right points its strokes at 45 degrees, "
              "which v2 turned to 135")

        section("the strongest edge")
        for shape, where in (((70, 100), (69, 99)), ((70, 100), (0, 0)),
                             ((9, 9), (8, 0)), ((1, 1), (0, 0))):
            field = np.zeros(shape + (4,), dtype=np.float32)
            field[..., 2] = rng.random(shape)
            field[where + (2,)] = 5.0
            got = painter_build._peak(upload(field))
            check(abs(got - 5.0) < 1e-6,
                  f"found by reduction over {shape[1]} by {shape[0]}, at {where} ({got})")

        section("reading the picture at the stamp centres")
        values = rng.random((48, 64, 4)).astype(np.float32)
        texture = upload(values)
        for count in (1, 1000, 1024):
            x = rng.integers(0, 64, count)
            y = rng.integers(0, 48, count)
            got = painter_build._gather((texture, texture), x, y)
            check(len(got) == 2 and got[0].shape == (count, 4)
                  and worst(got[0], values[y, x]) == 0.0 and worst(got[1], values[y, x]) == 0.0,
                  f"{count} centres read exactly, in the order they were drawn")
        corners = painter_build._gather((texture,), np.array([0, 63, 0, 63]),
                                        np.array([0, 0, 47, 47]))[0]
        check(worst(corners, values[[0, 0, 47, 47], [0, 63, 0, 63]]) == 0.0,
              "the corners of the picture included")

        section("one stamp")
        brush = rng.random((16, 16)).astype(np.float32)
        target, framebuffer = canvas(64)
        got = draw(framebuffer, 64, [brush], stamps_at([32], [32], [0.0], [(1.0, 1.0, 1.0, 1.0)]),
                   16, 16)
        check(worst(got[24:40, 24:40, 3], brush) < HALF_TOL,
              "unturned and at its own size, a stamp is its brush texel for texel")
        outside = got[..., 3].copy()
        outside[24:40, 24:40] = 0.0
        check(float(outside.max()) == 0.0, "and covers nothing past v2's square")

        bar = np.zeros((32, 32), dtype=np.float32)
        bar[14:18, :] = 1.0
        target, framebuffer = canvas(64)
        got = draw(framebuffer, 64, [bar], stamps_at([32], [32], [pi / 4], [(1.0, 1.0, 1.0, 1.0)]),
                   32, 32)[..., 3]
        check(got[40, 40] > 0.9 and got[24, 24] > 0.9 and got[23, 40] < 0.05
              and got[40, 23] < 0.05,
              "a bar turned by 45 degrees runs from the lower left to the upper right: "
              f"{got[40, 40]:.2f} and {got[24, 24]:.2f} on it, {got[23, 40]:.2f} "
              f"and {got[40, 23]:.2f} off it")

        solid = np.ones((8, 8), dtype=np.float32)
        target, framebuffer = canvas(32)
        got = draw(framebuffer, 32, [solid],
                   stamps_at([16, 16], [16, 16], [0.0, 0.0],
                             [(1.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.5, 0.5)]), 8, 8)
        check(worst(got[16, 16], (0.5, 0.0, 0.5, 1.0)) < HALF_TOL,
              "a later stamp goes over an earlier one, premultiplied "
              f"({np.round(got[16, 16], 3).tolist()})")

        section("a stamp keeps to its island")
        # Island 1 on the left with its margin, island 2 on the right, and
        # no island in the rows above. The stamp covers part of all four.
        islands = np.zeros((32, 32), dtype=np.float32)
        islands[:20, :14] = 1.0
        islands[:20, 14:16] = -1.0
        islands[:20, 16:] = 2.0
        square = np.zeros((32, 32), dtype=bool)
        square[8:24, 8:24] = True
        for owner, paints, says in (
                (1, (1.0, -1.0, 0.0),
                 "a stamp paints its own island, its margin and texels of none, not the island beside it"),
                (2, (2.0, 0.0), "whichever island it is on"),
                (0, (0.0,), "and one centred on no island paints only texels of none")):
            target, framebuffer = canvas(32)
            got = draw(framebuffer, 32, [solid],
                       stamps_at([16], [16], [0.0], [(1.0, 1.0, 1.0, 1.0)], [owner]), 16, 16, islands)[..., 3]
            want = square & np.isin(islands, paints)
            check(worst(got[want], 1.0) < HALF_TOL and float(got[~want].max()) == 0.0,
                  f"{says} ({int((got > 0.5).sum())} texels painted, {int(want.sum())} expected)")

        section("a stamp paints each texel once")
        # Island 1 everywhere, so the stamp's quad and two pieces carried
        # onto its own island, as across a seam of an island with itself,
        # all paint the same texels. The pieces read the brush's middle,
        # and carry a part of the stamp that is off the image.
        everywhere = np.ones((32, 32), dtype=np.float32)
        unit = np.array([0, 1, 1 + 1j, 1j])
        points = np.zeros((2, seams.CORNERS), complex)
        points[:, :4] = [4 + 4j + 16 * unit, 12 + 12j + 16 * unit]
        middle = np.where(np.arange(seams.CORNERS) < 4, 5 + 5j, 0) * np.ones((2, 1))
        away = np.full((2, seams.CORNERS), -64 - 64j)
        overlapping = seams.Pieces(stamp=np.array([0, 0]), points=points, coords=middle, sources=away,
                                   count=np.array([4, 4]), island=np.array([1, 1], np.int32))
        union = np.zeros((32, 32), dtype=bool)
        for low, high in ((8, 24), (4, 20), (12, 28)):
            union[low:high, low:high] = True
        half = (0.5, 0.5, 0.5, 0.5)
        target, framebuffer = canvas(32)
        got = draw(framebuffer, 32, [solid], stamps_at([16], [16], [0.0], [half], [1]), 16, 16, everywhere,
                   pieces=overlapping)[..., 3]
        check(worst(got[union], 0.5) < HALF_TOL and float(got[~union].max()) == 0.0,
              f"a stamp's quad and its pieces over it paint each texel once ({got.max():.4f} at most)")
        target, framebuffer = canvas(32, depth=False)
        got = draw(framebuffer, 32, [solid], stamps_at([16], [16], [0.0], [half], [1]), 16, 16, everywhere,
                   pieces=overlapping)[..., 3]
        check(float(got.max()) > 0.7,
              f"which takes the depth slots: without them, a texel under all three is painted three times "
              f"({got.max():.3f})")
        twice = seams.Pieces(stamp=np.array([0, 0, 1, 1]), points=np.concatenate([points, points]),
                             coords=np.concatenate([middle, middle]), sources=np.concatenate([away, away]),
                             count=np.full(4, 4), island=np.ones(4, np.int32))
        target, framebuffer = canvas(32)
        got = draw(framebuffer, 32, [solid], stamps_at([16, 16], [16, 16], [0.0, 0.0], [half, half], [1, 1]),
                   16, 16, everywhere, pieces=twice)[..., 3]
        check(worst(got[union], 0.75) < HALF_TOL,
              f"and the next stamp paints over it once more ({got[union].min():.4f} to {got[union].max():.4f})")
        # The brush's right half is empty, and a piece over the whole
        # quad reads its solid left half.
        left = np.zeros((16, 16), dtype=np.float32)
        left[:, :8] = 1.0
        points = np.zeros((1, seams.CORNERS), complex)
        points[0, :4] = 8 + 8j + 16 * unit
        covering = seams.Pieces(stamp=np.array([0]), points=points, coords=middle[:1] - 2, sources=away[:1],
                                count=np.array([4]), island=np.array([1], np.int32))
        target, framebuffer = canvas(32)
        got = draw(framebuffer, 32, [left], stamps_at([16], [16], [0.0], [half], [1]), 16, 16, everywhere,
                   pieces=covering)[..., 3]
        check(worst(got[8:24, 8:24], 0.5) < HALF_TOL,
              f"a texel the brush leaves empty is left for the stamp's other parts to paint "
              f"({got[8:24, 16:24].min():.4f} to {got[8:24, 16:24].max():.4f} there)")
        # Island 1 on the left, then its margin, then island 2 between two
        # margins of its own, then texels of none. A stamp of island 1 on
        # the left, and a piece of it over island 2 that carries the part
        # twelve texels to its left.
        islands = np.zeros((32, 32), dtype=np.float32)
        islands[:, :12] = 1.0
        islands[:, 12:16] = -1.0
        islands[:, 16:18] = -2.0
        islands[:, 18:28] = 2.0
        islands[:, 28:30] = -2.0
        points = np.zeros((1, seams.CORNERS), complex)
        points[0, :4] = 16 + 8j + np.array([0, 16, 16 + 16j, 16j])
        sources = np.where(np.arange(seams.CORNERS) < 4, points - 12, 0)
        across = seams.Pieces(stamp=np.array([0]), points=points, coords=middle[:1], sources=sources,
                              count=np.array([4]), island=np.array([2], np.int32))
        target, framebuffer = canvas(32)
        got = draw(framebuffer, 32, [solid], stamps_at([8], [16], [0.0], [(1.0, 1.0, 1.0, 1.0)], [1]), 16, 16,
                   islands, pieces=across)[..., 3]
        painted = np.zeros((32, 32), dtype=bool)
        painted[8:24, :18] = painted[8:24, 24:30] = True
        check(worst(got[painted], 1.0) < HALF_TOL and float(got[~painted].max()) == 0.0,
              f"a piece paints the far island's margin, not texels of none, and the island itself only where "
              f"the part it carries is off the stamp's own island, which the stamp's quad paints "
              f"({int((got > 0.5).sum())} texels painted, {int(painted.sum())} expected)")
        # The depth slots keep a stamp's parts apart only within one draw,
        # so a build cuts its stamps into draws between stamps, here of at
        # most four crossings each.
        chunk = painter_build.PLAN_CHUNK
        painter_build.PLAN_CHUNK = 4
        try:
            blocks = list(painter_build._blocks(6, np.repeat(np.arange(6), [0, 3, 9, 1, 0, 2])))
            # A stamp ends exactly at the chunk, and the next one past it.
            edge = list(painter_build._blocks(3, np.repeat(np.arange(3), [2, 3, 1])))
            alone = list(painter_build._blocks(5, np.zeros(0, np.int64)))
        finally:
            painter_build.PLAN_CHUNK = chunk
        check(blocks == [(0, 2, 0, 3), (2, 3, 3, 12), (3, 6, 12, 15)]
              and edge == [(0, 1, 0, 2), (1, 3, 2, 6)],
              f"draws hold whole stamps and at most the chunk of crossings, unless one stamp alone has more "
              f"({blocks}, {edge})")
        check(alone == [(0, 5, 0, 0)], f"and stamps that cross no seam are one draw ({alone})")

        section("a stamp carried across a seam")
        # The brush has a bar along its bottom and one down its right side,
        # so the part of it that crosses the seam shows which way it came
        # out and how far it reaches into B. A's shared edge is at x = 128.
        side, size = 512, 48
        shape = np.zeros((size, size), dtype=np.float32)
        shape[:10, :] = 1.0
        shape[:, 40:43] = 1.0
        image, origins = drawing.atlas([shape], size, 1, 1)
        corner = (122 - size // 2) + 1j * (76 - size // 2)
        y, x = np.mgrid[0:side, 0:side] + 0.5
        pair_tree = bpy.data.node_groups.new("Painter Seam", 'PaintSystemNodeTree')
        pair_tree.initialize()
        for name, (turn, flip, offset) in {"turned a quarter": (1j, 0, 0.8 + 0.2j),
                                           "mirrored": (0, -1, 1.0 + 0.5j),
                                           "twice as large": (2, 0, -0.05 + 0.4j)}.items():
            obj = seam_pair(f"Painter Seam {name}", pair_tree, turn, flip, offset)
            snap = seams.snapshot(obj, "UVMap", pair_tree, bpy.context.evaluated_depsgraph_get())
            crossings = seams.crossings(snap, index_of(snap), side, side)
            ids = island_texels(obj, pair_tree, side)
            target, framebuffer = canvas(side)
            got = draw(framebuffer, side, [shape], stamps_at([122], [76], [0.0], [(1.0, 1.0, 1.0, 1.0)], [1]),
                       size, size, ids, crossings=crossings)[..., 3]
            # Every texel of B and of its margin, carried back to where it
            # lies in A's texels when the mesh is unfolded, reads the brush
            # there.
            carried = (x + 1j * y) / side - offset
            unfolded = (np.conj(turn) * carried - flip * np.conj(carried)) / (abs(turn) ** 2 - abs(flip) ** 2)
            local = unfolded * side - corner
            # B's edge, and the side of it B is on.
            start, end, inner = (side * (offset + turn * uv + flip * np.conj(uv))
                                 for uv in (0.25 + 0.05j, 0.25 + 0.25j, 0.35 + 0.15j))
            normal = 1j * (end - start) / abs(end - start)
            normal *= np.sign((np.conj(normal) * (inner - start)).real)
            band = (np.conj(normal) * (x + 1j * y - start)).real >= -seams.BAND
            within = (local.real >= 0) & (local.real <= size) & (local.imag >= 0) & (local.imag <= size)
            b_texels = np.abs(ids) == 2
            results = {}
            for label, brush in (("", image), ("upside down", image[::-1])):
                want = np.where(band & within, bilinear(brush, local.real + origins[0, 0],
                                                        local.imag + origins[0, 1]), 0.0)
                inked, wanted = b_texels & (got > 0.5), b_texels & (want > 0.5)
                results[label] = ((inked & wanted).sum() / max(1, (inked | wanted).sum()),
                                  worst(got[b_texels], want[b_texels]))
            overlap, error = results[""]
            check(overlap > 0.95 and error < 0.05 and results["upside down"][0] < 0.5,
                  f"B {name}: the stamp runs on into B as the unfolded mesh shows it (overlap {overlap:.3f}, "
                  f"off by {error:.3f} at most, and {results['upside down'][0]:.2f} with the brush upside down)")

        section("a Painterly layer")
        tree = bpy.data.node_groups.new("Painter", 'PaintSystemNodeTree')
        tree.initialize()
        source = create_managed_image("Painter Source", SIZE, SIZE)
        flat = np.tile(np.array([0.4, 0.6, 0.2, 1.0], dtype=np.float32), (SIZE, SIZE, 1))
        source.pixels.foreach_set(flat.ravel())
        source.update()
        # The painter needs the mesh it paints for, for the UV seams its
        # strokes are to follow. A plane whose UV map covers the whole
        # image has none.
        plane = bake_plane()
        use_tree(plane, tree)
        with core.suspend_compile(tree):
            layer = tree.insert_layer_node(IMAGE)
            layer.image = source
            node = tree.insert_layer_node(FILTER)
            node.filter_type = 'PAINTERLY'
            node.resolution = str(SIZE)
            node.surface_name = plane.name
        core.flush_now()

        labels = []
        run = layer_build.steps(bpy.context, tree, node)
        while True:
            try:
                labels.append(next(run))
            except StopIteration as done:
                built = done.value
                break
        fractions = [fraction for _label, fraction in labels]
        check(all(a <= b for a, b in zip(fractions, fractions[1:]))
              and 0.0 <= fractions[0] and fractions[-1] <= 1.0,
              f"the progress only moves forward ({len(labels)} units)")
        names = {label for label, _fraction in labels}
        check({"Painterly: planning the strokes", "Painterly: reading the UV seams",
               "Painterly: reading the picture", "Painterly: painting, step 1 of 4",
               "Painterly: painting, step 4 of 4", "Painterly: finishing"} <= names,
              "and says which step of the painting it is on")
        check(worst(pixels(built), flat) <= BYTE_TOL,
              "a flat picture paints to itself: every stroke picks up the colour it lands on")

        detailed = picture(SIZE)
        source.pixels.foreach_set(detailed.ravel())
        source.update()
        first = pixels(layer_build.build_layer(bpy.context, tree, node)).copy()
        uploaded = {key: entry[0]
                    for key, entry in painter_build._atlases[node.painter.brush].items()}
        second = pixels(layer_build.build_layer(bpy.context, tree, node))
        check(bool(np.array_equal(first, second)), "the same settings paint the same pixels twice")
        kept = painter_build._atlases[node.painter.brush]
        check(uploaded and all(kept[key][0] is texture for key, texture in uploaded.items()),
              f"the second time with the {len(uploaded)} atlases the first one uploaded")
        moved = np.abs(first - detailed).max(axis=2) > BYTE_TOL
        check(float(moved.mean()) > 0.05,
              f"and they are painted: {moved.mean():.0%} of the texels moved off the picture")
        texel_map.draw_islands = without_islands
        try:
            unkept = build_pixels(tree, node)
        finally:
            texel_map.draw_islands = real_draw_islands
        check(bool(np.array_equal(unkept, first)),
              "the plane is one island covering the image, so keeping strokes to it changes no texel")

        node.painter.seed = 7
        core.flush_now()
        reseeded = pixels(layer_build.build_layer(bpy.context, tree, node))
        check(not np.array_equal(reseeded, first), "another seed paints other strokes")
        node.painter.seed = 42

        # The ramp's gradient is a small fraction of the discs' edges, so a
        # threshold between the two keeps only the strokes on the discs.
        # Nothing kept at all would pass a peak read as infinite, which is
        # why the lower bound is there.
        node.painter.edge_threshold = 30.0
        core.flush_now()
        edged = pixels(layer_build.build_layer(bpy.context, tree, node))
        few = np.abs(edged - detailed).max(axis=2) > BYTE_TOL
        check(0.0 < float(few.mean()) < float(moved.mean()) / 2,
              f"a threshold leaves out the strokes off the strong edges "
              f"({few.mean():.1%} moved, {moved.mean():.1%} without it)")
        node.painter.edge_threshold = 0.0

        section("a GPU out of memory")
        # Failing one format at a time reaches the painter's own textures:
        # the stamp centres it gathers at (RGBA32F), the brush atlas
        # (R16F), the island map (R32F) and the stamps' depth slots
        # (DEPTH_COMPONENT32F). Each failure has to come back as the
        # Refused the operators report, not as a bare RuntimeError.
        real_texture = gpu.types.GPUTexture
        for failing in ('RGBA32F', 'R16F', 'R32F', 'DEPTH_COMPONENT32F'):
            def allocate(size, *args, failing=failing, **kwargs):
                if kwargs.get('format') == failing:
                    raise RuntimeError("GPUTexture: texture creation failed")
                return real_texture(size, *args, **kwargs)

            # Dropping the kept atlases makes the build upload them again.
            painter_build.release()
            gpu.types.GPUTexture = allocate
            try:
                layer_build.build_layer(bpy.context, tree, node)
                message = None
            except filters_core.Refused as error:
                message = str(error)
            finally:
                gpu.types.GPUTexture = real_texture
            check(message is not None and "enough memory" in message,
                  f"a failed {failing} allocation refuses by name ({message})")

        clear = np.zeros((SIZE, SIZE, 4), dtype=np.float32)
        source.pixels.foreach_set(clear.ravel())
        source.update()
        core.flush_now()
        empty = pixels(layer_build.build_layer(bpy.context, tree, node))
        check(float(empty[..., 3].max()) == 0.0,
              "a transparent stack stays transparent: no stroke is placed where there is nothing")

        section("strokes keep to their own UV island")
        # Each island is painted a flat colour of its own, into its
        # margin, as a baked texture is. With no smoothing each stroke
        # takes the colour under its centre, which is its island's. So a
        # texel of one island that shows another colour was painted from
        # across the gap.
        pair = pair_mesh("Painter Pair", tree, SIZE)
        smoothing = node.painter.smoothing
        node.surface_name = pair.name
        node.painter.smoothing = 0.0
        ids = np.abs(island_texels(pair, tree, SIZE)).astype(int)
        colours = np.array([(0.5, 0.5, 0.5, 1.0), (0.9, 0.1, 0.1, 1.0), (0.1, 0.1, 0.9, 1.0)],
                           dtype=np.float32)[ids]
        source.pixels.foreach_set(colours.ravel())
        source.update()
        core.flush_now()
        real = read_texel_map(texel_map.get_texel_map(pair, "UVMap", (SIZE, SIZE)))[..., 3] > 0.75
        check(set(np.unique(ids[real]).tolist()) == {1, 2} and float(real.mean()) > 0.5,
              f"the two quads are two islands ({float(real.mean()):.0%} of the texels)")

        def foreign(painted):
            return int((real & (np.abs(painted - colours).max(axis=2) > BYTE_TOL)).sum())

        kept = build_pixels(tree, node)
        past = int(((ids == 0) & (np.abs(kept - colours).max(axis=2) > BYTE_TOL)).sum())
        check(foreign(kept) == 0 and past > 0,
              f"no texel of either island takes the other's colour ({foreign(kept)} do), "
              f"while strokes still reach past their island onto {past} texels of none")
        texel_map.draw_islands = without_islands
        try:
            unkept = build_pixels(tree, node)
        finally:
            texel_map.draw_islands = real_draw_islands
        check(foreign(unkept) > 0,
              f"kept to no island, strokes paint {foreign(unkept)} texels across the gap")
        # The margins painted yellow: only a stroke centred in a margin
        # takes that colour, and it is that island's stroke, so it paints
        # the island's edge.
        signed = island_texels(pair, tree, SIZE)
        edged = np.where((signed < 0)[..., None], np.array([0.9, 0.9, 0.1, 1.0], np.float32), colours)
        source.pixels.foreach_set(edged.ravel())
        source.update()
        core.flush_now()
        yellow = int(((signed > 0) & (build_pixels(tree, node)[..., 1] > 0.6)).sum())
        source.pixels.foreach_set(colours.ravel())
        source.update()
        core.flush_now()
        check(yellow > 0, f"a stroke centred in an island's margin paints the island ({yellow} texels)")

        section("a mesh that enters Edit Mode while its layer builds")
        # The build copied the mesh when it started, so it paints what it
        # copied, whatever happens to the mesh after that.
        run = layer_build.steps(bpy.context, tree, node)
        next(run)
        bpy.context.view_layer.objects.active = pair
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            edited = bmesh.from_edit_mesh(pair.data)
            uv_layer = edited.loops.layers.uv["UVMap"]
            for face in edited.faces:
                for loop in face.loops:
                    loop[uv_layer].uv = loop[uv_layer].uv * 0.5
            bmesh.update_edit_mesh(pair.data)
            try:
                while True:
                    next(run)
            except StopIteration as done:
                during, message = pixels(done.value).copy(), ""
            except filters_core.Refused as error:
                during, message = None, str(error)
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
        check(during is not None and bool(np.array_equal(during, kept)),
              f"the build finishes with the pixels it would have painted anyway {message!r}")

        section("the UV map the islands come from")
        # The layer below names its map, and the mesh renders with another
        # one, which folds the right quad onto the left.
        layer.uv_map = "UVMap"
        folded = pair.data.uv_layers.new(name="Folded")
        folded.active_render = True
        named = np.empty(len(pair.data.loops) * 2, np.float32)
        pair.data.uv_layers["UVMap"].data.foreach_get('uv', named)
        folded.data.foreach_set('uv', np.concatenate([named[:8], named[:8]]))
        core.flush_now()
        inputs = painter_build.read_inputs(node, layer_plan.resolve_input(bpy.context, tree, node))
        check(bool(np.array_equal(inputs.snapshot.uv.ravel(), named)),
              "the islands are those of the map the layers below use, not the one the mesh renders with")
        pair.data.uv_layers.remove(folded)
        layer.uv_map = ""
        core.flush_now()

        section("strokes run on across seams")
        # Suzanne's head, ears and eyes are five islands. The seams run
        # round the back of the head and where the ears join it, and the
        # eyes join nothing. A picture that changes smoothly over the mesh
        # should change as little across a seam, once painted, as it does
        # within an island. The points either side come from bmesh.
        bpy.ops.mesh.primitive_monkey_add()
        monkey = bpy.context.active_object
        use_tree(monkey, tree)
        seam_source = create_managed_image("Painter Seams", SEAM_SIZE, SEAM_SIZE)
        layer.image = seam_source
        node.surface_name = monkey.name
        node.resolution = str(SEAM_SIZE)
        node.painter.smoothing = smoothing
        found = read_texel_map(texel_map.get_texel_map(monkey, "UVMap", (SEAM_SIZE, SEAM_SIZE)))
        world, covered = found[..., :3], found[..., 3].copy()
        smooth = np.ones((SEAM_SIZE, SEAM_SIZE, 4), dtype=np.float32)
        for channel in range(3):
            smooth[..., channel] = 0.5 + 0.45 * np.sin(5.0 * world[..., channel] + 0.3 + channel)
        found = world = None
        samples = {distance: seam_samples(monkey.data, "UVMap", SEAM_SIZE, distance) for distance in (1.5, 6.0, 12.0)}
        real_pieces = seams.pieces

        def painted_over(picture, carried=True):
            """The layer built over *picture*, with or without the pieces carried across seams."""
            seam_source.pixels.foreach_set(picture.ravel())
            seam_source.update()
            core.flush_now()
            if not carried:
                seams.pieces = (lambda crossings, corners, coords, stamp, crossing:
                                real_pieces(crossings, corners, coords, stamp[:0], crossing[:0]))
            try:
                return build_pixels(tree, node)
            finally:
                seams.pieces = real_pieces

        painted = painted_over(smooth)
        chunk = painter_build.PLAN_CHUNK
        painter_build.PLAN_CHUNK = 64
        try:
            cut = build_pixels(tree, node)
        finally:
            painter_build.PLAN_CHUNK = chunk
        check(bool(np.array_equal(cut, painted)),
              "a build cut into draws of far fewer crossings paints the same pixels")
        cut = None
        ratios = continuity(painted, samples, covered)
        apart = continuity(painted_over(smooth, carried=False), samples, covered)
        check(all(0.6 <= each <= 1.6 for each, _ in ratios.values())
              and all(0.6 <= each <= 1.7 for _, each in ratios.values()),
              f"the painting changes about as much across the seams as within an island, over all edges "
              f"and the uneven ones ({ratios_text(ratios)}; the picture itself "
              f"{ratios_text(continuity(smooth, samples, covered))})")
        check(apart[1.5][0] > 3.0,
              f"kept to their islands without carrying across, strokes change {apart[1.5][0]:.2f} times as "
              f"much right at the seams")
        real = covered >= 1.0
        moved = float(np.abs(painted[..., :3] - smooth[..., :3]).mean(axis=2)[real].mean())
        check(moved > 0.01, f"and the painting is not the picture below itself (off by {moved:.3f} on average)")

        # Each kind of island painted a pure colour of its own, into its
        # margin, and painted with no smoothing, so each stroke takes its
        # own island's colour: the head red, the ears green, the eyes blue.
        node.painter.smoothing = 0.0
        index = index_of(seams.snapshot(monkey, "UVMap", tree, bpy.context.evaluated_depsgraph_get()))
        faces = np.bincount(index.face_island)
        palette = np.zeros((len(faces), 4), dtype=np.float32)
        palette[:, 3] = 1.0
        for island, count in enumerate(faces[1:], 1):
            palette[island, {318: 0, 59: 1, 32: 2}[int(count)]] = 1.0
        colours = palette[np.abs(island_texels(monkey, tree, SEAM_SIZE)).astype(int)]
        here, there = samples[1.5][:2]
        between = on_texels(covered, here, there)
        own, other = (bilinear(colours[..., :3], each[:, 0], each[:, 1]) for each in (here, there))
        between &= np.abs(own - other).max(axis=1) > 0.5
        for carried in (True, False):
            flat = painted_over(colours, carried)
            painted_here, painted_there = (bilinear(flat[..., :3], each[between, 0], each[between, 1])
                                           for each in (here, there))
            crossed = ((np.abs(painted_here - other[between]).sum(axis=1)
                        < np.abs(painted_here - own[between]).sum(axis=1))
                       | (np.abs(painted_there - own[between]).sum(axis=1)
                          < np.abs(painted_there - other[between]).sum(axis=1)))
            if carried:
                eyes = real & (colours[..., 2] == 1.0)
                bled = int((eyes & (flat[..., :2].max(axis=2) > BYTE_TOL)).sum())
                check(crossed.mean() >= 0.2 and bled == 0,
                      f"strokes carry their island's colour across: at {crossed.mean():.0%} of the points "
                      f"along the seams between the head and the ears, one 1.5 texels in on either side "
                      f"shows the other side's colour, and none of the {int(eyes.sum())} texels of the eyes, "
                      f"which join nothing, shows the head's or the ears' ({bled} do)")
            else:
                check(crossed.mean() < 0.05,
                      f"which they do not when kept to their islands ({crossed.mean():.0%})")
        painted = flat = colours = smooth = covered = real = None
        layer.image = source
        node.resolution = str(SIZE)
        node.surface_name = plane.name
        node.painter.smoothing = smoothing

        section("at 4K")
        source.pixels.foreach_set(picture(SIZE).ravel())
        source.update()
        node.resolution = '4096'
        core.flush_now()
        start = time.perf_counter()
        layer_build.build_layer(bpy.context, tree, node)
        took = time.perf_counter() - start
        limit = BUDGET_4K * perf_scale()
        check(took < limit, f"the default settings paint in {took:.2f} s (limit {limit:.0f} s)")

    except Exception:
        traceback.print_exc()
        check(False, "unexpected exception")

# Python's own teardown would free them after the GPU context has gone,
# which segfaults a background Blender. The textures made above are
# module globals, so they go too.
target = framebuffer = texture = uploaded = kept = None
import_from("filters.composite").release()
import_from("filters.blend_glsl").release()
filters_core.release()
painter_build.release()
texel_map.release()

finish("FILTER PAINTER TEST")
