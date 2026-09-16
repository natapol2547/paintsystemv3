"""The selection is document data, and its mask is derived from it (PS-091).

A selection is an ordered list of operations - a box here, a lasso there,
an inversion - stored on the tree. That makes selection undo Blender's own
undo and saves the selection with the file, the way mesh selection works,
and it means nothing has to store the mask: `selection/raster.py` rebuilds
it from the ops, and caches it under `prefix_digests`, a digest of exactly
what the mask depends on.

Point lists live in an ID property rather than a collection of typed
point groups. A lasso carries hundreds of points and memfile undo copies
every step; a `CollectionProperty` would allocate one PropertyGroup per
point and copy them all on each push, while an ID property holding a flat
array of floats is one allocation, is written to the file, and is
restored by undo the same way.

Digests are never written to the file or to an ID property, so changing
what goes into them needs no versioning; `tests/test_selection_model.py`
pins a few so the change is at least deliberate.
"""
import hashlib
import struct
from array import array

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntVectorProperty, PointerProperty)

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
"""Kinds that, with mode `REPLACE`, hide every op before them."""

MODELESS_KINDS = frozenset(('INVERT', 'TRANSFORM'))
"""Kinds that act on the mask before them instead of combining a shape
with it. Their mode means nothing; `add_op` stores `ADD`."""

SPACELESS_KINDS = frozenset(('ALL', 'INVERT'))
"""Kinds whose result does not depend on the space they were made in."""

OUTLINELESS_KINDS = frozenset(('ALL', 'INVERT', 'TRANSFORM'))
"""Kinds with no outline, so points, feather and anti-alias mean nothing."""

KIND_CODES = {item[0]: index for index, item in enumerate(SELECTION_OP_KINDS)}
MODE_CODES = {item[0]: index for index, item in enumerate(SELECTION_MODES)}
SPACE_CODES = {item[0]: index for index, item in enumerate(SELECTION_SPACES)}
"""Stable small integers for the enum items, packed into digests."""

DIGEST_TAG = b"PS-091 selection mask 1"
"""Version of the digest format; changing it gives every selection new cache keys."""

DIGEST_SIZE = 20
"""Bytes in each BLAKE2b prefix digest."""

_IDENTITY = (1.0, 0.0, 0.0, 0.0,
             0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0,
             0.0, 0.0, 0.0, 1.0)


