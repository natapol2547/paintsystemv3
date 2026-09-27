# SPDX-License-Identifier: GPL-3.0-or-later
"""The UV islands and seams of the mesh a Painterly layer paints for (PS-053).

A texel belongs to one UV island, and the texels of another island sit
somewhere else on the mesh. So a stroke that runs from one island into
the next would paint a part of the mesh it is nowhere near. The painter
keeps each stroke to the island its centre is on, and carries the part
that runs over a seam across to the island on the other side of it.
This module works out both from the mesh, in numpy.
`gpu_passes.texel_map.draw_islands` draws the islands, and
`painter.build` draws what is carried across.

- `snapshot` copies what is needed from the evaluated mesh, in the same
  tick as the checks in `filters.layer_plan._seam_surface`. Nothing
  reads the mesh after that. So entering Edit Mode or deleting the object
  while the build runs cannot make it fail.
- Two faces are in one island when they share an edge and agree on the
  UVs at both of its ends. An edge used by one face, or by more than two,
  joins nothing and carries nothing.
- Only faces whose material shows the tree count. Faces of another
  material often reuse the same UV space, and their texels are not this
  layer's to paint.
- The islands are found by union-find on arrays (`_islands`), one round
  per build unit. Each round hooks every root to the smallest root it is
  joined to, then follows the pointers to the end. A million faces take
  about seven rounds.
- An edge whose two faces do not agree on its UVs is a seam, and it can
  be crossed both ways (`_crossings`). Crossing from the near face to
  the far one unfolds the far face onto the near face's side of the
  edge, as the mesh would lie flat there (`crossings`). The map follows
  the shapes of the triangle on each side of the edge, in the world and
  in UV. It takes the near edge onto the far edge, and the outside of the
  near face onto the inside of the far one. A turn and a scale alone are
  not enough: UVs are often stretched across an edge by another factor
  than along it, and then a stroke would reach the wrong depth into the
  far island.
- A stamp is carried across an edge when its centre is on the near
  face's side of the edge's line, within `BAND` texels (`candidates`).
  From further out, the stroke reaches that part of the seam across
  another edge.
- Each piece carried across is cut to the far face's side of its edge,
  plus `BAND` texels of margin, and at each end to its half of the angle
  it makes with the next crossing along the seam (`_beyond`). Where
  there is no next crossing, the island map stops it instead. On the far
  island's own texels, a piece paints only where the part of the stamp
  it carries is off the stamp's own island, since the stamp's quad
  paints the rest where it is. So a stamp near the tip of a slit, or the
  end of a hole, is not painted a second time past it. The far island's
  margin takes the part from just inside the near face, the
  continuation that filtering reads past the far edge.
- The islands and crossings are kept per mesh content (`digest`), so a
  rebuild after a stroke below finds them again. Positions are left out
  of the digest, so moving or posing the mesh keeps the entry. Only the
  maps across the seams depend on the positions, and each build works
  them out again from the mesh as it is then. A pose that splits a face
  into other triangles makes a new entry, because each crossing's third
  corners come from the triangles.

Points in the image are complex numbers, x + iy, wherever a stamp is
carried across a seam. Any linear map of the plane is then
``turn * z + flip * conj(z)``: a turn and a scale alone have no flip,
and a mirror has no turn.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from math import sqrt

import numpy as np

from ...context import tree_slots
from ...gpu_passes import surface, texel_map
from ...lru import LRUCache
from ..core import Refused

# The largest UV difference treated as the same point, as for surface keys.
SEAM_TOLERANCE = surface.UV_TOLERANCE
# How many meshes keep their islands and crossings. An entry costs one
# int32 per face and about 100 bytes per crossing.
SEAM_ENTRIES = 4
# A crossing that stretches or shrinks a stroke more than this many times
# in any direction carries nothing across.
SCALE_LIMIT = 16.0
# Texels a piece may paint past the far edge, into the island's margin.
# Filtering at render time reads the margin, so it gets the continuation
# as well as the island's own strokes running on in UV.
BAND = float(texel_map.MARGIN)
# The most corners a piece can have: a quad cut by three lines.
CORNERS = 7
# Pieces smaller than this many square texels are dropped.
MIN_PIECE_AREA = 0.25
# Two unit directions closer than this cannot tell a cap's two sides apart.
MIN_CAP = 1e-6


@dataclass(frozen=True, eq=False)
class Snapshot:
    """What the painter reads from the mesh, copied when the build starts."""

    # The mesh vertex of every corner.
    corner_vert: np.ndarray
    # The first corner of every face, in order.
    face_offset: np.ndarray
    # ``(corners, 2)``: every corner's UV, in the map the layer is built in.
    uv: np.ndarray
    # ``(triangles, 3)``: the corners of the triangles the faces render as.
    tri_corners: np.ndarray
    # Whether each face's material shows the layer's tree.
    shown: np.ndarray
    # ``(vertices, 3)``: every vertex in world space, without the
    # object's location. Only the maps across seams read it.
    position: np.ndarray


@dataclass(frozen=True, eq=False)
class Index:
    """The mesh's UV islands and the ways across its seams, kept per mesh content."""

    # The island of every face, from 1, or 0 for a face that is not shown.
    face_island: np.ndarray
    # ``(crossings, 6)``: the corners at the start and the end of the near
    # edge and the third corner of the triangle along it, then the same
    # three of the far face. Both starts are the same mesh vertex.
    crossing_corners: np.ndarray
    # ``(crossings, 2)``: the island crossed from and the island crossed onto.
    crossing_islands: np.ndarray
    # ``(crossings, 2, 2)``: at the start and at the end of the far edge,
    # the far UV the next crossing along the seam runs on to, or NaN
    # where no crossing follows on.
    beyond: np.ndarray
    # ``(crossings, 2)``: the row of that next crossing, or -1.
    follow: np.ndarray


