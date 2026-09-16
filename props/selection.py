"""The selection is document data, and its mask is derived from it (PS-091).

A selection is an ordered list of operations - a box here, a lasso there,
an inversion - stored on the tree. That makes selection undo Blender's own
undo and saves the selection with the file, the way mesh selection works,
and it means nothing has to store the mask: `selection/raster.py` rebuilds
it from the ops, and `ops_hash` tells it when the mask it holds no longer
matches them.

Point lists live in an ID property rather than a collection of typed
point groups. A lasso carries hundreds of points and memfile undo copies
every step; a `CollectionProperty` would allocate one PropertyGroup per
point and copy them all on each push, while an ID property holding a flat
array of floats is one allocation, is written to the file, and is
restored by undo the same way.
"""
import hashlib
import json

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

_IDENTITY = (1.0, 0.0, 0.0, 0.0,
             0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0,
             0.0, 0.0, 0.0, 1.0)


def _flat(values) -> list[float]:
    """*values* as a flat list of rounded floats.

    A `subtype='MATRIX'` property reads back as a `Matrix`, which iterates
    as four `Vector` rows rather than sixteen floats. A `Vector` is a
    sequence without an `__iter__` of its own, so the rows are told apart
    by what is *not* a number rather than by what iterates.
    """
    out = []
    for value in values:
        if isinstance(value, (int, float)):
            out.append(round(float(value), 6))
        else:
            out.extend(round(float(inner), 6) for inner in value)
    return out


class PaintSystemSelectionOp(bpy.types.PropertyGroup):
    """One operation in a selection. Rasterised by `selection/raster.py`."""

    kind: EnumProperty(name="Kind", items=SELECTION_OP_KINDS, default='BOX')
    mode: EnumProperty(name="Mode", items=SELECTION_MODES, default='REPLACE')
    space: EnumProperty(name="Space", items=SELECTION_SPACES, default='UV')

    feather: FloatProperty(
        name="Feather",
        description="Width of the soft edge, in pixels of the space the op was drawn in",
        default=0.0, min=0.0, soft_max=64.0,
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

    def hash_parts(self) -> list:
        """Everything that changes what this op rasterises to.

        Matrices and the region size only matter for a VIEW op, and the
        raster image and transform only for their own kinds, so a UV box
        keeps the same hash whatever the viewport is doing.
        """
        parts = [self.kind, self.mode, self.space,
                 round(self.feather, 4), self.antialias,
                 [round(value, 6) for pair in self.get_points() for value in pair]]
        if self.space == 'VIEW':
            parts.extend([self.through, list(self.region_size),
                          _flat(self.view_matrix), _flat(self.projection_matrix)])
        if self.kind == 'RASTER':
            parts.append(self.raster_image.name_full if self.raster_image else None)
        if self.kind == 'TRANSFORM':
            parts.append(_flat(self.transform))
        return parts


class PaintSystemSelection(bpy.types.PropertyGroup):
    """The selection on one layer image, as the ops that built it."""

    image: PointerProperty(
        name="Image",
        description="The layer image this selection applies to",
        type=bpy.types.Image,
    )
    ops: CollectionProperty(type=PaintSystemSelectionOp)

    feather: FloatProperty(
        name="Feather",
        description="Default width of the soft edge for new operations, in pixels",
        default=0.0, min=0.0, soft_max=64.0,
    )
    antialias: BoolProperty(
        name="Anti-Alias",
        description="Smooth the edge of new operations over one pixel",
        default=True,
    )

    # Set by `selection/raster.py` to the `ops_hash` the mask was built
    # from, so a stale mask is recognised after undo, redo or a reload.
    mask_hash: StringProperty(name="Mask Hash", options={'SKIP_SAVE'})

    @property
    def is_empty(self) -> bool:
        return len(self.ops) == 0

    def add_op(self, kind: str, mode: str = 'REPLACE', space: str = 'UV',
               **values) -> PaintSystemSelectionOp:
        """Append an operation, applying the selection's own defaults.

        A `REPLACE` drops every op before it: nothing earlier can show
        through, so keeping them would only slow the rebuild down.
        """
        if mode == 'REPLACE':
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
        self.mask_hash = ""

    def ops_hash(self) -> str:
        """A digest of every op, in order. Empty string when there are none.

        The mask is derived from exactly this, so two selections with the
        same digest have the same mask and a changed digest means the mask
        has to be rebuilt.
        """
        if not len(self.ops):
            return ""
        payload = json.dumps([op.hash_parts() for op in self.ops],
                             sort_keys=True, separators=(',', ':'))
        return hashlib.sha1(payload.encode('utf-8')).hexdigest()


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
