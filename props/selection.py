"""Selection data on the tree: an ordered list of operations (PS-091).

A selection is a list of ops, such as a box, a lasso or an inversion.
Because it is document data, Blender's own undo covers it and it is saved
with the file, like mesh selection. The mask is never stored.
`selection/raster.py` rebuilds it from the ops and caches it under
`prefix_digests`, a digest of exactly what the mask depends on.

Points are stored in one ID property holding a flat array of floats, not
in a `CollectionProperty`. A lasso has hundreds of points, and memfile
undo copies the data on every undo push. A collection would allocate and
copy one PropertyGroup per point. The ID property is a single allocation,
and it is saved to the file and restored by undo the same way.

Digests are never saved to the file or stored in an ID property, so
changing what goes into them needs no versioning.
`tests/test_selection_model.py` pins a few so that such a change is
deliberate.
"""
import hashlib
import struct
from array import array
from collections.abc import Callable

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntVectorProperty, PointerProperty, StringProperty)

SELECTION_OP_KINDS = [
    ('BOX', "Box", "Rectangle"),
    ('ELLIPSE', "Ellipse", "Ellipse"),
    ('LASSO', "Lasso", "Free-hand outline"),
    ('FACES', "Faces", "The faces selected on the mesh"),
    ('RASTER', "Raster", "Coverage read from an image, such as a magic wand result"),
    ('TRANSFORM', "Transform", "Carries the selection along with content a transform moved"),
    ('INVERT', "Invert", "Inverts everything before it"),
    ('ALL', "All", "The whole image"),
]

SELECTION_MODES = [
    ('REPLACE', "Replace", "Replace the selection"),
    ('ADD', "Add", "Add to the selection"),
    ('SUBTRACT', "Subtract", "Subtract from the selection"),
    ('INTERSECT', "Intersect", "Keep only what both cover"),
]

SELECTION_SPACES = [
    ('UV', "UV", "Drawn in the image editor, in texel space"),
    ('VIEW', "View", "Drawn in the 3D view, in screen space"),
]

POINTS_KEY = "points"
"""ID property on an op holding its outline as a flat list of x, y floats."""

FEATHER_MAX = 1024.0
"""Widest soft edge an op can carry, in pixels. The rasteriser clamps to it too."""

REPLACING_KINDS = frozenset(('BOX', 'ELLIPSE', 'LASSO', 'FACES', 'RASTER', 'ALL'))
"""Kinds that hide every op before them when their mode is `REPLACE`."""

MODELESS_KINDS = frozenset(('INVERT', 'TRANSFORM'))
"""Kinds that change the mask before them instead of combining a shape
with it. Their mode is ignored, and `add_op` stores `ADD`."""

SPACELESS_KINDS = frozenset(('ALL', 'INVERT'))
"""Kinds whose result does not depend on the space they were made in."""

OUTLINELESS_KINDS = frozenset(('ALL', 'INVERT', 'TRANSFORM'))
"""Kinds with no outline, so points, feather and anti-alias mean nothing."""

KIND_CODES = {item[0]: index for index, item in enumerate(SELECTION_OP_KINDS)}
MODE_CODES = {item[0]: index for index, item in enumerate(SELECTION_MODES)}
SPACE_CODES = {item[0]: index for index, item in enumerate(SELECTION_SPACES)}
"""Stable small integers for the enum items, packed into digests."""

DIGEST_TAG = b"PS-091 selection mask 2"
"""Version of the digest format. Changing it gives every selection new cache keys."""

NO_SURFACE_KEY = b"\xff" * 16
"""Surface key a `VIEW` op adds to its digest when no provider gives one."""

DIGEST_SIZE = 20
"""Bytes in each BLAKE2b prefix digest."""

_IDENTITY = (1.0, 0.0, 0.0, 0.0,
             0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0,
             0.0, 0.0, 0.0, 1.0)


def _matrix_values(values) -> list[float]:
    """Return *values* as a flat list of floats.

    A `subtype='MATRIX'` property reads back as a `Matrix`, which iterates
    as four `Vector` rows, not sixteen floats. A `Vector` has no
    `__iter__` of its own, so a row is detected by not being a number,
    not by being iterable.
    """
    out = []
    for value in values:
        if isinstance(value, (int, float)):
            out.append(float(value))
        else:
            out.extend(float(inner) for inner in value)
    return out


def points_view(points) -> memoryview | None:
    """A points ID property value as a flat numeric buffer, or None when malformed.

    Only an ID property array of even length is well formed. A list of
    pairs, a string, a group, a scalar or an odd length is malformed.
    """
    try:
        view = memoryview(points)
    except TypeError:
        return None
    if view.ndim != 1 or len(view) % 2:
        return None
    return view


