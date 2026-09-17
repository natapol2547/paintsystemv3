"""The texel position map rasterises a mesh into UV space (PS-092).

These need a GPU context. Blender 5.2 added `gpu.init()`, which builds one
in background mode from EGL without a display, so they run in the ordinary
headless job there. Background 4.2 to 5.1 have no way to get a context and
every test here skips; that coverage comes from the windowed job.
"""
import os
import sys
import time

import bpy
import gpu
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, guarded, import_from, register_addon, section, skip  # noqa: E402

register_addon()
core = import_from("gpu_passes.core")
texel_map = import_from("gpu_passes.texel_map")
surface = import_from("gpu_passes.surface")

SIZE = 128
CUBE = "PS Texel Cube"


def cube():
    """A factory cube at the origin, made once."""
    obj = bpy.data.objects.get(CUBE)
    if obj is not None:
        return obj
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    obj.name = CUBE
    obj.data.name = CUBE
    return obj


def face_maps(obj):
    """Per face: the exact UV to world map, its UV bounds and its world normal.

    Each face is planar and its UVs are affine in the surface, so three
    corners determine where any UV inside the island sits in world space.
    That is the ground truth the rasteriser has to reproduce, and it
    covers every texel of the island rather than one sample.
    """
    mesh = obj.data
    uv = mesh.uv_layers.active.uv
    matrix = obj.matrix_world
    out = []
    for polygon in mesh.polygons:
        corner_uvs = [tuple(uv[i].vector) for i in polygon.loop_indices]
        loops = list(polygon.loop_indices)[:3]
        uv_matrix = np.array([[*uv[i].vector, 1.0] for i in loops], dtype=np.float64)
        world = np.array([list(matrix @ mesh.vertices[mesh.loops[i].vertex_index].co)
                          for i in loops], dtype=np.float64)
        bounds = (min(c[0] for c in corner_uvs), min(c[1] for c in corner_uvs),
                  max(c[0] for c in corner_uvs), max(c[1] for c in corner_uvs))
        normal = (matrix.to_3x3().inverted_safe().transposed()
                  @ polygon.normal).normalized()
        out.append((np.linalg.solve(uv_matrix, world), bounds, np.array(normal)))
    return out


def texel(array, u, v):
    """The texel of a `(height, width, 4)` read back nearest UV `(u, v)`."""
    height, width = array.shape[:2]
    x = min(int(u * width), width - 1)
    y = min(int(v * height), height - 1)
    return array[y, x]


def texel_centre_uvs(size):
    """The UV of every texel centre, as two `(size, size)` arrays."""
    ys, xs = np.mgrid[0:size, 0:size]
    return (xs + 0.5) / size, (ys + 0.5) / size


def dilate(mask, steps):
    """*mask* grown by *steps* texels in every direction, 8-connected."""
    grown = mask.copy()
    for _ in range(steps):
        padded = np.pad(grown, 1, constant_values=False)
        grown = np.zeros_like(grown)
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                grown |= padded[dy:dy + mask.shape[0], dx:dx + mask.shape[1]]
    return grown


def available():
    if core.gpu_available():
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


def test_availability():
    section("whether this session can draw")
    ready = core.gpu_available()
    expected = not bpy.app.background or hasattr(gpu, 'init')
    check(ready == expected,
          f"gpu_available() is {ready}, expected {expected} on "
          f"{bpy.app.version_string} background={bpy.app.background}")
    if ready:
        # CI sets this on the Vulkan job: without an ICD Blender falls back
        # to OpenGL quietly, and the job would pass without Vulkan coverage.
        expected_backend = os.environ.get("PS_EXPECT_GPU_BACKEND")
        if expected_backend:
            check(gpu.platform.backend_type_get() == expected_backend,
                  f"the GPU backend is {gpu.platform.backend_type_get()}, expected {expected_backend}")
    else:
        check(texel_map.get_texel_map(cube(), "", (64, 64)) is None,
              "get_texel_map returns None instead of raising when it cannot draw")


