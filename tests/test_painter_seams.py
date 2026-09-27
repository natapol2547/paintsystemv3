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
  materials.

`tests/test_filter_painter.py` checks that strokes stay on their island.
"""
import os
import sys
import traceback

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


def snap_of(faces, uvs, shown=None):
    """A snapshot of *faces*, given as vertex lists, with one UV per corner."""
    corner_vert = np.array([v for face in faces for v in face], np.int32)
    face_offset = np.cumsum([0] + [len(face) for face in faces])[:-1].astype(np.int32)
    # A fan from each face's first corner, as a quad renders.
    tri_corners = [(start, start + i, start + i + 1)
                   for start, face in zip(face_offset.tolist(), faces) for i in range(1, len(face) - 1)]
    return seams.Snapshot(
        corner_vert=corner_vert,
        face_offset=face_offset,
        uv=np.array([uv for face in uvs for uv in face], np.float32).reshape(-1, 2),
        tri_corners=np.array(tri_corners, np.int32).reshape(-1, 3),
        shown=np.ones(len(faces), bool) if shown is None else np.array(shown, bool),
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
    fields = ('corner_vert', 'face_offset', 'uv', 'tri_corners', 'shown')
    saved = [getattr(snap, name).copy() for name in fields]
    bpy.context.view_layer.objects.active = pair
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        edited = bmesh.from_edit_mesh(pair.data)
        uv_layer = edited.loops.layers.uv["UVMap"]
        for face in edited.faces:
            for loop in face.loops:
                loop[uv_layer].uv = loop[uv_layer].uv * 0.5
        bmesh.update_edit_mesh(pair.data)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.evaluated_depsgraph_get()
    check(all(np.array_equal(getattr(snap, name), was) for name, was in zip(fields, saved)),
          "a snapshot stays as it was while the mesh's UVs are edited")

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
except Exception:
    traceback.print_exc()
    check(False, "exception during the seams test")

finish("painter seams")