@dataclass(frozen=True, eq=False)
class Crossings:
    """The crossings of an `Index` one build can use, in texels of its image, as complex points."""

    # ``(width, height)`` of the image.
    size: tuple[int, int]
    near_start: np.ndarray
    near_end: np.ndarray
    near_island: np.ndarray
    far_island: np.ndarray
    far_start: np.ndarray
    # The unit normal of the near edge, pointing into the near face.
    near_normal: np.ndarray
    # How far into the near face a piece can start: `BAND` texels past
    # the far edge, measured on the near side. More than `BAND` where the
    # far side has fewer texels to the length.
    band: np.ndarray
    # A point is carried across as ``far_start + turn * d + flip * conj(d)``,
    # where ``d = point - near_start``.
    turn: np.ndarray
    flip: np.ndarray
    # ``(crossings, 3)``: the lines a piece is cut to, the far edge moved
    # `BAND` texels outward and a cap at each end. A piece keeps the
    # points where ``dot(normal, point) + offset >= 0``. A line that is
    # not there has a zero normal and an offset of 1, which keeps all.
    normals: np.ndarray
    offsets: np.ndarray


@dataclass(frozen=True, eq=False)
class Pieces:
    """The parts of stamps carried across seams, in the order to draw them."""

    # The stamp each piece is part of.
    stamp: np.ndarray
    # ``(pieces, CORNERS)``: the corners on the image, in the atlas, and
    # on the image before they were carried across, as complex points.
    # Only the first `count` are used.
    points: np.ndarray
    coords: np.ndarray
    sources: np.ndarray
    count: np.ndarray
    # The island each piece paints.
    island: np.ndarray


# Digest of the mesh content to its index. Least recently used first.
_index_cache: LRUCache[bytes, Index] = LRUCache()


def snapshot(obj, uv_map: str, tree, depsgraph) -> Snapshot:
    """Copy the corners, UVs and triangles of *obj*'s evaluated mesh.

    *uv_map* names a UV map the evaluated mesh has, which
    `filters.layer_plan._seam_surface` has checked.
    """
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.data
    corners = surface.read_corners(mesh, uv_map)
    if corners is None:
        # Only a mesh in Edit Mode lacks the corner attributes, which the
        # checks refuse first.
        raise Refused(f"The UV seams of '{obj.name}' cannot be read right now")
    tri_corners = np.empty(len(mesh.loop_triangles) * 3, np.int32)
    mesh.loop_triangles.foreach_get('loops', tri_corners)
    material = np.zeros(len(mesh.polygons), np.int32)
    attribute = mesh.attributes.get('material_index')
    if attribute is not None:
        attribute.data.foreach_get('value', material)
    # The evaluated object's slots, because a modifier can add materials,
    # and a face renders with the material its index picks there. A face
    # whose index is past the last slot renders with the last slot's
    # material, so it is clamped the same way. With no slot at all,
    # nothing is shown.
    slots = np.array(tree_slots(evaluated, tree) or [False], dtype=bool)
    # In world space, so an object scaled more along one axis unfolds its
    # faces as they look in the scene.
    position = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get('co', position)
    position = position.reshape(-1, 3) @ np.array(evaluated.matrix_world.to_3x3(), np.float32).T
    return Snapshot(
        corner_vert=corners['corner_vert'],
        face_offset=corners['face_offset'],
        uv=corners['uv'].reshape(-1, 2),
        tri_corners=tri_corners.reshape(-1, 3),
        shown=slots[np.clip(material, 0, len(slots) - 1)],
        position=position,
    )


