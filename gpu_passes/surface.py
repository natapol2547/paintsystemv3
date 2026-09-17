"""Content keys for the evaluated surface of an object (PS-092, PS-093).

A surface key is a 16-byte BLAKE2b digest of what a per-object GPU cache
is built from: evaluated positions, corner vertices, face offsets, one UV
map, material indices and the attributes that shape corner normals. The
world matrix is not part of it. Caches keyed by it survive the events that
report a geometry update without changing the surface: a texture paint
stroke on 5.3, the undo of a stroke, entering Texture Paint.

Three costs, cheapest first:

- `peek_key` (draw callbacks): the last key and whether it is fresh.
  Fresh means the entry was resolved, nothing marked it suspect since, and
  the identity token (counts and data pointers of the evaluated mesh) is
  unchanged. Microseconds; never reads arrays.
- `resolve_key` (timers, operators): a fresh key as it is; otherwise read
  the arrays through the attribute API and compare them with the ones the
  key was made from. Only a real change pays for the hash. About 25 ms to
  read and compare at a million triangles on 5.2.
- `mark_suspect` (handlers): a flag. Handlers never evaluate.

The arrays are read from attributes rather than `MeshLoop.vertex_index`
and `MeshPolygon.material_index`, which cost 5 to 25 times as much. An
entry keeps its arrays for the next compare, so at most `ENTRY_LIMIT`
entries are kept, the least recently resolved dropped first. A dropped
entry costs one read and hash on its next resolve and gives the same key,
so nothing keyed by it has to be rebuilt.

An entry also records that it was resolved when there is no surface: a
non-mesh, an object in Edit Mode, or an evaluated mesh without the UV map
(a Remesh modifier drops UVs). `peek_key` then reports that None key as
fresh until the token changes, so a draw callback caches its empty result
instead of asking again on every redraw.
"""
import hashlib
import logging

import bpy
import numpy as np

log = logging.getLogger(__name__)

UV_TOLERANCE = 1e-6
"""Largest UV difference treated as no change. Blender 4.2 to 4.5 re-evaluate
a Subdivision Surface with UVs that differ by up to 6e-8 each time."""

KEY_SIZE = 16
"""Bytes in a surface key."""

ENTRY_LIMIT = 8
"""Entries kept, each with its arrays: about 35 MB per entry at a million triangles."""


class _Entry:
    __slots__ = ('key', 'token', 'arrays', 'suspect', 'resolved')

    def __init__(self):
        self.key = None
        self.token = None
        self.arrays = None
        self.suspect = True
        self.resolved = False


# (object session_uid, UV map) -> entry; insertion order is least recently resolved first.
_entries: dict[tuple[int, str], _Entry] = {}
_requests: set[tuple[int, str]] = set()

_CUSTOM_NORMAL_FORMATS = {
    'INT16_2D': (np.int16, 2, 'value'),
    'FLOAT_VECTOR': (np.float32, 3, 'vector'),
}
"""How to read a `custom_normal` attribute: 4.5+ encodes them as INT16_2D on corners."""

_OPTIONAL_ATTRIBUTES = (
    ('material_index', np.int32),
    ('sharp_face', bool),
    ('sharp_edge', bool),
)


def _first_pointer(collection) -> int:
    return collection[0].as_pointer() if len(collection) else 0


def _evaluated_mesh(obj, depsgraph) -> bpy.types.Mesh | None:
    if obj is None or obj.type != 'MESH' or obj.mode == 'EDIT':
        return None
    mesh = obj.evaluated_get(depsgraph).data
    return mesh if isinstance(mesh, bpy.types.Mesh) else None


def _token(mesh: bpy.types.Mesh, uv_map: str) -> tuple:
    """Counts and data pointers: equal tokens are the same arrays unless something wrote into them."""
    attributes = mesh.attributes
    position = attributes.get('position')
    corner_vert = attributes.get('.corner_vert')
    uv = attributes.get(uv_map)
    return (len(mesh.vertices), len(mesh.loops), len(mesh.polygons), mesh.as_pointer(),
            _first_pointer(position.data) if position is not None else 0,
            _first_pointer(corner_vert.data) if corner_vert is not None else 0,
            _first_pointer(mesh.polygons),
            _first_pointer(uv.data) if uv is not None else 0)


def _read_custom(attribute) -> np.ndarray:
    """A custom normal attribute's domain and values as bytes, whatever its format."""
    dtype, width, prop = _CUSTOM_NORMAL_FORMATS.get(attribute.data_type, (np.float32, 3, 'vector'))
    values = np.empty(len(attribute.data) * width, dtype)
    attribute.data.foreach_get(prop, values)
    return np.concatenate([np.frombuffer(attribute.domain.encode(), np.uint8), values.view(np.uint8)])