def test_texels_match_the_surface():
    section("every covered texel holds its own point on the surface")
    if not available():
        return
    obj = cube()
    built = texel_map.build_texel_map(obj, "", SIZE, SIZE)
    check(built is not None, "the map was built")
    if built is None:
        return
    positions = built.positions()
    normals = built.normals()
    covered = positions[..., 3] > 0.75
    us, vs = texel_centre_uvs(SIZE)

    worst_position = 0.0
    worst_normal = 0.0
    tested = 0
    islands = 0
    for affine, (u0, v0, u1, v1), normal in face_maps(obj):
        # Step in off the island edge, where a texel is only part covered
        # and its centre can sit outside the triangle.
        pad = 1.5 / SIZE
        inside = ((us >= u0 + pad) & (us <= u1 - pad)
                  & (vs >= v0 + pad) & (vs <= v1 - pad) & covered)
        if not inside.any():
            continue
        islands += 1
        tested += int(inside.sum())
        uvw = np.stack([us[inside], vs[inside], np.ones(int(inside.sum()))], axis=1)
        expected = uvw @ affine
        worst_position = max(worst_position,
                             float(np.abs(expected - positions[inside][:, :3]).max()))
        worst_normal = max(worst_normal,
                           float(np.abs(normals[inside][:, :3] - normal).max()))

    check(islands == len(obj.data.polygons),
          f"all {len(obj.data.polygons)} faces have an island in the map, {islands} found")
    check(tested > 1000, f"{tested} texels checked against the exact surface")
    check(worst_position < 1e-3,
          f"world position is within 1e-3 of the surface (worst {worst_position:.2e})")
    check(worst_normal < 1e-2, f"world normal is within 1e-2 (worst {worst_normal:.2e})")


def test_margin_extends_islands():
    section("the margin extends islands and stops")
    if not available():
        return
    obj = cube()
    bare = texel_map.build_texel_map(obj, "", SIZE, SIZE, margin=0)
    grown = texel_map.build_texel_map(obj, "", SIZE, SIZE, margin=texel_map.MARGIN)
    if bare is None or grown is None:
        check(False, "both maps were built")
        return

    islands = bare.positions()[..., 3] > 0.75
    grown_alpha = grown.positions()[..., 3]
    grown_islands = grown_alpha > 0.75
    margin = (grown_alpha > 0.0) & ~grown_islands

    check(islands.any() and not islands.all(),
          f"the cube covers part of UV space ({int(islands.sum())} of {SIZE * SIZE} texels)")
    check(np.array_equal(islands, grown_islands),
          "drawing the margin does not change which texels are real surface")
    check(margin.any(), f"the margin covers {int(margin.sum())} texels")
    check(np.isclose(grown_alpha[margin], texel_map.MARGIN_COVERAGE).all(),
          f"every margin texel has alpha {texel_map.MARGIN_COVERAGE}")

    reachable = dilate(islands, texel_map.MARGIN)
    check(not (margin & ~reachable).any(),
          f"no margin texel is further than {texel_map.MARGIN} texels from an island "
          f"({int((margin & ~reachable).sum())} are)")
    check(not (grown_alpha > 0.0).all(), "texels beyond the margin keep coverage 0")

    # A margin texel continues its own triangle, so it stays on the cube.
    filled = grown.positions()[margin][:, :3]
    check(np.abs(filled).max() < 1.5,
          f"margin positions stay near the cube surface (max |coordinate| "
          f"{float(np.abs(filled).max()):.3f}, the cube reaches 1.0)")


