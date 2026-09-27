"""The UV islands the painter keeps its strokes to, checked without a GPU (PS-053).

`filters.painter.seams` is plain numpy over a copy of the mesh, so it is
checked on small meshes whose islands are known, and on Suzanne against
a walk over the same mesh in bmesh. The rest is what a later change
could quietly undo:

- only an edge used by exactly two faces that agree on the UVs at both
  of its ends joins them, whichever way each face runs along it;
- a face whose material does not show the tree is in no island, and
  joins nothing, so it can split an island in two;
- the islands are numbered from the mesh alone;
- the islands are read off the evaluated mesh and its materials, as it
  renders;
- the cache key ignores positions, but not the exact UVs, faces or
  materials;
- a crossing carries a point across a seam exactly where the mesh,
  unfolded flat, puts it, for any linear map between the two sides'
  UVs, and follows the mesh as it is posed;
- crossings that cannot be drawn are left out, crossings stacked in UV
  are kept once, and crossings that follow on along a seam split a
  stamp between them at the line halving the angle of their far edges;
- a stamp crosses an edge only from its own side, and from as far off
  as the band past the far edge reaches;
- the cuts, the stamps' candidate crossings and the third corners agree
  with a brute-force reference.

`tests/test_filter_painter.py` checks that strokes stay on their island
and run on across seams without a break.
"""
import os
import sys
import traceback
from dataclasses import replace
from math import pi, sqrt

import bmesh
import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, import_from, register_addon, section, use_tree  # noqa: E402

register_addon()
seams = import_from("filters.painter.seams")

# Two unit quads side by side, sharing the edge from vertex 1 to vertex 4.
#
#   3 --- 4 --- 5
#   |  A  |  B  |
#   0 --- 1 --- 2
POINTS = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0), (1, 1, 0), (2, 1, 0)]
A = (0, 1, 4, 3)
B = (1, 2, 5, 4)
# B the other way round, so it runs along the shared edge the same way as A.
B_FLIPPED = (1, 4, 5, 2)


def uvs_at(face, offset=(0.0, 0.0)):
    """Each corner's UV, the vertex's own x and y, moved by *offset*."""
    return [(POINTS[v][0] + offset[0], POINTS[v][1] + offset[1]) for v in face]


def snap_of(faces, uvs, shown=None, positions=None):
    """A snapshot of *faces*, given as vertex lists, with one UV per corner.

    Every vertex is at the origin unless *positions* gives them.
    """
    corner_vert = np.array([v for face in faces for v in face], np.int32)
    face_offset = np.cumsum([0] + [len(face) for face in faces])[:-1].astype(np.int32)
    # A fan from each face's first corner, as a quad renders.
    tri_corners = [(start, start + i, start + i + 1)
                   for start, face in zip(face_offset.tolist(), faces) for i in range(1, len(face) - 1)]
    if positions is None:
        positions = np.zeros((corner_vert.max(initial=-1) + 1, 3))
    return seams.Snapshot(
        corner_vert=corner_vert,
        face_offset=face_offset,
        uv=np.array([uv for face in uvs for uv in face], np.float32).reshape(-1, 2),
        tri_corners=np.array(tri_corners, np.int32).reshape(-1, 3),
        shown=np.ones(len(faces), bool) if shown is None else np.array(shown, bool),
        position=np.array(positions, np.float32),
    )


def run(snap):
    """The `Index` of *snap*, and how many units working it out took."""
    steps = seams.index_of(snap, "reading the UV seams")
    units = 0
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return done.value, units
        units += 1


def islands_of(snap):
    """The island of every face of *snap*, worked out afresh."""
    seams.release()
    return run(snap)[0].face_island.tolist()


def crossings_of(snap, width=64, height=64):
    """The `Index` of *snap*, worked out afresh, and its crossings for an image of *width* by *height*."""
    seams.release()
    index = run(snap)[0]
    return index, seams.crossings(snap, index, width, height)


def texels(z, width=64, height=64):
    """UV points, as complex numbers, in texels of an image of *width* by *height*."""
    return z.real * width + 1j * z.imag * height


def mapped_uvs(face, turn, flip, offset):
    """The UVs of *face*, its vertices' x and y carried by ``offset + turn * z + flip * conj(z)``."""
    z = np.array([complex(*POINTS[v][:2]) for v in face])
    uv = offset + turn * z + flip * np.conj(z)
    return list(zip(uv.real.tolist(), uv.imag.tolist()))


def inside(polygons, count, points):
    """Whether each of *points* is inside its row's convex, counter-clockwise polygon."""
    slot = np.arange(polygons.shape[1])
    after = np.where(slot + 1 < count[:, None], slot + 1, 0)
    edges = np.take_along_axis(polygons, after, axis=1) - polygons
    left = (np.conj(edges[:, :, None]) * (points[:, None, :] - polygons[:, :, None])).imag >= 0.0
    return (left | (slot >= count[:, None])[:, :, None]).all(axis=1) & (count >= 3)[:, None]


def strip(order):
    """A strip of quads along x, where face *i* is the quad at ``order[i]``."""
    faces, uvs = [], []
    for position in order:
        face = (position, position + 1, len(order) + 2 + position, len(order) + 1 + position)
        faces.append(face)
        uvs.append([(position, 0), (position + 1, 0), (position + 1, 1), (position, 1)])
    return faces, uvs