def digest(snap: Snapshot) -> bytes:
    """A 16-byte key for everything the index of *snap* depends on.

    The UVs go in exactly as they are. Rounding them would not make the
    key follow the joins: two UVs within `SEAM_TOLERANCE` of each other
    can round apart, and two further apart can round together, so an
    entry could be found for UVs that make other islands. Exact UVs cost
    a miss when a UV moves by less than the tolerance.
    """
    key = hashlib.blake2b(digest_size=16)
    for array in (snap.corner_vert, snap.face_offset, snap.shown, snap.uv, snap.tri_corners):
        key.update(np.int64(array.size).tobytes())
        key.update(np.ascontiguousarray(array).tobytes())
    return key.digest()


def index_of(snap: Snapshot, progress):
    """The islands and crossings of *snap*, from the cache or worked out now.

    This is a generator. It yields *progress* between units, and returns
    an `Index`.
    """
    key = digest(snap)
    cached = _index_cache.touch(key)
    if cached is not None:
        return cached
    yield progress
    face_of, corners = _shared_edges(snap)
    agree = _agree(snap.uv, corners)
    face_island = yield from _islands(len(snap.face_offset), face_of[corners[0][agree]],
                                      face_of[corners[2][agree]], snap.shown, progress)
    yield progress
    index = _crossings(snap, face_of, [each[~agree] for each in corners], face_island)
    _index_cache[key] = index
    _index_cache.trim(SEAM_ENTRIES)
    return index


def triangle_islands(snap: Snapshot, index: Index) -> np.ndarray:
    """The island of every triangle of *snap*, from the face it is part of."""
    face = np.searchsorted(snap.face_offset, snap.tri_corners[:, 0], side='right') - 1
    return index.face_island[face]


def crossings(snap: Snapshot, index: Index, width: int, height: int) -> Crossings:
    """The crossings of *index* a build of *width* by *height* texels can use.

    *snap* is the mesh the build reads. It has the content *index* was
    made from, so the corners are the same, and the maps across the seams
    come from its positions. A crossing is left out when a triangle along
    it has no area in the world, or when it would stretch or shrink a
    stroke by more than `SCALE_LIMIT`.
    """
    corners = index.crossing_corners
    uv = snap.uv.astype(np.float64)[corners]
    points = uv[..., 0] * width + 1j * (uv[..., 1] * height)
    position = snap.position.astype(np.float64)[snap.corner_vert[corners]]
    near_start, near_end, near_third, far_start, far_end, far_third = points.T
    along_near, along_far = near_end - near_start, far_end - far_start
    with np.errstate(invalid='ignore', divide='ignore'):
        out_of_near = -_across(position[:, :3], near_start, along_near, near_third)
        into_far = _across(position[:, 3:], far_start, along_far, far_third)
        # The map takes the near edge onto the far edge, and a step out of
        # the near face onto the same step into the far face. Solved from
        # turn * a + flip * conj(a) = b for both.
        det = along_near * np.conj(out_of_near) - np.conj(along_near) * out_of_near
        turn = (along_far * np.conj(out_of_near) - np.conj(along_near) * into_far) / det
        flip = (along_near * into_far - out_of_near * along_far) / det
        # The most and the least the map scales any direction by. A map
        # that is NaN or infinite fails both tests.
        stretch = np.abs(turn) + np.abs(flip)
        squash = np.abs(np.abs(turn) - np.abs(flip))
    kept = np.flatnonzero((stretch <= SCALE_LIMIT) & (squash >= 1.0 / SCALE_LIMIT))
    near_start, near_end, near_third, far_start, far_end, far_third, turn, flip = (
        each[kept] for each in (near_start, near_end, near_third, far_start, far_end, far_third, turn, flip))
    along_near, along_far = near_end - near_start, far_end - far_start
    near_normal = _inward(along_near, near_third - near_start)
    # The map scales areas by |turn|² - |flip|² and takes the near edge
    # onto the far edge, so a step one texel into the near face goes this
    # deep out of the far face. Where that is less than a texel, the band
    # starts further into the near face than `BAND`.
    depth = np.abs(np.abs(turn) ** 2 - np.abs(flip) ** 2) * np.abs(along_near) / np.abs(along_far)
    band = BAND / np.minimum(depth, 1.0)
    count = len(kept)
    normals = np.zeros((count, 3), complex)
    offsets = np.ones((count, 3))
    normals[:, 0] = _inward(along_far, far_third - far_start)
    offsets[:, 0] = BAND - _dot(normals[:, 0], far_start)
    # A cap at each end halves the angle between this far edge and the
    # next crossing's, so where both carry a stamp across, each paints
    # its own side and neither paints over the other. A next crossing
    # this image leaves out carries nothing, so it gets no cap.
    carried = np.zeros(len(index.crossing_corners), bool)
    carried[kept] = True
    follow = index.follow[kept]
    beyond = np.where((carried[follow] & (follow >= 0))[..., None], index.beyond[kept], np.nan)
    beyond = beyond[..., 0] * width + 1j * (beyond[..., 1] * height)
    with np.errstate(invalid='ignore', divide='ignore'):
        for end, (corner, other) in enumerate(((far_start, far_end), (far_end, far_start))):
            own = (other - corner) / np.abs(other - corner)
            theirs = beyond[:, end] - corner
            normal = own - theirs / np.abs(theirs)
            length = np.abs(normal)
            there = np.flatnonzero(np.isfinite(length) & (length > MIN_CAP))
            normals[there, 1 + end] = normal[there] / length[there]
            offsets[there, 1 + end] = -_dot(normals[there, 1 + end], corner[there])
    islands = index.crossing_islands[kept]
    return Crossings(
        size=(width, height), near_start=near_start, near_end=near_end,
        near_island=islands[:, 0], far_island=islands[:, 1], far_start=far_start,
        near_normal=near_normal, band=band, turn=turn, flip=flip, normals=normals, offsets=offsets,
    )