def test_transform_is_applied():
    section("the map follows the object transform")
    if not available():
        return
    obj = cube()
    before = texel_map.build_texel_map(obj, "", SIZE, SIZE)
    obj.location = (5.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    after = texel_map.build_texel_map(obj, "", SIZE, SIZE)
    obj.location = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    if before is None or after is None:
        check(False, "both maps were built")
        return

    u0, v0, u1, v1 = face_maps(obj)[0][1]
    centroid = ((u0 + u1) / 2, (v0 + v1) / 2)
    moved = texel(after.positions(), *centroid) - texel(before.positions(), *centroid)
    check(abs(moved[0] - 5.0) < 1e-3 and abs(moved[1]) < 1e-3 and abs(moved[2]) < 1e-3,
          f"positions moved by exactly the object's 5 units in x {tuple(round(float(v), 4) for v in moved[:3])}")


def test_cache():
    section("the cache reuses and invalidates")
    if not available():
        return
    obj = cube()
    texel_map.invalidate()
    first = texel_map.get_texel_map(obj, "", (64, 64))
    second = texel_map.get_texel_map(obj, "", (64, 64))
    check(first is not None and first is second, "the same arguments return the same map")
    check(texel_map.cached_count() == 1, f"one map is cached, not {texel_map.cached_count()}")

    other_size = texel_map.get_texel_map(obj, "", (32, 32))
    check(other_size is not first, "a different size is a different map")

    obj.location = (2.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    moved = texel_map.get_texel_map(obj, "", (64, 64))
    check(moved is not first, "moving the object gives a new map even without the handler")
    obj.location = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()

    texel_map.invalidate(obj.session_uid)
    check(texel_map.cached_count() == 0,
          f"invalidating the object empties the cache, {texel_map.cached_count()} left")


def test_cache_follows_surface_content():
    section("the cache follows the surface's content, not its update events")
    if not available():
        return
    obj = cube()
    bpy.context.view_layer.objects.active = obj
    texel_map.invalidate()
    first = texel_map.get_texel_map(obj, "", (64, 64))
    obj.data.update()
    bpy.context.view_layer.update()
    check(texel_map.get_texel_map(obj, "", (64, 64)) is first and texel_map.cached_count() == 1,
          f"a geometry update that changes nothing keeps the map ({texel_map.cached_count()} cached)")

    bpy.ops.ed.undo_push(message="before the vertex move")
    obj.data.vertices[0].co.z += 0.5
    obj.data.update()
    bpy.ops.ed.undo_push(message="vertex move")
    bpy.context.view_layer.update()
    moved = texel_map.get_texel_map(obj, "", (64, 64))
    changed = int((np.abs(moved.positions() - first.positions()) > 1e-4).any(-1).sum()) if moved is not None else 0
    check(moved is not None and moved is not first and changed > 0,
          f"moving a vertex builds a new map with moved positions ({changed} texels differ)")

    bpy.ops.ed.undo()
    bpy.context.view_layer.update()
    obj = cube()
    undone = texel_map.get_texel_map(obj, "", (64, 64))
    fresh = texel_map.build_texel_map(obj, "", 64, 64)
    check(undone is first, "undoing the move finds the map built before it")
    check(undone is not None and fresh is not None and np.array_equal(undone.positions(), fresh.positions()),
          "which matches a fresh build pixel for pixel")

    count = texel_map.cached_count()
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        in_edit = texel_map.get_texel_map(obj, "", (64, 64))
        check(in_edit is not None and texel_map.cached_count() == count,
              f"in Edit Mode a map is built and not cached ({texel_map.cached_count()} cached, {count} before)")
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')

    check(texel_map.get_texel_map(obj, "PS Texel Missing", (64, 64), fallback_to_active=False) is None,
          "a missing UV map without the fallback gives None")
    check(texel_map.get_texel_map(obj, "PS Texel Missing", (64, 64)) is not None,
          "and with it falls back to the active UV map")
    texel_map.invalidate()


def test_position_batch():
    section("the depth batch shares the triangle soup with the map")
    if not available():
        return
    obj = cube()
    texel_map.invalidate()
    calls = []
    extract = texel_map._triangle_arrays

    def counting_triangle_arrays(*args, **kwargs):
        calls.append(args[1])
        return extract(*args, **kwargs)

    texel_map._triangle_arrays = counting_triangle_arrays
    try:
        texel_map.get_texel_map(obj, "", (64, 64), fallback_to_active=False)
        batch = texel_map.get_position_batch(obj, "")
        check(batch is not None and len(calls) == 1,
              f"a batch after a map miss in the same build extracts once ({len(calls)} extractions)")
        check(texel_map.cached_batch_count() == 1 and texel_map.cached_count() == 1,
              f"one batch and one map are cached ({texel_map.cached_batch_count()}, {texel_map.cached_count()})")
        texel_map._forget_pending_arrays()
        check(texel_map.get_position_batch(obj, "") is batch and len(calls) == 1,
              f"a later call is a hit ({len(calls)} extractions)")
        obj.location = (1.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        try:
            moved = texel_map.get_position_batch(obj, "")
            check(moved is not None and moved is not batch and texel_map.cached_batch_count() == 2,
                  f"moving the object gives a new batch ({texel_map.cached_batch_count()} cached)")
        finally:
            obj.location = (0.0, 0.0, 0.0)
            bpy.context.view_layer.update()
        check(texel_map.get_position_batch(obj, "PS Texel Missing") is None,
              "a missing UV map gives no batch")
        texel_map.invalidate()
        calls.clear()
        texel_map.get_position_batch(obj, "")
        texel_map.get_texel_map(obj, "", (32, 32), fallback_to_active=False)
        check(len(calls) == 1 and texel_map.cached_count() == 1,
              f"so does a map after a batch miss ({len(calls)} extractions)")
    finally:
        texel_map._triangle_arrays = extract
        texel_map.invalidate()
    check(texel_map.cached_batch_count() == 0, "invalidate drops the batches too")


def test_cache_budget():
    section("the cache stays inside its video memory budget")
    if not available():
        return
    obj = cube()
    texel_map.invalidate()
    budget = texel_map.CACHE_BUDGET
    try:
        # Two 64x64 maps are 786 kB together; allow room for one.
        texel_map.CACHE_BUDGET = 64 * 64 * 24 + 1
        first = texel_map.get_texel_map(obj, "", (64, 64))
        texel_map.get_texel_map(obj, "", (64, 64), tile=1002)
        check(texel_map.cached_count() == 1,
              f"the older map was evicted, {texel_map.cached_count()} cached")
        check(texel_map.get_texel_map(obj, "", (64, 64)) is not first,
              "the evicted map is rebuilt on the next request")
    finally:
        texel_map.CACHE_BUDGET = budget
        texel_map.invalidate()


def test_udim_tile():
    section("a UDIM tile draws only the UVs inside it")
    if not available():
        return
    obj = cube()
    layer = obj.data.uv_layers.active
    original = np.empty(len(layer.uv) * 2, dtype=np.float32)
    layer.uv.foreach_get('vector', original)
    try:
        shifted = original.copy()
        shifted[0::2] += 1.0  # every u into the 1002 tile
        layer.uv.foreach_set('vector', shifted)
        obj.data.update()
        bpy.context.view_layer.update()

        in_tile = texel_map.build_texel_map(obj, "", SIZE, SIZE, tile=1002)
        out_of_tile = texel_map.build_texel_map(obj, "", SIZE, SIZE, tile=1001)
        if in_tile is None or out_of_tile is None:
            check(False, "both maps were built")
            return
        check((in_tile.positions()[..., 3] > 0.75).any(), "tile 1002 holds the shifted UVs")
        check(not (out_of_tile.positions()[..., 3] > 0.0).any(),
              f"tile 1001 is empty, "
              f"{int((out_of_tile.positions()[..., 3] > 0.0).sum())} texels covered")
    finally:
        layer.uv.foreach_set('vector', original)
        obj.data.update()
        bpy.context.view_layer.update()
        texel_map.invalidate()


def test_cost():
    section("cost of a 4K map")
    if not available():
        return
    obj = cube()
    start = time.perf_counter()
    built = texel_map.build_texel_map(obj, "", 4096, 4096)
    elapsed = 1000 * (time.perf_counter() - start)
    if built is None:
        check(False, "the 4K map was built")
        return
    print(f"  4096x4096 map on {gpu_renderer()}: {elapsed:.0f} ms, "
          f"{built.video_memory / (1 << 20):.0f} MB")
    check(elapsed < 2000, f"building a 4K map takes {elapsed:.0f} ms")

    start = time.perf_counter()
    array = built.positions()
    read = 1000 * (time.perf_counter() - start)
    print(f"  4096x4096 RGBA32F read back: {read:.0f} ms")
    check(array.shape == (4096, 4096, 4), f"the read back is shaped {array.shape}")


def gpu_renderer():
    try:
        return f"{gpu.platform.backend_type_get()} {gpu.platform.renderer_get()}"
    except SystemError:
        return "unknown"


for test in (test_availability,
             test_texels_match_the_surface,
             test_margin_extends_islands,
             test_transform_is_applied,
             test_cache,
             test_cache_follows_surface_content,
             test_position_batch,
             test_cache_budget,
             test_udim_tile,
             test_cost):
    guarded(test)

# Give the textures back while the GPU context is still up. Python frees
# them at interpreter shutdown otherwise, which is after the context has
# gone, and freeing a texture there segfaults Blender.
texel_map.release()
surface.release()

finish("TEXEL MAP TEST")