def mesh_object(name, faces, uvs, tree=None):
    """A mesh object of *faces* over `POINTS`, with a UV map "UVMap" set to *uvs*."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(POINTS, [], faces)
    layer = mesh.uv_layers.new(name="UVMap")
    layer.data.foreach_set('uv', np.array([uv for face in uvs for uv in face], np.float32).ravel())
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    if tree is not None:
        use_tree(obj, tree)
    return obj


def snapshot(obj, tree):
    return seams.snapshot(obj, "UVMap", tree, bpy.context.evaluated_depsgraph_get())


def same_point(a, b):
    return max(abs(a.x - b.x), abs(a.y - b.y)) <= seams.SEAM_TOLERANCE


def same_partition(ours, theirs):
    """Whether two numberings of the faces split them into the same islands."""
    return len(ours) == len(theirs) and len(set(zip(ours, theirs))) == max(ours) == max(theirs)


def reference(mesh, uv_map):
    """The island of every face of *mesh*, by walking it in bmesh."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    layer = bm.loops.layers.uv[uv_map]
    island = [0] * len(bm.faces)
    count = 0
    for face in bm.faces:
        if island[face.index]:
            continue
        count += 1
        island[face.index] = count
        stack = [face]
        while stack:
            for loop in stack.pop().loops:
                if len(loop.edge.link_faces) != 2:
                    continue
                other = loop.link_loop_radial_next
                start, end = ((other, other.link_loop_next) if other.vert == loop.vert
                              else (other.link_loop_next, other))
                agree = (same_point(loop[layer].uv, start[layer].uv)
                         and same_point(loop.link_loop_next[layer].uv, end[layer].uv))
                if agree and not island[other.face.index]:
                    island[other.face.index] = count
                    stack.append(other.face)
    bm.free()
    return island