class PaintSystemSelectionOp(bpy.types.PropertyGroup):
    """One operation in a selection. Rasterised by `selection/raster.py`."""

    kind: EnumProperty(name="Kind", items=SELECTION_OP_KINDS, default='BOX')
    mode: EnumProperty(name="Mode", items=SELECTION_MODES, default='REPLACE')
    space: EnumProperty(name="Space", items=SELECTION_SPACES, default='UV')

    feather: FloatProperty(
        name="Feather",
        description="Width of the soft edge, in pixels of the space the op was drawn in",
        default=0.0, min=0.0, max=FEATHER_MAX, soft_max=64.0,
    )
    antialias: BoolProperty(
        name="Anti-Alias",
        description="Smooth the edge over one pixel",
        default=True,
    )
    through: BoolProperty(
        name="Select Through",
        description="Ignore occlusion, so the op reaches surfaces hidden behind others",
        default=False,
    )

    # A VIEW op stores the view it was drawn in and is redrawn from it, so
    # it survives undo, a reload and later camera moves. `view_matrix`
    # maps object space to the view at commit time, so the selection stays
    # on the same texels when the object moves later. `projection_matrix`
    # is the region's window matrix. `object` and `uv_map` name the
    # surface the op was drawn on. `uv_map` is always a real map name,
    # never the empty string.
    region_size: IntVectorProperty(name="Region Size", size=2, default=(0, 0))
    view_matrix: FloatVectorProperty(
        name="View Matrix", size=16, subtype='MATRIX', default=_IDENTITY)
    projection_matrix: FloatVectorProperty(
        name="Projection Matrix", size=16, subtype='MATRIX', default=_IDENTITY)
    object: PointerProperty(name="Object", type=bpy.types.Object)
    uv_map: StringProperty(name="UV Map")

    # RASTER: a greyscale image written once and never changed, so undo
    # only needs the pointer. It is packed right after it is written. An
    # unpacked generated image comes back black after undoing past its
    # creation and then redoing (PS-096).
    raster_image: PointerProperty(name="Raster", type=bpy.types.Image)

    # TRANSFORM: the matrix a committed move applied, so the selection
    # follows the content without any pixel being copied (PS-094).
    transform: FloatVectorProperty(
        name="Transform", size=16, subtype='MATRIX', default=_IDENTITY)

    def set_points(self, points) -> None:
        """Store an iterable of (x, y) pairs as the op's outline."""
        flat = []
        for point in points:
            flat.extend((float(point[0]), float(point[1])))
        self[POINTS_KEY] = flat

    def update_digest(self, digest,
                      surface_key: Callable[["PaintSystemSelectionOp"], bytes | None] | None = None) -> None:
        """Add everything that affects the mask this op draws to *digest*.

        Values are packed at full precision with `struct`. The point list
        is added as its float64 buffer, without a copy. Values that cannot
        change the mask are left out. These are the mode of `INVERT` and
        `TRANSFORM`, the space of `ALL` and `INVERT`, the outline, feather
        and anti-alias of kinds without an outline, and the view of a `UV`
        op. A `RASTER` op adds its image's `session_uid`, so an image
        deleted and replaced by another of the same name gives a new mask.

        An outlined `VIEW` op also depends on the surface it was drawn on.
        It adds its UV map name and the key *surface_key* gives for it, or
        `NO_SURFACE_KEY` when there is no provider or no key. Only `VIEW`
        ops call the provider. The object's `session_uid` is left out, so
        the same ops on an identical surface give the same mask.
        """
        kind = self.kind
        outlined = kind not in OUTLINELESS_KINDS
        digest.update(struct.pack(
            '<BBBdB',
            KIND_CODES[kind],
            0 if kind in MODELESS_KINDS else MODE_CODES[self.mode],
            0 if kind in SPACELESS_KINDS else SPACE_CODES[self.space],
            self.feather if outlined else 0.0,
            self.antialias if outlined else False))
        points = self.get(POINTS_KEY) if outlined else None
        view = points_view(points) if points is not None else None
        if points is not None and view is None:
            # Malformed points cannot be built (`selection/raster.py` reports
            # the op), so a marker count is enough for the digest.
            digest.update(struct.pack('<Q', 0xFFFFFFFFFFFFFFFF))
        else:
            count = len(view) if view is not None else 0
            digest.update(struct.pack('<Q', count))
            if count:
                # A list assigned with integers is stored as an int array.
                digest.update(view if view.format == 'd' else array('d', view.tolist()))
        if outlined and self.space == 'VIEW':
            digest.update(struct.pack('<B2i', self.through, *self.region_size))
            digest.update(struct.pack('<32d', *_matrix_values(self.view_matrix),
                                      *_matrix_values(self.projection_matrix)))
            uv_map = self.uv_map.encode('utf-8')
            digest.update(struct.pack('<I', len(uv_map)))
            digest.update(uv_map)
            key = surface_key(self) if surface_key is not None else None
            digest.update(key if key is not None else NO_SURFACE_KEY)
        if kind == 'RASTER':
            image = self.raster_image
            name = image.name_full.encode('utf-8') if image is not None else b""
            digest.update(struct.pack('<IQ', len(name), image.session_uid if image is not None else 0))
            digest.update(name)
        if kind == 'TRANSFORM':
            digest.update(struct.pack('<16d', *_matrix_values(self.transform)))


