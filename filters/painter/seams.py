# SPDX-License-Identifier: GPL-3.0-or-later
"""The UV islands of the mesh a Painterly layer paints for (PS-053).

A texel belongs to one UV island, and the texels of another island sit
somewhere else on the mesh. So a stroke that runs from one island into
the next would paint a part of the mesh it is nowhere near. The painter
keeps each stroke to the island its centre is on. This module finds the
islands from the mesh, in numpy, and `gpu_passes.texel_map.island_map`
draws them.

- `snapshot` copies what is needed from the evaluated mesh, in the same
  tick as the checks in `filters.layer_plan._seam_surface`. Nothing
  reads the mesh after that. So entering Edit Mode or deleting the object
  while the build runs cannot make it fail.
- Two faces are in one island when they share an edge and agree on the
  UVs at both of its ends. An edge used by one face, or by more than two,
  joins nothing.
- Only faces whose material shows the tree count. Faces of another
  material often reuse the same UV space, and their texels are not this
  layer's to paint.
- The islands are found by union-find on arrays (`_islands`), one round
  per build unit. Each round hooks every root to the smallest root it is
  joined to, then follows the pointers to the end. A million faces take
  about seven rounds.
- The islands are kept per mesh content (`digest`), so a rebuild after a
  stroke below finds them again. Positions are left out of the digest,
  so moving or posing the mesh keeps the entry.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from ...context import tree_slots
from ...gpu_passes import surface
from ...lru import LRUCache
from ..core import Refused

# The largest UV difference treated as the same point, as for surface keys.
SEAM_TOLERANCE = surface.UV_TOLERANCE
# How many meshes keep their islands. An entry costs one int32 per face.
SEAM_ENTRIES = 4


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


@dataclass(frozen=True, eq=False)
class Index:
    """The mesh's UV islands, kept per mesh content."""

    # The island of every face, from 1, or 0 for a face that is not shown.
    face_island: np.ndarray


# Digest of the mesh content to its islands. Least recently used first.
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
    return Snapshot(
        corner_vert=corners['corner_vert'],
        face_offset=corners['face_offset'],
        uv=corners['uv'].reshape(-1, 2),
        tri_corners=tri_corners.reshape(-1, 3),
        shown=slots[np.clip(material, 0, len(slots) - 1)],
    )


def digest(snap: Snapshot) -> bytes:
    """A 16-byte key for everything the islands of *snap* depend on.

    The UVs go in exactly as they are. Rounding them would not make the
    key follow the joins: two UVs within `SEAM_TOLERANCE` of each other
    can round apart, and two further apart can round together, so an
    entry could be found for UVs that make other islands. Exact UVs cost
    a miss when a UV moves by less than the tolerance.
    """
    key = hashlib.blake2b(digest_size=16)
    for array in (snap.corner_vert, snap.face_offset, snap.shown, snap.uv):
        key.update(np.int64(array.size).tobytes())
        key.update(np.ascontiguousarray(array).tobytes())
    return key.digest()


def index_of(snap: Snapshot, progress):
    """The islands of *snap*, from the cache or worked out now.

    This is a generator. It yields *progress* between units, and returns
    an `Index`.
    """
    key = digest(snap)
    cached = _index_cache.touch(key)
    if cached is not None:
        return cached
    yield progress
    first, second = _joins(snap)
    face_island = yield from _islands(len(snap.face_offset), first, second, snap.shown, progress)
    index = Index(face_island=face_island)
    _index_cache[key] = index
    _index_cache.trim(SEAM_ENTRIES)
    return index


def triangle_islands(snap: Snapshot, index: Index) -> np.ndarray:
    """The island of every triangle of *snap*, from the face it is part of."""
    face = np.searchsorted(snap.face_offset, snap.tri_corners[:, 0], side='right') - 1
    return index.face_island[face]


def _next_corners(snap: Snapshot) -> tuple[np.ndarray, np.ndarray]:
    """The face of every corner, and the corner after it around that face."""
    corners, faces = len(snap.corner_vert), len(snap.face_offset)
    sizes = np.diff(np.append(snap.face_offset, corners))
    face_of = np.repeat(np.arange(faces, dtype=np.int32), sizes)
    following = np.arange(1, corners + 1, dtype=np.int32)
    following[snap.face_offset + sizes - 1] = snap.face_offset
    return face_of, following


def _joins(snap: Snapshot) -> tuple[np.ndarray, np.ndarray]:
    """The pairs of shown faces that are in one island, as two arrays of face indices.

    Every corner starts an edge that runs to the next corner of its face.
    Sorting the edges by their two vertices puts the uses of one mesh
    edge next to each other.
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
    far_start = np.where(same, far, following[far])
    far_end = np.where(same, following[far], far)
    uv = snap.uv
    agree = ((np.abs(uv[near] - uv[far_start]).max(axis=1) <= SEAM_TOLERANCE)
             & (np.abs(uv[following[near]] - uv[far_end]).max(axis=1) <= SEAM_TOLERANCE))
    return face_of[near[agree]], face_of[far[agree]]


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


def release() -> None:
    """Forget the kept islands."""
    _index_cache.clear()