try:
    section("which faces an edge joins")
    check(islands_of(snap_of([A, B], [uvs_at(A), uvs_at(B)])) == [1, 1],
          "two faces that agree on the UVs of their shared edge are one island")
    check(islands_of(snap_of([A, B_FLIPPED], [uvs_at(A), uvs_at(B_FLIPPED)])) == [1, 1],
          "whichever way each face runs along the edge")
    check(islands_of(snap_of([A, B], [uvs_at(A), uvs_at(B, (3, 0))])) == [1, 2],
          "an edge whose UVs differ on each side is a seam")
    # B's corners on the shared edge are its first (vertex 1) and its
    # last (vertex 4), and each end is compared on its own.
    for corner, (u, v) in ((0, (1, 0)), (3, (1, 1))):
        end = f"vertex {B[corner]}"
        one_end = uvs_at(B)
        one_end[corner] = (u + 2, v)
        check(islands_of(snap_of([A, B], [uvs_at(A), one_end])) == [1, 2],
              f"and so is one whose UVs differ at one end only ({end})")
        near = uvs_at(B)
        near[corner] = (u + seams.SEAM_TOLERANCE / 2, v)
        check(islands_of(snap_of([A, B], [uvs_at(A), near])) == [1, 1],
              f"UVs closer than the tolerance count as the same point ({end})")
        near[corner] = (u + seams.SEAM_TOLERANCE * 8, v)
        check(islands_of(snap_of([A, B], [uvs_at(A), near])) == [1, 2],
              f"and UVs further apart do not ({end})")
    check(islands_of(snap_of([A, B_FLIPPED], [uvs_at(A), uvs_at(B_FLIPPED, (0, 0.5))])) == [1, 2],
          "also for faces that run along it the same way")

    # A third face on the shared edge, whose UVs agree with both.
    third = (1, 6, 7, 4)
    check(islands_of(snap_of([A, B, third], [uvs_at(A), uvs_at(B), [(1, 0), (1, 0), (1, 1), (1, 1)]]))
          == [1, 2, 3], "an edge used by more than two faces joins none of them")

    section("the faces the tree is shown on")
    faces, uvs = strip([0, 1, 2])
    check(islands_of(snap_of(faces, uvs)) == [1, 1, 1], "a strip of faces is one island")
    check(islands_of(snap_of(faces, uvs, shown=[True, False, True])) == [1, 0, 2],
          "a face that is not shown is in none, and the faces either side of it are two")
    check(islands_of(snap_of(faces, uvs, shown=[False, False, False])) == [0, 0, 0],
          "a mesh with no face shown has no island")
    check(islands_of(snap_of([], [])) == [], "and a mesh with no faces has none either")

    section("numbering and rounds")
    faces, uvs = strip([0, 2, 1])
    faces.insert(1, (20, 21, 22, 23))
    uvs.insert(1, [(5, 5), (6, 5), (6, 6), (5, 6)])
    check(islands_of(snap_of(faces, uvs)) == [1, 2, 1, 1],
          "islands are numbered in the order of their first face")
    # Face 0 joins 2, 2 joins 3 and 3 joins 1. The first round hooks 3
    # onto 1 and 2 onto 0, so a second round is needed to join the two.
    faces, uvs = strip([0, 3, 1, 2])
    seams.release()
    index, units = run(snap_of(faces, uvs))
    check(index.face_island.tolist() == [1, 1, 1, 1] and units > 2,
          f"islands that need more than one round come out whole ({units} units)")
    order = np.random.default_rng(7).permutation(300).tolist()
    faces, uvs = strip(order)
    check(islands_of(snap_of(faces, uvs)) == [1] * 300, "so does a long strip listed in a random order")

    bpy.ops.mesh.primitive_monkey_add()
    monkey = bpy.context.active_object
    tree = bpy.data.node_groups.new("Seams", 'PaintSystemNodeTree')
    tree.initialize()
    use_tree(monkey, tree)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ours = islands_of(seams.snapshot(monkey, "UVMap", tree, depsgraph))
    theirs = reference(monkey.data, "UVMap")
    check(same_partition(ours, theirs),
          f"Suzanne has the islands a walk over the mesh finds ({max(ours)} and {max(theirs)})")
    check(sorted(np.bincount(ours)[1:].tolist()) == [32, 32, 59, 59, 318],
          "which are its eyes, its ears and its head")

    subdivided = monkey.modifiers.new("Subdivide", 'SUBSURF')
    subdivided.levels = 1
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ours = islands_of(seams.snapshot(monkey, "UVMap", tree, depsgraph))
    theirs = reference(monkey.evaluated_get(depsgraph).data, "UVMap")
    # Subdividing once makes a face of every corner.
    check(same_partition(ours, theirs) and len(ours) == len(monkey.data.loops),
          f"so does Suzanne subdivided, as she renders ({len(ours)} faces, {max(ours)} islands)")
    monkey.modifiers.remove(subdivided)

    # Smart UV Project cuts the mesh into many islands, some of them a
    # single face, with UVs that nearly touch across the cuts.
    unwrapped = bpy.data.objects.new("Seams Unwrapped", monkey.data.copy())
    bpy.context.scene.collection.objects.link(unwrapped)
    bpy.context.view_layer.objects.active = unwrapped
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.smart_project(island_margin=0.003)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ours = islands_of(seams.snapshot(unwrapped, "UVMap", tree, depsgraph))
    theirs = reference(unwrapped.data, "UVMap")
    check(same_partition(ours, theirs) and max(ours) > 20,
          f"and so does Suzanne unwrapped by Smart UV Project ({max(ours)} islands)")

    section("what a snapshot reads")
    pair = mesh_object("Seams Pair", [A, B], [uvs_at(A), uvs_at(B, (3, 0))], tree)
    snap = snapshot(pair, tree)
    check(snap.shown.tolist() == [True, True], "both faces show the tree")
    islands = seams.triangle_islands(snap, run(snap)[0])
    check(islands.tolist() == [1, 1, 2, 2], f"each triangle is in its face's island ({islands.tolist()})")

    shows = pair.data.materials[0]
    plain = bpy.data.materials.new("Seams Plain")
    pair.data.materials.append(plain)
    pair.data.polygons[1].material_index = 1
    check(snapshot(pair, tree).shown.tolist() == [True, False],
          "a face with a material that does not show the tree is not shown")
    pair.data.polygons[1].material_index = 7
    check(snapshot(pair, tree).shown.tolist() == [True, False],
          "a face past the last slot uses the last slot's material")
    pair.data.materials[0], pair.data.materials[1] = plain, shows
    check(snapshot(pair, tree).shown.tolist() == [False, True], "so it shows the tree when that material does")
    pair.data.materials[1] = None
    check(snapshot(pair, tree).shown.tolist() == [False, False], "and an empty slot shows nothing")
    pair.data.polygons[1].material_index = 0

    # A modifier can give faces a material the object has no slot for.
    # They render with it, so that material is the one that counts.
    group = bpy.data.node_groups.new("Seams Set Material", 'GeometryNodeTree')
    group.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    setter = group.nodes.new('GeometryNodeSetMaterial')
    setter.inputs['Material'].default_value = shows
    group.links.new(group.nodes.new('NodeGroupInput').outputs[0], setter.inputs['Geometry'])
    group.links.new(setter.outputs[0], group.nodes.new('NodeGroupOutput').inputs[0])
    modifier = pair.modifiers.new("Set Material", 'NODES')
    modifier.node_group = group
    check(snapshot(pair, tree).shown.tolist() == [True, True],
          "a face shows the tree when a modifier gives it a material that does")
    pair.modifiers.remove(modifier)
    pair.data.materials[0] = shows

    subdivided = pair.modifiers.new("Subdivide", 'SUBSURF')
    subdivided.levels = 1
    snap = snapshot(pair, tree)
    check(len(snap.face_offset) == 8 and snap.uv.shape == (32, 2),
          f"the snapshot is of the evaluated mesh ({len(snap.face_offset)} faces)")
    check(islands_of(snap) == [1] * 4 + [2] * 4, "whose seams are where the mesh's are")
    pair.modifiers.remove(subdivided)

    # A build goes on from its snapshot while the mesh is edited, so the
    # snapshot must not share memory with the mesh.
    snap = snapshot(pair, tree)
    fields = ('corner_vert', 'face_offset', 'uv', 'tri_corners', 'shown', 'position')
    saved = [getattr(snap, name).copy() for name in fields]
    bpy.context.view_layer.objects.active = pair
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        edited = bmesh.from_edit_mesh(pair.data)
        uv_layer = edited.loops.layers.uv["UVMap"]
        for face in edited.faces:
            for loop in face.loops:
                loop[uv_layer].uv = loop[uv_layer].uv * 0.5
        for vertex in edited.verts:
            vertex.co.z += 1.0
        bmesh.update_edit_mesh(pair.data)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.evaluated_depsgraph_get()
    check(all(np.array_equal(getattr(snap, name), was) for name, was in zip(fields, saved)),
          "a snapshot stays as it was while the mesh's UVs and vertices are edited")

    pair.location = (5, 0, 0)
    pair.rotation_euler = (0, 0, pi / 2)
    pair.scale = (1, 3, 1)
    bpy.context.view_layer.update()
    position = snapshot(pair, tree).position
    placed = np.array([pair.matrix_world.to_3x3() @ vertex.co for vertex in pair.data.vertices])
    check(np.abs(position - placed).max() < 1e-5,
          "the vertices are read as the object turns and scales them, without its location")
    pair.location, pair.rotation_euler, pair.scale = (0, 0, 0), (0, 0, 0), (1, 1, 1)

    section("the key the islands are kept under")
    before = seams.digest(snapshot(pair, tree))
    check(seams.digest(snapshot(pair, tree)) == before, "the same mesh gives the same key")
    pair.location = (5, 0, 0)
    pair.data.vertices[0].co = (0, 0, 3)
    pair.data.update()
    check(seams.digest(snapshot(pair, tree)) == before, "moving the object or a vertex keeps it")
    pair.data.uv_layers["UVMap"].data[0].uv = (seams.SEAM_TOLERANCE * 8, 0)
    pair.data.update()
    moved = seams.digest(snapshot(pair, tree))
    check(moved != before, "moving a UV changes it")
    pair.data.polygons[1].material_index = 1
    pair.data.update()
    check(seams.digest(snapshot(pair, tree)) not in (before, moved), "and so does hiding a face")
    pair.data.polygons[1].material_index = 0
    pair.data.update()
    # Posing can fill an n-gon with other triangles, and each crossing's
    # third corners come from them.
    square = snap_of([A], [uvs_at(A)])
    split = replace(square, tri_corners=np.array([[0, 1, 3], [1, 2, 3]], np.int32))
    check(seams.digest(split) != seams.digest(square), "and so does splitting a face into other triangles")

    # Ripping the shared edge gives B vertices of its own, and moves no UV.
    joined = snap_of([A, B], [uvs_at(A), uvs_at(B)])
    ripped = snap_of([A, (6, 2, 5, 7)], [uvs_at(A), uvs_at(B)])
    seams.release()
    check(seams.digest(ripped) != seams.digest(joined)
          and [run(each)[0].face_island.tolist() for each in (joined, ripped)] == [[1, 1], [1, 2]],
          "and so does ripping an edge, with every UV where it was")
    # Both round to one step of the tolerance, but only the first is
    # within it of A's UVs, so the two make different islands.
    within, beyond = uvs_at(B), uvs_at(B)
    for corner, v in ((0, 0), (3, 1)):
        within[corner] = (1 + 0.9 * seams.SEAM_TOLERANCE, v)
        beyond[corner] = (1 + 1.1 * seams.SEAM_TOLERANCE, v)
    seams.release()
    check([run(snap_of([A, B], [uvs_at(A), uvs]))[0].face_island.tolist() for uvs in (within, beyond)]
          == [[1, 1], [1, 2]], "and so does a UV that moves out of the tolerance, however little")

    section("the islands kept")
    seams.release()
    snap = snapshot(pair, tree)
    first, units = run(snap)
    check(units > 0, "the first time, the islands are worked out")
    again, units = run(snapshot(pair, tree))
    check(again is first and units == 0, "the next time, they are found again without a unit")
    strips = [snap_of(*strip(list(range(count + 2)))) for count in range(seams.SEAM_ENTRIES)]
    for each in strips:
        run(each)
    check(all(run(each)[1] == 0 for each in strips), f"the last {seams.SEAM_ENTRIES} meshes are kept")
    check(run(snap)[1] > 0, "and the one before them is not")
    seams.release()
    check(run(snap)[1] > 0, "and releasing forgets them")
    seams.release()

    section("the maps across a seam")
    # A's UVs are its vertices' x and y, so a point of A's UVs past the
    # shared edge is where B lies in the world, unfolded flat. B's UVs
    # carry its vertices by one linear map, and the crossing onto B must
    # carry every point by the same map, and the one back by its inverse.
    maps = {
        "turned a quarter and twice as large": (2j, 0, 5),
        "half as large": (0.5, 0, 3),
        "mirrored": (0, -1, 4),
        "stretched along the seam": (2, -1, 5),
        "stretched across the seam": (2, 1, 5),
        "sheared": (1 - 0.5j, 0.5j, 5),
    }
    rng = np.random.default_rng(3)
    sample = rng.uniform(0.0, 2.0, 64) + 1j * rng.uniform(0.0, 1.0, 64)
    for name, (turn, flip, offset) in maps.items():
        snap = snap_of([A, B], [uvs_at(A), mapped_uvs(B, turn, flip, offset)], positions=POINTS)
        for width, height in ((64, 64), (64, 32)):
            _, found = crossings_of(snap, width, height)
            onto = np.flatnonzero(found.near_island == 1)
            back = np.flatnonzero(found.near_island == 2)
            if len(onto) != 1 or len(back) != 1:
                check(False, f"B {name}: one crossing each way ({len(onto)} and {len(back)})")
                continue
            here = texels(sample, width, height)
            there = texels(offset + turn * sample + flip * np.conj(sample), width, height)
            error = max(np.abs(seams.carry(found, onto, here[None]) - there).max(),
                        np.abs(seams.carry(found, back, there[None]) - here).max())
            check(error < 1e-3, f"B {name}: both ways across carry a point where the unfolded mesh has it "
                                f"({width} by {height} texels, off by {error:.2g})")
    _, found = crossings_of(snap_of([A, B], [uvs_at(A), mapped_uvs(B, 2j, 0, 5)], positions=POINTS))
    onto = found.near_island == 1
    check(np.abs(found.turn[onto] - 2j).max() < 1e-6 and np.abs(found.flip[onto]).max() < 1e-6,
          "a turn and a scale have no flip")
    _, found = crossings_of(snap_of([A, B], [uvs_at(A), mapped_uvs(B, 0, -1, 4)], positions=POINTS))
    check(np.abs(found.turn).max() < 1e-6 and np.abs(found.flip + 1).max() < 1e-6,
          "and a mirror has no turn, whichever way it is crossed")

    # The same UVs over B folded up about the shared edge, and over B
    # twice as wide in the world, as posing could leave it.
    folded, widened = [list(point) for point in POINTS], [list(point) for point in POINTS]
    folded[2], folded[5] = [1, 0, 1], [1, 1, 1]
    widened[2], widened[5] = [3, 0, 0], [3, 1, 0]
    uvs = [uvs_at(A), uvs_at(B, (3, 0))]
    index, _ = crossings_of(snap_of([A, B], uvs, positions=POINTS))
    past, up = rng.uniform(0.0, 1.0, 32), rng.uniform(0.0, 1.0, 32)
    for name, positions, there in (("folded up about the seam", folded, (4 + past) + 1j * up),
                                   ("twice as wide in the world", widened, (4 + past / 2) + 1j * up)):
        posed = snap_of([A, B], uvs, positions=positions)
        again, units = run(posed)
        found = seams.crossings(posed, again, 64, 64)
        onto = np.flatnonzero(found.near_island == 1)
        error = (np.abs(seams.carry(found, onto, texels((1 + past) + 1j * up)[None]) - texels(there)).max()
                 if len(onto) == 1 else np.inf)
        check(again is index and units == 0 and error < 1e-3,
              f"with B {name}, the islands are kept and the crossing follows the mesh (off by {error:.2g})")

    section("crossings that carry nothing")
    moved = uvs_at(B, (3, 0))
    with_third = POINTS + [(1, 0, 1), (1, 1, 1)]
    flattened = [list(point) for point in POINTS]
    flattened[5] = [1, 0.5, 0]
    cases = {
        "a face on its own": snap_of([A], [uvs_at(A)], positions=POINTS),
        "an edge used by more than two faces": snap_of(
            [A, B, (1, 6, 7, 4)], [uvs_at(A), moved, [(7, 0), (8, 0), (8, 1), (7, 1)]], positions=with_third),
        "an edge shorter in UV than the tolerance": snap_of(
            [A, B], [uvs_at(A), [(5, 0.5), (6, 0), (6, 1), (5, 0.5 + 5e-7)]], positions=POINTS),
        "an edge whose triangle has no area in UV": snap_of(
            [A, B], [uvs_at(A), [(4, 0), (5, 0), (4, 0.5), (4, 1)]], positions=POINTS),
    }
    for name, snap in cases.items():
        index, found = crossings_of(snap)
        check(len(index.crossing_corners) == 0 and len(found.near_start) == 0, f"{name} has no crossing")
    index, found = crossings_of(snap_of([A, B], [uvs_at(A), moved], positions=flattened))
    check(len(index.crossing_corners) == 2 and len(found.near_start) == 0,
          "one whose triangle has no area in the world is kept, but no build can use it")
    for scale, kept in ((1 / 20, 0), (1 / 10, 2)):
        _, found = crossings_of(snap_of([A, B], [uvs_at(A), mapped_uvs(B, scale, 0, 3)], positions=POINTS))
        check(len(found.near_start) == kept,
              f"B {1 / scale:g} times smaller in UV: {kept} of 2 crossings used, since the limit is "
              f"{seams.SCALE_LIMIT:g} times either way")

    section("crossings kept once")
    copy = [tuple(v + 6 for v in face) for face in (A, B)]
    index, _ = crossings_of(snap_of([A, B, *copy], [uvs_at(A), moved, uvs_at(A), moved], positions=POINTS * 2))
    check(sorted(index.crossing_islands.tolist()) == [[1, 2], [2, 1]],
          "a copy stacked on the same UVs is crossed once, between the smallest islands of each set")
    other_side = [(4, 0), (3, 0), (3, 1), (4, 1)]
    index, _ = crossings_of(snap_of([A, B, *copy], [uvs_at(A), moved, uvs_at(A), other_side],
                                    positions=POINTS * 2))
    check(len(index.crossing_corners) == 4,
          "but a copy whose far face lies on the other side of the same edge is a crossing of its own")
    # B mirrored in place: its edge lies over A's, end for end, so the way
    # across and the way back look alike but for the side each leaves.
    in_place = [(POINTS[v][0], 1 - POINTS[v][1]) for v in B]
    index, _ = crossings_of(snap_of([A, B], [uvs_at(A), in_place], positions=POINTS))
    check(sorted(index.crossing_islands.tolist()) == [[1, 2], [2, 1]],
          "and a far face mirrored in place, its edge over the near one's end for end, is crossed both ways")

    # A new Suzanne, since Smart UV Project above also unwrapped the first.
    bpy.ops.mesh.primitive_monkey_add()
    monkey = bpy.context.active_object
    use_tree(monkey, tree)
    half = bpy.data.objects.new("Seams Half", monkey.data.copy())
    bpy.context.scene.collection.objects.link(half)
    edited = bmesh.new()
    edited.from_mesh(half.data)
    bmesh.ops.delete(edited, geom=[vertex for vertex in edited.verts if vertex.co.x < -1e-6], context='VERTS')
    edited.to_mesh(half.data)
    edited.free()
    alone = len(crossings_of(snapshot(half, tree))[0].crossing_corners)
    half.modifiers.new("Mirror", 'MIRROR')
    mirrored = len(crossings_of(snapshot(half, tree))[0].crossing_corners)
    check(alone > 0 and mirrored == alone,
          f"so Suzanne's right half mirrored onto the same UVs has only the half's crossings "
          f"({mirrored} and {alone})")

    section("crossings that follow on")
    #   6 --- 7 --- 8
    #   | A2  | B2  |
    #   3 --- 4 --- 5
    #   | A1  | B1  |
    #   0 --- 1 --- 2
    grid = [(x, y, 0) for y in range(3) for x in range(3)]
    faces = [(0, 1, 4, 3), (1, 2, 5, 4), (3, 4, 7, 6), (4, 5, 8, 7)]

    def grid_uvs(face, offset=(0, 0)):
        return [(grid[v][0] + offset[0], grid[v][1] + offset[1]) for v in face]

    snap = snap_of(faces, [grid_uvs(faces[0]), grid_uvs(faces[1], (3, 0)), grid_uvs(faces[2]),
                           grid_uvs(faces[3], (3, 0))], positions=grid)
    index, found = crossings_of(snap)
    lower = np.flatnonzero((found.near_island == 1) & (found.near_start.imag + found.near_end.imag < 128))
    upper = np.flatnonzero((found.near_island == 1) & (found.near_start.imag + found.near_end.imag > 128))
    check(len(found.near_start) == 4 and np.isfinite(index.beyond[..., 0]).sum() == 4,
          "two crossings along one seam follow on from each other at the vertex they share, both ways")
    # A stamp of 16 texels on vertex 4, carried from A onto B, three
    # images to the right.
    corners = (64 + 64j) + np.array([[-8 - 8j, 8 - 8j, 8 + 8j, -8 + 8j]])
    coords = np.array([[0, 8, 8 + 8j, 8j]], complex)
    stamp, crossing = seams.candidates(found, np.array([64 + 64j]), np.array([1]), 16)
    carried = seams.pieces(found, corners, coords, stamp, crossing)
    areas = seams._area(carried.points, carried.count)
    split = {int(each): carried.points[k, :carried.count[k]] for k, each in enumerate(crossing)}
    check(sorted(crossing.tolist()) == sorted([*lower, *upper]) and np.abs(areas - 96).max() < 1e-9,
          f"a stamp on the shared vertex is split between them, and each piece reaches {seams.BAND:g} "
          f"texels past the far edge ({areas.tolist()})")
    check(len(lower) == len(upper) == 1 and split[lower[0]].imag.max() <= 64 + 1e-9
          and split[upper[0]].imag.min() >= 64 - 1e-9,
          "at the line halfway between the two, so they do not paint over each other")
    check(np.abs(carried.coords - np.where(np.arange(seams.CORNERS) < carried.count[:, None],
                                           (carried.points - 192 - corners[0, 0]) / 2, 0)).max() < 1e-9
          and (carried.island == 2).all(),
          "each piece shows its own part of the brush, on the far island")
    # B2 bent away from B1 along the seam: the two far edges meet at an
    # angle at vertex 4, and the caps halve it.
    bent = grid_uvs(faces[3], (3, 0))
    bent[2], bent[3] = (bent[2][0] + 0.5, bent[2][1]), (bent[3][0] + 0.5, bent[3][1])
    _, found = crossings_of(snap_of(faces, [grid_uvs(faces[0]), grid_uvs(faces[1], (3, 0)),
                                            grid_uvs(faces[2]), bent], positions=grid))
    stamp, crossing = seams.candidates(found, np.array([64 + 64j]), np.array([1]), 16)
    carried = seams.pieces(found, corners, coords, stamp, crossing)
    up = (32 + 64j) / abs(32 + 64j)
    halfway = (-1j - up) / abs(-1j - up)
    side = {int(each): (np.conj(halfway) * (carried.points[k, :carried.count[k]] - (256 + 64j))).real
            for k, each in enumerate(crossing)}
    middle = found.near_start.imag + found.near_end.imag
    below = [side[k] for k in np.flatnonzero((found.near_island == 1) & (middle < 128))]
    above = [side[k] for k in np.flatnonzero((found.near_island == 1) & (middle > 128))]
    check(len(carried.stamp) == 2 and len(below) == len(above) == 1
          and -1e-9 <= below[0].min() < 1e-6 and -1e-6 < above[0].max() <= 1e-9,
          "where the far edges bend, the two pieces meet on the line halving the angle between them")
    # B2 still meets B1 at vertex 4 in UV, but a seam along the edge from
    # 4 to 5 makes it an island of its own.
    moved_up = [(4, 1), (5, 1.5), (5, 2), (4, 2)]
    index, found = crossings_of(snap_of(faces, [grid_uvs(faces[0]), grid_uvs(faces[1], (3, 0)),
                                                grid_uvs(faces[2]), moved_up], positions=grid))
    check(np.isnan(index.beyond).all() and not found.normals[:, 1:].any(),
          "crossings onto different islands do not follow on, and have no caps")
    # B2's triangle along the edge from 4 to 7 flat in the world: the
    # crossings over that edge carry nothing, so the ones next to them
    # are not capped toward them.
    flat = [list(point) for point in grid]
    flat[8] = [1, 1.5, 0]
    index, found = crossings_of(snap_of(faces, [grid_uvs(faces[0]), grid_uvs(faces[1], (3, 0)),
                                                grid_uvs(faces[2]), grid_uvs(faces[3], (3, 0))], positions=flat))
    check(np.isfinite(index.beyond[..., 0]).sum() == 4 and len(found.near_start) == 2
          and not found.normals[:, 1:].any(),
          "a crossing is not capped toward a next crossing the build leaves out")

    section("stamps near a seam")
    _, found = crossings_of(snap_of([A, B], [uvs_at(A), moved], positions=POINTS))
    onto = np.flatnonzero(found.near_island == 1).tolist()
    half = 16 * sqrt(2) / 2
    reach = half + seams.BAND
    # A stamp of A centred in A's margin past the seam still crosses it,
    # but one centred further out reaches that part of B across another
    # edge, or its quad paints it where it is.
    for x, owner, expected in ((60, 1, onto), (64 - reach + 0.5, 1, onto), (64 - reach - 0.5, 1, []),
                               (64 + seams.BAND - 0.5, 1, onto), (64 + seams.BAND + 0.5, 1, []),
                               (60, 2, []), (60, 0, [])):
        stamp, crossing = seams.candidates(found, np.array([x + 32j]), np.array([owner]), 16)
        check(crossing.tolist() == expected,
              f"a stamp of island {owner} at {x:.1f} texels, the seam at 64, may cross {expected}")
    corners = (60 + 32j) + np.array([[-8 - 8j, 8 - 8j, 8 + 8j, -8 + 8j]])
    stamp, crossing = seams.candidates(found, np.array([60 + 32j]), np.array([1]), 16)
    carried = seams.pieces(found, corners, coords, stamp, crossing)
    points = carried.points[0, :carried.count[0]]
    check(len(carried.stamp) == 1 and abs(seams._area(carried.points, carried.count)[0] - 8 * 16) < 1e-9
          and points.real.min() >= 256 - seams.BAND - 1e-9 and points.real.max() <= 260 + 1e-9,
          f"its piece is the part past the seam, plus {seams.BAND:g} texels of margin")
    sources = carried.sources[:1, :carried.count[0]]
    check(np.abs(seams.carry(found, crossing, sources) - points).max() < 1e-9,
          "and its sources are its corners before they were carried across")

    # B eight times smaller in UV: the band past B's edge is eight times
    # as many texels on A's side, so stamps further into A reach it,
    # from grid cells further from the edge's own.
    _, found = crossings_of(snap_of([A, B], [uvs_at(A), mapped_uvs(B, 1 / 8, 0, 3)], positions=POINTS))
    onto, back = found.near_island == 1, found.near_island == 2
    check(np.abs(found.band[onto] - 8 * seams.BAND).max() < 1e-6
          and np.abs(found.band[back] - seams.BAND).max() < 1e-6,
          f"a piece may start {8 * seams.BAND:g} texels into A when B has an eighth of the texels, "
          f"and {seams.BAND:g} into B the other way")
    far = 64 - (half + 8 * seams.BAND)
    reached = [seams.candidates(found, np.array([x + 32j]), np.array([1]), 16)[1].tolist()
               for x in (far + 0.5, far - 0.5)]
    # B's edge is at 200 texels. The stamp covers 24 to 40 texels before
    # A's edge, which is 3 to 5 past B's, and only up to 4 is kept.
    x = 64 - 8 - 6 * seams.BAND
    corners = (x + 32j) + np.array([[-8 - 8j, 8 - 8j, 8 + 8j, -8 + 8j]])
    stamp, crossing = seams.candidates(found, np.array([x + 32j]), np.array([1]), 16)
    carried = seams.pieces(found, corners, coords, stamp, crossing)
    points = carried.points[0, :carried.count[0]]
    check(reached == [np.flatnonzero(onto).tolist(), []] and len(carried.stamp) == 1
          and points.real.min() >= 200 - seams.BAND - 1e-9 and points.real.max() <= 200 - 3 * seams.BAND / 4 + 1e-9,
          "so stamps that far into A cross the seam, and their pieces paint only that band past B's edge")

    # Suzanne, against every stamp and crossing checked one by one.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    suzanne = seams.snapshot(monkey, "UVMap", tree, depsgraph)
    index, found = crossings_of(suzanne, 512, 512)
    check(len(index.crossing_corners) == 98 and len(found.near_start) == 98
          and np.isfinite(index.beyond[..., 0]).sum() == 168,
          f"Suzanne has 98 crossings, all used, and 168 of their ends follow on "
          f"({len(index.crossing_corners)}, {len(found.near_start)}, {np.isfinite(index.beyond[..., 0]).sum()})")
    centres = rng.uniform(0, 512, 20000) + 1j * rng.uniform(0, 512, 20000)
    owners = rng.integers(0, 6, 20000)
    size = 24
    stamp, crossing = seams.candidates(found, centres, owners, size)
    reach = size * sqrt(2) / 2 + found.band
    along = found.near_end - found.near_start
    # All 98 are used, so the crossings are the index's, in order.
    third = suzanne.uv.astype(np.float64)[index.crossing_corners[:, 2]] @ np.array([512, 512j])
    normal = 1j * along / np.abs(along)
    normal = np.where((np.conj(normal) * (third - found.near_start)).real > 0, normal, -normal)
    expected, distances = set(), {}
    for k in np.flatnonzero(owners > 0):
        t = np.clip(((np.conj(along) * (centres[k] - found.near_start)).real / np.abs(along) ** 2), 0, 1)
        distance = np.abs(centres[k] - (found.near_start + t * along))
        inward = (np.conj(normal) * (centres[k] - found.near_start)).real
        for each in np.flatnonzero((distance <= reach) & (inward >= -seams.BAND) & (found.near_island == owners[k])):
            expected.add((int(k), int(each)))
            distances[int(k), int(each)] = distance[each]
    pairs = list(zip(stamp.tolist(), crossing.tolist()))
    ordered = all(a[0] < b[0] or (a[0] == b[0] and distances[a] <= distances[b] + 1e-9)
                  for a, b in zip(pairs, pairs[1:]) if a in distances and b in distances)
    wide = int((found.band > seams.BAND).sum())
    check(set(pairs) == expected and len(pairs) == len(expected) and ordered and len(pairs) > 100 and wide > 0,
          f"a stamp may cross every crossing of its island within reach from its side, nearest first "
          f"({len(pairs)} pairs, {wide} crossings reaching further)")

    tri = suzanne.tri_corners.astype(np.int64)
    first, second, third = (np.roll(tri, -shift, axis=1).ravel() for shift in range(3))
    # Only the edges of faces, not the diagonals a quad is cut along.
    following = seams._next_corners(suzanne)[1]
    edge = (following[first] == second) | (following[second] == first)
    first, second, third = first[edge], second[edge], third[edge]
    check(np.array_equal(seams._third_corners(suzanne, first, second), third)
          and np.array_equal(seams._third_corners(suzanne, second, first), third)
          and len(first) == len(suzanne.corner_vert),
          "the third corner along each edge of a face is its triangle's other corner, whichever way it runs")
    last = suzanne.face_offset[-1]
    check(seams._third_corners(suzanne, np.array([0]), np.array([last])).tolist() == [-1],
          "and two corners of different faces have none")

    section("cutting a piece")
    rows = 400
    centre = rng.uniform(-5, 5, rows) + 1j * rng.uniform(-5, 5, rows)
    turn = np.exp(2j * pi * rng.uniform(0, 1, rows)) * rng.uniform(1, 4, rows)
    # The first row is a unit square with three of its corners cut off,
    # the most corners a piece can have.
    centre[0], turn[0] = 0, 1
    cut_corner = [(-direction / abs(direction), 1.2) for direction in (-1 - 1j, 1 - 1j, 1 + 1j)]
    square = centre[:, None] + turn[:, None] * np.array([-1 - 1j, 1 - 1j, 1 + 1j, -1 + 1j])
    atlas_map = rng.normal(size=(3, rows)) + 1j * rng.normal(size=(3, rows))

    def atlas_of(points):
        return atlas_map[0][:, None] + atlas_map[1][:, None] * points + atlas_map[2][:, None] * np.conj(points)

    points = np.zeros((rows, seams.CORNERS), complex)
    points[:, :4] = square
    coords = atlas_of(points)
    count = np.full(rows, 4)
    lines = []
    for line in range(3):
        normal = np.exp(2j * pi * rng.uniform(0, 1, rows))
        offset = -(np.conj(normal) * centre).real + rng.uniform(-4, 4, rows)
        # The last rows get the line that is not there, and a line past
        # the whole polygon on each side.
        normal[-3], offset[-3] = 0, 1
        offset[-2], offset[-1] = 100, -100
        normal[0], offset[0] = cut_corner[line]
        lines.append((normal, offset))
        points, (coords,), count = seams._clip(points, (coords,), count, normal, offset)
    probe = centre[:, None] + 4 * (rng.uniform(-1, 1, (rows, 200)) + 1j * rng.uniform(-1, 1, (rows, 200)))
    wanted = inside(square, np.full(rows, 4), probe)
    for normal, offset in lines:
        wanted &= (np.conj(normal[:, None]) * probe).real + offset[:, None] >= 0.0
    used = np.arange(seams.CORNERS) < count[:, None]
    check((count <= seams.CORNERS).all() and np.array_equal(inside(points, count, probe), wanted)
          and count[0] == seams.CORNERS and count[-3] == 4 and count[-1] == 0,
          f"three cuts keep exactly the part of a quad on the kept side of each line "
          f"(at most {count.max()} corners)")
    check(np.abs(np.where(used, coords - atlas_of(points), 0)).max() < 1e-9,
          "and the atlas points are cut by the same map as the corners")
except Exception:
    traceback.print_exc()
    check(False, "exception during the seams test")

finish("painter seams")