class PaintSystemSelection(bpy.types.PropertyGroup):
    """The selection of a tree, as the ops that built it.

    It applies to the tree's active layer, whichever layer that is. Its
    mask is built at the size of that layer's image
    (`selection/session.py`).
    """

    ops: CollectionProperty(type=PaintSystemSelectionOp)

    def add_op(self, kind: str, mode: str = 'REPLACE', space: str = 'UV',
               **values) -> PaintSystemSelectionOp:
        """Append an op and set the op properties named in *values*.

        A `points` value is stored with `set_points`. Properties not in
        *values* keep their defaults.

        A `REPLACE` of a kind in `REPLACING_KINDS` removes every op before
        it. Nothing earlier can show through, so keeping them would only
        slow down the rebuild. `INVERT` and `TRANSFORM` act on the ops
        before them, so they never remove anything. They are stored with
        mode `ADD` whatever *mode* says.
        """
        if kind in MODELESS_KINDS:
            mode = 'ADD'
        elif mode == 'REPLACE' and kind in REPLACING_KINDS:
            self.ops.clear()
        op = self.ops.add()
        op.kind = kind
        op.mode = mode
        op.space = space
        points = values.pop('points', None)
        for name, value in values.items():
            setattr(op, name, value)
        if points is not None:
            op.set_points(points)
        return op

    def clear(self) -> None:
        self.ops.clear()

    def invert(self) -> None:
        """Invert the selection by removing a trailing `INVERT` or appending one.

        Inverting twice gives back the same ops, so the digest and the
        cached mask are the same as before too. An empty selection and the
        whole image invert into each other as ops. An empty selection
        becomes `ALL`. A selection ending in an `ALL` that covers
        everything is cleared, instead of being kept as ops whose mask is
        empty.
        """
        ops = self.ops
        if not len(ops):
            self.add_op('ALL')
        elif ops[-1].kind == 'INVERT':
            ops.remove(len(ops) - 1)
        elif ops[-1].kind == 'ALL' and ops[-1].mode in {'REPLACE', 'ADD'}:
            self.clear()
        else:
            self.add_op('INVERT')

    def chain_start(self) -> int:
        """Index of the last op that replaces everything before it, or 0.

        Ops before it cannot show through, so the rasteriser starts there.
        `add_op` already removes them. This covers ops added any other way.
        """
        start = 0
        for index, op in enumerate(self.ops):
            if op.mode == 'REPLACE' and op.kind in REPLACING_KINDS:
                start = index
        return start

    def prefix_digests(self, width: int = 0, height: int = 0, tile: int = 0,
                       surface_key: Callable[[PaintSystemSelectionOp], bytes | None] | None = None,
                       ) -> list[bytes]:
        """One digest per op, covering that op and every op that shows through to it.

        Each digest is a 20-byte BLAKE2b. Digest ``k`` identifies the mask
        after op ``k`` at *width* x *height* for UDIM *tile*.
        `selection/raster.py` uses the digests as cache keys and resumes
        from the longest prefix it has already built. A replacing op starts
        again from the root digest, which holds only the digest tag, the
        size and the tile. So ops before it do not change its digest or
        any later one. *surface_key* gives each `VIEW` op the key of the
        surface it was drawn on (`PaintSystemSelectionOp.update_digest`).
        """
        root = hashlib.blake2b(DIGEST_TAG + struct.pack('<III', width, height, tile),
                               digest_size=DIGEST_SIZE).digest()
        previous = root
        out = []
        for op in self.ops:
            digest = hashlib.blake2b(digest_size=DIGEST_SIZE)
            digest.update(root if op.mode == 'REPLACE' and op.kind in REPLACING_KINDS else previous)
            op.update_digest(digest, surface_key)
            previous = digest.digest()
            out.append(previous)
        return out


classes = (
    PaintSystemSelectionOp,
    PaintSystemSelection,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