def _read(mesh: bpy.types.Mesh, uv_map: str) -> dict[str, np.ndarray] | None:
    """The arrays a key is made from, or None when *uv_map* is not a corner UV map of *mesh*."""
    attributes = mesh.attributes
    uv = attributes.get(uv_map)
    if uv is None or uv.domain != 'CORNER' or uv.data_type != 'FLOAT2':
        return None
    arrays = {
        'position': np.empty(len(mesh.vertices) * 3, np.float32),
        'corner_vert': np.empty(len(mesh.loops), np.int32),
        'face_offset': np.empty(len(mesh.polygons), np.int32),
        'uv': np.empty(len(mesh.loops) * 2, np.float32),
    }
    attributes['position'].data.foreach_get('vector', arrays['position'])
    attributes['.corner_vert'].data.foreach_get('value', arrays['corner_vert'])
    mesh.polygons.foreach_get('loop_start', arrays['face_offset'])
    uv.data.foreach_get('vector', arrays['uv'])
    custom = attributes.get('custom_normal')
    if custom is not None:
        arrays['custom_normal'] = _read_custom(custom)
    elif getattr(mesh, 'has_custom_normals', False):
        # 4.2 to 4.4 keep custom normals outside the attribute API.
        arrays['corner_normal'] = np.empty(len(mesh.loops) * 3, np.float32)
        mesh.corner_normals.foreach_get('vector', arrays['corner_normal'])
    for name, dtype in _OPTIONAL_ATTRIBUTES:
        attribute = attributes.get(name)
        if attribute is not None:
            arrays[name] = np.empty(len(attribute.data), dtype)
            attribute.data.foreach_get('value', arrays[name])
    if 'sharp_edge' in arrays:
        # Which edge is sharp depends on the edge order too.
        arrays['edge_verts'] = np.empty(len(mesh.edges) * 2, np.int32)
        attributes['.edge_verts'].data.foreach_get('value', arrays['edge_verts'])
    return arrays


def _same(a: dict, b: dict) -> bool:
    if a.keys() != b.keys():
        return False
    for name, array in a.items():
        other = b[name]
        if np.array_equal(array, other):
            continue
        if name != 'uv' or array.shape != other.shape:
            return False
        if np.abs(array - other).max() > UV_TOLERANCE:
            return False
    return True


def _digest(arrays: dict, uv_map: str) -> bytes:
    digest = hashlib.blake2b(digest_size=KEY_SIZE)
    digest.update(uv_map.encode())
    for name in sorted(arrays):
        array = arrays[name]
        digest.update(name.encode())
        digest.update(np.int64(array.size).tobytes())
        digest.update(memoryview(array).cast('B'))
    return digest.digest()


def mark_suspect(session_uid: int | None = None) -> None:
    """The surface of one object, or of every object, may have changed. For handlers."""
    for (uid, _), entry in _entries.items():
        if session_uid is None or uid == session_uid:
            entry.suspect = True


def forget() -> None:
    """Drop every entry and request; after a file read, whose objects are new."""
    _entries.clear()
    _requests.clear()


def peek_key(obj: bpy.types.Object, uv_map: str, depsgraph) -> tuple[bytes | None, bool]:
    """The last resolved key and whether it is still fresh. For draw callbacks.

    `(None, False)` for an object and map never resolved or dropped since;
    `(None, True)` for one resolved without a surface whose token held.
    """
    entry = _entries.get((obj.session_uid, uv_map))
    if entry is None or not entry.resolved:
        return None, False
    if entry.suspect:
        return entry.key, False
    mesh = _evaluated_mesh(obj, depsgraph)
    token = None if mesh is None else _token(mesh, uv_map)
    return entry.key, token == entry.token


def resolve_key(obj: bpy.types.Object, uv_map: str, depsgraph=None) -> bytes | None:
    """The current key of *obj*'s evaluated surface with UV map *uv_map*. For timers and operators.

    *uv_map* is a name, never '' (see `texel_map.resolve_uv_map`). None
    for a non-mesh, an object in Edit Mode or an evaluated mesh without
    that corner UV map.
    """
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    ident = (obj.session_uid, uv_map)
    entry = _entries.pop(ident, None) or _Entry()
    _entries[ident] = entry
    while len(_entries) > ENTRY_LIMIT:
        del _entries[next(iter(_entries))]
    entry.resolved = True
    mesh = _evaluated_mesh(obj, depsgraph)
    if mesh is None:
        entry.key = entry.arrays = entry.token = None
        entry.suspect = False
        return None
    token = _token(mesh, uv_map)
    if not entry.suspect and entry.token == token:
        return entry.key
    arrays = _read(mesh, uv_map)
    if arrays is None:
        entry.key = entry.arrays = None
    elif entry.arrays is None or not _same(arrays, entry.arrays):
        entry.key = _digest(arrays, uv_map)
        entry.arrays = arrays
    entry.token = token
    entry.suspect = False
    return entry.key


def request(obj: bpy.types.Object, uv_map: str) -> None:
    """Resolve on the next timer tick. Safe from draw callbacks."""
    _requests.add((obj.session_uid, uv_map))
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.0)


def _tick() -> None:
    requests = list(_requests)
    _requests.clear()
    by_uid = {obj.session_uid: obj for obj in bpy.data.objects}
    changed = False
    for uid, uv_map in requests:
        obj = by_uid.get(uid)
        if obj is None:
            continue
        before = _entries.get((uid, uv_map))
        before = (before.resolved, before.key) if before is not None else (False, None)
        changed |= (True, resolve_key(obj, uv_map)) != before
    if changed:
        _surfaces_changed()


def _surfaces_changed() -> None:
    """Redraw what draws from a surface, and let the selection check its masks."""
    # Imported here: the selection package imports this one.
    from ..selection import session

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                area.tag_redraw()
    session.notify()


def release() -> None:
    """Forget everything and stop the timer; on unregister."""
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    forget()