def carry(crossings: Crossings, crossing: np.ndarray, points: np.ndarray) -> np.ndarray:
    """*points* carried across *crossing*, one crossing per row of *points*."""
    offset = points - crossings.near_start[crossing][:, None]
    return (crossings.far_start[crossing][:, None] + crossings.turn[crossing][:, None] * offset
            + crossings.flip[crossing][:, None] * np.conj(offset))


def candidates(crossings: Crossings, centres: np.ndarray, owners: np.ndarray,
               size: int) -> tuple[np.ndarray, np.ndarray]:
    """The crossings each stamp may carry paint over, as ``(stamps, crossings)``.

    *centres* are the stamps' centres, *owners* their islands, and *size*
    their side. At any angle a stamp covers no texel further than half
    its diagonal from its centre, and a piece can start `Crossings.band`
    texels into the near face. A crossing counts when its near edge
    passes within that reach of the centre, the centre is on the near
    face's side of the edge's line or within `BAND` texels of it, and the
    crossing leaves from the stamp's own island.

    The pairs are sorted by stamp, and within a stamp by the distance to
    the edge, nearest first. That is the order the pieces are drawn in,
    so where two pieces of one stamp overlap, the nearer crossing's shows.
    """
    half = size * sqrt(2.0) / 2.0
    reach = half + crossings.band
    stamps = np.flatnonzero(owners > 0)
    start, end = crossings.near_start, crossings.near_end
    width, height = crossings.size
    # Edges that no stamp on the image can reach are left out, so the
    # stamps near the image's border are not tested against every edge
    # elsewhere in UV.
    edges = np.flatnonzero((np.maximum(start.real, end.real) >= -reach)
                           & (np.minimum(start.real, end.real) <= width + reach)
                           & (np.maximum(start.imag, end.imag) >= -reach)
                           & (np.minimum(start.imag, end.imag) <= height + reach))
    if len(stamps) == 0 or len(edges) == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    # A grid of cells twice the least reach wide. Points are put along
    # every near edge at most that reach apart, so a centre within the
    # least reach of an edge is within one and a half of one of its
    # points, which is in the centre's cell or in one next to it. A point
    # of an edge that reaches further is filed in as many more cells
    # around its own as the extra reach needs.
    least = half + BAND
    cell = 2.0 * least
    columns, rows = int(width // cell) + 3, int(height // cell) + 3
    length = np.abs(end[edges] - start[edges])
    count = np.ceil(length / least).astype(np.int64) + 1
    edge = np.repeat(edges, count)
    step = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
    along = step / np.repeat(np.maximum(count - 1, 1), count)
    points = start[edge] + along * (end[edge] - start[edge])
    spread = np.ceil((reach[edge] - least) / cell).astype(np.int64)
    side = 2 * spread + 1
    block = side * side
    which = np.repeat(np.arange(len(points)), block)
    at = np.arange(block.sum()) - np.repeat(np.cumsum(block) - block, block)
    shift = (at // side[which] - spread[which]) + 1j * (at % side[which] - spread[which])
    total = len(start)
    filed = np.unique(_cell(points[which] + cell * shift, cell, columns, rows) * total + edge[which])
    filed_cell, filed_edge = np.divmod(filed, total)

    own = _cell(centres[stamps], cell, columns, rows)
    around = (own[:, None] + (np.arange(-1, 2)[:, None] * rows + np.arange(-1, 2)).ravel()).ravel()
    low = np.searchsorted(filed_cell, around, side='left')
    found = np.searchsorted(filed_cell, around, side='right') - low
    stamp = np.repeat(np.repeat(stamps, 9), found)
    crossing = filed_edge[np.arange(found.sum()) - np.repeat(np.cumsum(found) - found, found)
                          + np.repeat(low, found)]
    mine = crossings.near_island[crossing] == owners[stamp]
    pairs = np.unique(stamp[mine] * total + crossing[mine])
    stamp, crossing = np.divmod(pairs, total)
    distance = _distance(centres[stamp], start[crossing], end[crossing])
    inside = _dot(crossings.near_normal[crossing], centres[stamp] - start[crossing])
    near = (distance <= reach[crossing]) & (inside >= -BAND)
    stamp, crossing, distance = stamp[near], crossing[near], distance[near]
    order = np.lexsort((crossing, distance, stamp))
    return stamp[order], crossing[order]


def pieces(crossings: Crossings, corners: np.ndarray, coords: np.ndarray,
           stamp: np.ndarray, crossing: np.ndarray) -> Pieces:
    """What each stamp paints across each of its crossings from `candidates`.

    *corners* and *coords* are the corners of every stamp on the image
    and in the atlas, as ``(stamps, 4)`` complex arrays. Each stamp is
    carried across whole, then cut to the lines of its crossing. The atlas
    coordinates and the corners before they were carried are cut with the
    corners, which is exact because all three are affine maps of the
    brush. So a mirrored crossing also mirrors the brush.
    """
    count = len(stamp)
    points = np.zeros((count, CORNERS), complex)
    atlas = np.zeros((count, CORNERS), complex)
    sources = np.zeros((count, CORNERS), complex)
    points[:, :4] = carry(crossings, crossing, corners[stamp])
    atlas[:, :4] = coords[stamp]
    sources[:, :4] = corners[stamp]
    used = np.full(count, 4)
    for line in range(crossings.normals.shape[1]):
        points, (atlas, sources), used = _clip(points, (atlas, sources), used, crossings.normals[crossing, line],
                                               crossings.offsets[crossing, line])
    kept = _area(points, used) >= MIN_PIECE_AREA
    return Pieces(stamp=stamp[kept], points=points[kept], coords=atlas[kept], sources=sources[kept],
                  count=used[kept], island=crossings.far_island[crossing[kept]])


def _dot(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The dot product of two complex points as plane vectors."""
    return (np.conj(a) * b).real


def _inward(along: np.ndarray, toward: np.ndarray) -> np.ndarray:
    """The unit normal of each edge *along*, on the side the step *toward* goes."""
    normal = 1j * along / np.abs(along)
    return np.where(_dot(normal, toward) > 0.0, normal, -normal)


def _across(corners: np.ndarray, start: np.ndarray, along: np.ndarray, third: np.ndarray) -> np.ndarray:
    """Where a step straight across an edge, towards its triangle's third corner, goes in texels.

    *corners* are the triangle's corners at the edge's start and end and
    its third corner, in the world, as ``(triangles, 3, 3)``. *start* is
    the edge's start in texels, *along* the edge in texels, and *third*
    the third corner in texels. The step is as long as the edge in the
    world, so both sides of one edge measure it alike. A triangle maps
    onto its UVs by one linear map, so this is exact over the triangle.
    """
    edge = corners[:, 1] - corners[:, 0]
    offset = corners[:, 2] - corners[:, 0]
    length = np.einsum('ij,ij->i', edge, edge)
    share = np.einsum('ij,ij->i', offset, edge) / length
    height = np.linalg.norm(offset - share[:, None] * edge, axis=1) / np.sqrt(length)
    return (third - start - share * along) / height


def _side(start: np.ndarray, end: np.ndarray, point: np.ndarray) -> np.ndarray:
    """Twice the signed area from each edge *start* to *end* to *point*, positive when it is left of the edge."""
    edge, offset = end - start, point - start
    return edge[:, 0] * offset[:, 1] - edge[:, 1] * offset[:, 0]


def _cell(points: np.ndarray, cell: float, columns: int, rows: int) -> np.ndarray:
    """The grid cell of each of *points*, with the ones off the grid in its border."""
    x = np.clip(np.floor(points.real / cell), -1, columns - 2).astype(np.int64) + 1
    y = np.clip(np.floor(points.imag / cell), -1, rows - 2).astype(np.int64) + 1
    return x * rows + y


def _distance(points: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """How far each of *points* is from the segment from *start* to *end*."""
    along = end - start
    t = np.clip(_dot(along, points - start) / np.maximum(np.abs(along) ** 2, 1e-300), 0.0, 1.0)
    return np.abs(points - (start + t * along))


def _clip(points, others, count, normal, offset):
    """Cut convex polygons to the side of a line where ``dot(normal, p) + offset >= 0``.

    One polygon and one line per row. *points* is a ``(polygons,
    CORNERS)`` complex array of which the first *count* are used, and
    each array in *others* is cut the same way, as other maps of the same
    corners. Each corner that is kept goes out as it is, followed by the
    point where the edge after it crosses the line, if it does.

    A convex polygon gains at most one corner per line. Only a sliver
    with every corner within rounding of the line could cross it more
    often, and the corners past the last slot are dropped from it. It has
    no area, so `pieces` drops it anyway.
    """
    polygons, width = points.shape
    slot = np.arange(width)
    used = slot < count[:, None]
    after = np.where(slot + 1 < count[:, None], slot + 1, 0)
    side = _dot(normal[:, None], points) + offset[:, None]
    side_after = np.take_along_axis(side, after, axis=1)
    inside = used & (side >= 0.0)
    crosses = used & (((side > 0.0) & (side_after < 0.0)) | ((side < 0.0) & (side_after > 0.0)))
    t = np.where(crosses, side / np.where(crosses, side - side_after, 1.0), 0.0)
    emit = np.stack([inside, crosses], axis=2).reshape(polygons, 2 * width)
    position = np.cumsum(emit, axis=1) - 1
    emit &= position < width
    row, column = np.nonzero(emit)
    position = position[row, column]

    def cut(corners):
        crossed = corners + t * (np.take_along_axis(corners, after, axis=1) - corners)
        out = np.zeros_like(corners)
        out[row, position] = np.stack([corners, crossed], axis=2).reshape(polygons, 2 * width)[row, column]
        return out

    return cut(points), tuple(cut(each) for each in others), emit.sum(axis=1)


def _area(points: np.ndarray, count: np.ndarray) -> np.ndarray:
    """The area of each polygon, whichever way it winds."""
    slot = np.arange(points.shape[1])
    after = np.where(slot + 1 < count[:, None], slot + 1, 0)
    relative = points - points[:, :1]
    twice = (np.conj(relative) * np.take_along_axis(relative, after, axis=1)).imag
    return 0.5 * np.abs(np.where(slot < count[:, None], twice, 0.0).sum(axis=1))


def _sizes(snap: Snapshot) -> np.ndarray:
    """The number of corners of every face."""
    return np.diff(np.append(snap.face_offset, len(snap.corner_vert)))


def _next_corners(snap: Snapshot) -> tuple[np.ndarray, np.ndarray]:
    """The face of every corner, and the corner after it around that face."""
    sizes = _sizes(snap)
    face_of = np.repeat(np.arange(len(sizes), dtype=np.int32), sizes)
    following = np.arange(1, len(snap.corner_vert) + 1, dtype=np.int32)
    following[snap.face_offset + sizes - 1] = snap.face_offset
    return face_of, following


def _shared_edges(snap: Snapshot) -> tuple[np.ndarray, list[np.ndarray]]:
    """The face of every corner, and every edge two shown faces share.

    Each shared edge is given by four corners: the near face's corners at
    its start and at its end, then the far face's corners at the same two
    vertices. Every corner starts an edge that runs to the next corner of
    its face. Sorting the edges by their two vertices puts the uses of one
    mesh edge next to each other.
    """
    face_of, following = _next_corners(snap)
    kept = np.flatnonzero(snap.shown[face_of])
    start = snap.corner_vert[kept].astype(np.int64)
    end = snap.corner_vert[following[kept]].astype(np.int64)
    vertices = int(snap.corner_vert.max(initial=-1)) + 1
    edge = np.minimum(start, end) * vertices + np.maximum(start, end)
    order = np.argsort(edge, kind='stable')
    edge, kept = edge[order], kept[order]
    starts = np.flatnonzero(np.concatenate([[True], edge[1:] != edge[:-1]]))
    uses = np.diff(np.append(starts, len(edge)))
    pairs = starts[uses == 2]
    near, far = kept[pairs], kept[pairs + 1]
    # The two faces run along a shared edge in either direction. Pair the
    # far face's corners with the near face's by mesh vertex.
    same = snap.corner_vert[near] == snap.corner_vert[far]
    return face_of, [near, following[near], np.where(same, far, following[far]),
                     np.where(same, following[far], far)]


def _agree(uv: np.ndarray, corners: list[np.ndarray]) -> np.ndarray:
    """Whether the two faces of each shared edge have the same UVs at both of its ends."""
    near_start, near_end, far_start, far_end = corners
    return ((np.abs(uv[near_start] - uv[far_start]).max(axis=1, initial=0.0) <= SEAM_TOLERANCE)
            & (np.abs(uv[near_end] - uv[far_end]).max(axis=1, initial=0.0) <= SEAM_TOLERANCE))


def _islands(faces: int, first: np.ndarray, second: np.ndarray, shown: np.ndarray, progress):
    """Number the islands the joins *first* to *second* make, one round per unit.

    A generator that returns the island of every face. The islands are
    numbered from 1 in the order of their lowest face index, so the
    numbers depend only on the mesh. A face that is not shown gets 0.

    Every face points at a face with a lower or equal index, so the
    pointers never form a loop. A root points at itself.
    """
    parent = np.arange(faces, dtype=np.int32)
    while True:
        first_root, second_root = parent[first], parent[second]
        apart = first_root != second_root
        if not apart.any():
            break
        yield progress
        np.minimum.at(parent, np.maximum(first_root, second_root)[apart],
                      np.minimum(first_root, second_root)[apart])
        while True:
            jumped = parent[parent]
            if np.array_equal(jumped, parent):
                break
            parent = jumped
    island = np.zeros(faces, np.int32)
    roots = parent[shown]
    island[shown] = 1 + np.searchsorted(np.unique(roots), roots)
    return island


def _crossings(snap: Snapshot, face_of: np.ndarray, corners: list[np.ndarray],
               face_island: np.ndarray) -> Index:
    """The `Index` of *snap*: its islands and both ways across each seam edge in *corners*.

    A crossing is dropped when either edge has no length in UV, or when a
    triangle along it has no area in UV, because then its face has no
    side. That edge then carries nothing, as a border would.
    """
    near_start, near_end, far_start, far_end = corners
    six = np.stack([near_start, near_end, _third_corners(snap, near_start, near_end),
                    far_start, far_end, _third_corners(snap, far_start, far_end)], axis=1)
    uv = snap.uv.astype(np.float64)[six]
    p0, p1, p2, q0, q1, q2 = uv.transpose(1, 0, 2)
    near_side, far_side = _side(p0, p1, p2), _side(q0, q1, q2)
    usable = np.flatnonzero(
        (six >= 0).all(axis=1) & np.isfinite(uv).all(axis=(1, 2))
        & (face_of[near_start] != face_of[far_start]) & (near_side != 0.0) & (far_side != 0.0)
        & (np.abs(p1 - p0).max(axis=1, initial=0.0) > SEAM_TOLERANCE)
        & (np.abs(q1 - q0).max(axis=1, initial=0.0) > SEAM_TOLERANCE))
    # Triangles on the same side of their edges are mirrored against each
    # other, since without a mirror the near face's inside would land on
    # the far face's inside.
    mirrored = np.tile(((near_side > 0.0) == (far_side > 0.0))[usable], 2)
    # Whether each crossing's near triangle is left of its edge, for the
    # crossings one way and then the other.
    left = np.concatenate([near_side[usable] > 0.0, far_side[usable] > 0.0])
    six = np.concatenate([six[usable], six[usable][:, [3, 4, 5, 0, 1, 2]]])
    ends = snap.uv.astype(np.float64)[six[:, [0, 1, 3, 4]]]
    islands = face_island[face_of[six[:, [0, 3]]]]
    beyond, follow = _beyond(ends, islands, snap.corner_vert[six[:, :2]])
    kept, islands, place = _twins(ends, islands, mirrored, left)
    follow = np.where(follow >= 0, place[follow], -1)
    return Index(face_island=face_island, crossing_corners=six[kept], crossing_islands=islands,
                 beyond=beyond[kept], follow=follow[kept])


def _third_corners(snap: Snapshot, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """The third corner of the triangle along each edge from corner *start* to *end*, or -1.

    A face renders as triangles that use each of its edges once, so every
    edge of a face has exactly one.
    """
    triangles = snap.tri_corners.astype(np.int64)
    first, second = triangles.ravel(), np.roll(triangles, -1, axis=1).ravel()
    third = np.roll(triangles, -2, axis=1).ravel()
    total = len(snap.corner_vert)
    key = np.minimum(first, second) * total + np.maximum(first, second)
    order = np.argsort(key, kind='stable')
    key, third = key[order], third[order]
    wanted = np.minimum(start, end).astype(np.int64) * total + np.maximum(start, end)
    at = np.minimum(np.searchsorted(key, wanted), len(key) - 1)
    return np.where(key[at] == wanted, third[at], -1)


def _beyond(ends: np.ndarray, islands: np.ndarray, vertices: np.ndarray) -> np.ndarray:
    """Where the next crossing along the seam runs on to from each end of each crossing.

    Two crossings follow on from each other at a mesh vertex when they
    cross between the same two islands and meet there on both sides,
    within `SEAM_TOLERANCE`. An end is linked only when exactly one other
    end matches it and it is the only match of that one too. At a vertex
    where more seams meet, or where a slit closes and both sides are one
    point, nothing follows on, and the island map bounds the piece.
    Returns ``(crossings, 2, 2)``, the partner's far UV at its other end
    or NaN, and ``(crossings, 2)``, the partner's row or -1.
    """
    count = len(ends)
    beyond = np.full((count, 2, 2), np.nan)
    follow = np.full((count, 2), -1, np.int64)
    crossing = np.tile(np.arange(count), 2)
    end = np.repeat([0, 1], count)
    near_point, far_point = ends[crossing, end], ends[crossing, 2 + end]
    open_end = np.flatnonzero(np.abs(near_point - far_point).max(axis=1, initial=0.0) > SEAM_TOLERANCE)
    key = np.stack([islands[crossing, 0], islands[crossing, 1], vertices[crossing, end]], axis=1)
    order = open_end[np.lexsort(key[open_end].T[::-1])]
    key = key[order]
    first, second = [], []
    gap = 1
    # Ends of one group sit next to each other after the sort, so each
    # gap pairs every end with the one that many places on.
    while gap < len(order):
        together = np.flatnonzero((key[gap:] == key[:-gap]).all(axis=1))
        if len(together) == 0:
            break
        a, b = order[together], order[together + gap]
        meet = ((crossing[a] != crossing[b])
                & (np.abs(near_point[a] - near_point[b]).max(axis=1) <= SEAM_TOLERANCE)
                & (np.abs(far_point[a] - far_point[b]).max(axis=1) <= SEAM_TOLERANCE))
        first.append(a[meet])
        second.append(b[meet])
        gap += 1
    if not first:
        return beyond, follow
    a, b = np.concatenate(first), np.concatenate(second)
    matches = np.bincount(np.concatenate([a, b]), minlength=2 * count)
    only = (matches[a] == 1) & (matches[b] == 1)
    a, b = a[only], b[only]
    beyond[crossing[a], end[a]] = ends[crossing[b], 3 - end[b]]
    beyond[crossing[b], end[b]] = ends[crossing[a], 3 - end[a]]
    follow[crossing[a], end[a]] = crossing[b]
    follow[crossing[b], end[b]] = crossing[a]
    return beyond, follow


def _twins(ends: np.ndarray, islands: np.ndarray, mirrored: np.ndarray,
           left: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One crossing of each set drawn exactly over each other in UV, and its islands.

    Stacked UVs, from a Mirror modifier that does not flip them or an
    Array, give crossings that are copies of each other in UV. Where
    islands are stacked, the island map shows the one with the smallest
    number, so the copy kept crosses from and onto the smallest islands of
    its set. A crossing seen from its other end is the same crossing, so
    each is compared with its lower end first. The side its near triangle
    is on, from *left*, tells it from the way back across an edge whose
    far side is the near side end for end. Returns the rows kept, in
    order, their islands, and where each row's copy is among the rows
    kept.
    """
    count = len(ends)
    if count == 0:
        return np.zeros(0, np.int64), islands, np.zeros(0, np.int64)
    start = ends[:, [0, 2]].reshape(count, 4)
    finish = ends[:, [1, 3]].reshape(count, 4)
    rows = np.arange(count)
    differ = start != finish
    first = np.argmax(differ, axis=1)
    swap = differ.any(axis=1) & (finish[rows, first] < start[rows, first])
    # Adding zero turns -0.0 into 0.0, so the two compare as one point.
    key = np.where(swap[:, None], np.concatenate([finish, start], axis=1),
                   np.concatenate([start, finish], axis=1)) + 0.0
    key = np.concatenate([key, mirrored[:, None], (left != swap)[:, None]], axis=1)
    order = np.lexsort(key.T[::-1])
    ranked = key[order]
    group = np.empty(count, np.int64)
    group[order] = np.cumsum(np.concatenate([[True], (ranked[1:] != ranked[:-1]).any(axis=1)])) - 1
    groups = int(group.max()) + 1
    lowest = np.full((groups, 2), np.iinfo(np.int32).max, np.int32)
    np.minimum.at(lowest, group, islands)
    kept = np.full(groups, count)
    np.minimum.at(kept, group, rows)
    kept = np.sort(kept)
    place = np.empty(groups, np.int64)
    place[group[kept]] = np.arange(groups)
    return kept, lowest[group[kept]], place[group]


def release() -> None:
    """Forget the kept islands and crossings."""
    _index_cache.clear()