def _matrix_values(values) -> list[float]:
    """*values* as a flat list of floats.

    A `subtype='MATRIX'` property reads back as a `Matrix`, which iterates
    as four `Vector` rows rather than sixteen floats. A `Vector` is a
    sequence without an `__iter__` of its own, so the rows are told apart
    by what is *not* a number rather than by what iterates.
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

    Well formed is an ID property array of even length. A list of pairs,
    a string, a group, a scalar or an odd length is malformed.
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

    # A VIEW op is redrawn from the view it was made in, so it survives
    # undo, a reload and any later camera move.
    region_size: IntVectorProperty(name="Region Size", size=2, default=(0, 0))
    view_matrix: FloatVectorProperty(
        name="View Matrix", size=16, subtype='MATRIX', default=_IDENTITY)
    projection_matrix: FloatVectorProperty(
        name="Projection Matrix", size=16, subtype='MATRIX', default=_IDENTITY)

    # RASTER: a write-once greyscale image, never modified, so undo only
    # needs the pointer. It is packed right after its write, because an
    # unpacked generated image comes back black after an undo past its
    # creation and a redo (PS-096).
    raster_image: PointerProperty(name="Raster", type=bpy.types.Image)

    # TRANSFORM: the matrix a committed move applied, so the selection
    # follows the content without any pixel being copied (PS-094).
    transform: FloatVectorProperty(
        name="Transform", size=16, subtype='MATRIX', default=_IDENTITY)

    def get_points(self) -> list[tuple[float, float]]:
        """The outline as (x, y) pairs, empty when the op has no outline."""
        flat = self.get(POINTS_KEY)
        if not flat:
            return []
        values = list(flat)
        return list(zip(values[0::2], values[1::2]))

    def set_points(self, points) -> None:
        """Store an iterable of (x, y) pairs as the op's outline."""
        flat = []
        for point in points:
            flat.extend((float(point[0]), float(point[1])))
        self[POINTS_KEY] = flat

    def update_digest(self, digest) -> None:
        """Feed everything that changes what this op rasterises to into *digest*.

        Values go in at full precision, packed with `struct`; the point
        list goes in as its float64 buffer without a copy. What cannot
        change the mask stays out: the mode of an `INVERT` or `TRANSFORM`,
        the space of `ALL` and `INVERT`, the outline, feather and
        anti-alias of kinds without an outline, and the view of a `UV` op.
        A `RASTER` op includes its image's `session_uid`, so an image
        deleted and replaced by another of the same name is a new mask.
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
            # the op), so a marker count is enough to key the digest.
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
        if kind == 'RASTER':
            image = self.raster_image
            name = image.name_full.encode('utf-8') if image is not None else b""
            digest.update(struct.pack('<IQ', len(name), image.session_uid if image is not None else 0))
            digest.update(name)
        if kind == 'TRANSFORM':
            digest.update(struct.pack('<16d', *_matrix_values(self.transform)))


class PaintSystemSelection(bpy.types.PropertyGroup):
    """The selection of a tree, as the ops that built it.

    It applies to the tree's active layer, whichever that is, and its mask
    is built at the size of that layer's image (`selection/session.py`).
    """

    ops: CollectionProperty(type=PaintSystemSelectionOp)

    feather: FloatProperty(
        name="Feather",
        description="Default width of the soft edge for new operations, in pixels",
        default=0.0, min=0.0, max=FEATHER_MAX, soft_max=64.0,
    )
    antialias: BoolProperty(
        name="Anti-Alias",
        description="Smooth the edge of new operations over one pixel",
        default=True,
    )

    @property
    def is_empty(self) -> bool:
        return len(self.ops) == 0

    def add_op(self, kind: str, mode: str = 'REPLACE', space: str = 'UV',
               **values) -> PaintSystemSelectionOp:
        """Append an operation, applying the selection's own defaults.

        A `REPLACE` of a kind in `REPLACING_KINDS` drops every op before
        it: nothing earlier can show through, so keeping them would only
        slow the rebuild down. `INVERT` and `TRANSFORM` act on what came
        before them, so they never drop anything and are stored with mode
        `ADD` whatever *mode* says.
        """
        if kind in MODELESS_KINDS:
            mode = 'ADD'
        elif mode == 'REPLACE' and kind in REPLACING_KINDS:
            self.ops.clear()
        op = self.ops.add()
        op.kind = kind
        op.mode = mode
        op.space = space
        op.feather = self.feather
        op.antialias = self.antialias
        points = values.pop('points', None)
        for name, value in values.items():
            setattr(op, name, value)
        if points is not None:
            op.set_points(points)
        return op

    def clear(self) -> None:
        self.ops.clear()

    def invert(self) -> None:
        """Invert the selection: drop a trailing `INVERT`, else append one.

        Inverting twice gives back the ops, and so the digest and the cached
        mask, that the first inversion started from.
        """
        if len(self.ops) and self.ops[-1].kind == 'INVERT':
            self.ops.remove(len(self.ops) - 1)
        else:
            self.add_op('INVERT')

    def chain_start(self) -> int:
        """Index of the last op that replaces everything before it, or 0.

        Ops before it cannot show through, so the rasteriser starts there.
        `add_op` already drops them; this covers ops added any other way.
        """
        start = 0
        for index, op in enumerate(self.ops):
            if op.mode == 'REPLACE' and op.kind in REPLACING_KINDS:
                start = index
        return start

    def prefix_digests(self, width: int = 0, height: int = 0, tile: int = 0) -> list[bytes]:
        """One digest per op: of that op and everything that shows through to it.

        Digest ``k`` is the mask after op ``k`` at *width* x *height* for
        UDIM *tile*, so `selection/raster.py` uses them as cache keys and
        finds the longest prefix it has already built. A replacing op
        starts again from the root, which holds only the digest tag, the
        size and the tile, so ops before it do not change any digest from
        it on. 20-byte BLAKE2b.
        """
        root = hashlib.blake2b(DIGEST_TAG + struct.pack('<III', width, height, tile),
                               digest_size=DIGEST_SIZE).digest()
        previous = root
        out = []
        for op in self.ops:
            digest = hashlib.blake2b(digest_size=DIGEST_SIZE)
            digest.update(root if op.mode == 'REPLACE' and op.kind in REPLACING_KINDS else previous)
            op.update_digest(digest)
            previous = digest.digest()
            out.append(previous)
        return out

    def ops_hash(self) -> str:
        """A digest of the ops that affect the mask, from `chain_start` on, in order.

        Empty string when there are none. The mask is derived from exactly
        this, so two selections with the same digest have the same mask at
        any size, and a changed digest means the mask has to be rebuilt.
        """
        if not len(self.ops):
            return ""
        return self.prefix_digests()[-1].hex()


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
