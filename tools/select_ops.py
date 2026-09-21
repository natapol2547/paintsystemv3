"""Rectangle, ellipse and lasso selection in the 3D view (PS-093).

Each operator appends one `VIEW` op to the active tree's selection. A
drag records its outline in region pixels, together with the view it
was drawn in. That is the region size, the view matrix and the window
matrix. The op itself stores the view as object-to-view (`view_matrix @
matrix_world`). So the selection stays on the texels it covered when
the object moves later. The operators do no GPU work. The session's
tick builds the mask after `execute`.

The tool keymap (`workspace_tools`) picks the mode from the modifiers
held when the drag starts. Shift pressed after that makes a box or
ellipse square. Alt pressed after that draws it from its centre. A
modifier held from the start only counts once it has been released and
pressed again. So the Shift of an Add drag does not also make a square.

`execute` works from the stored properties alone, so Adjust Last
Operation and the tests run it without a region.
"""
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, \
    IntVectorProperty
from bpy.types import Operator, PropertyGroup
from bpy.utils import register_classes_factory
from mathutils import Matrix

from ..context import get_active_tree
from ..props.selection import FEATHER_MAX, SELECTION_MODES
from ..selection import raster, session
from ..undo import UNDO_OPTIONS, push_undo
from . import preview, shapes

LASSO_STEP = 2.0
"""Least distance between lasso points, in pixels at a UI scale of 1."""

_SHIFT_KEYS = frozenset(('LEFT_SHIFT', 'RIGHT_SHIFT'))
_ALT_KEYS = frozenset(('LEFT_ALT', 'RIGHT_ALT'))
_MOVE_EVENTS = frozenset(('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'))


def _flat(matrix) -> list[float]:
    """*matrix* as 16 floats in column order, the order a `subtype='MATRIX'` float vector stores."""
    return [value for column in matrix.col for value in column]


class PAINTSYSTEM_PG_tool_point(PropertyGroup):
    co: FloatVectorProperty(name="Point", size=2)


class _ShapeSelect:
    bl_options = UNDO_OPTIONS
    kind = 'BOX'

    mode: EnumProperty(name="Mode", items=SELECTION_MODES, default='REPLACE', options={'SKIP_SAVE'})
    feather: FloatProperty(
        name="Feather",
        description="Width of the soft edge, in screen pixels",
        default=0.0, min=0.0, max=FEATHER_MAX, soft_max=64.0, subtype='PIXEL',
    )
    antialias: BoolProperty(
        name="Anti-Alias",
        description="Smooth the edge over one pixel",
        default=True,
    )
    through: BoolProperty(
        name="Through",
        description="Select faces hidden behind others too",
        default=False,
    )
    points: CollectionProperty(type=PAINTSYSTEM_PG_tool_point, options={'HIDDEN', 'SKIP_SAVE'})
    region_size: IntVectorProperty(size=2, options={'HIDDEN', 'SKIP_SAVE'})
    view_matrix: FloatVectorProperty(size=(4, 4), subtype='MATRIX', options={'HIDDEN', 'SKIP_SAVE'})
    projection_matrix: FloatVectorProperty(size=(4, 4), subtype='MATRIX', options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_TEXTURE' and get_active_tree(context) is not None

    def invoke(self, context, event):
        if context.region_data is None:
            return {'CANCELLED'}
        region = context.region
        # A drag starts where the button went down, before the drag threshold.
        self._start = (event.mouse_prev_press_x - region.x + 0.5, event.mouse_prev_press_y - region.y + 0.5)
        self._end = (event.mouse_region_x + 0.5, event.mouse_region_y + 0.5)
        self._path = [self._start]
        self._step = shapes.lasso_append(self._path, self._end, LASSO_STEP * context.preferences.system.ui_scale)
        self._shift_armed = not event.shift
        self._alt_armed = not event.alt
        self._square = self._centre = False
        self._preview = preview.Preview(context)
        self._preview.update(self._outline())
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _corners(self):
        return shapes.box_corners(self._start, self._end, self._square, self._centre)

    def _outline(self) -> list[tuple[float, float]]:
        if self.kind == 'LASSO':
            return list(self._path)
        low, high = self._corners()
        if self.kind == 'BOX':
            return shapes.box_outline(low, high)
        return shapes.ellipse_outline(low, high)

    def modal(self, context, event):
        if event.type == 'TIMER':
            self._preview.tick()
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._preview.remove()
            return {'CANCELLED'}
        if event.type in _SHIFT_KEYS and event.value == 'RELEASE':
            self._shift_armed = True
        elif event.type in _ALT_KEYS and event.value == 'RELEASE':
            self._alt_armed = True
        square = self._shift_armed and event.shift
        centre = self._alt_armed and event.alt
        changed = (square, centre) != (self._square, self._centre)
        self._square, self._centre = square, centre
        if event.type in _MOVE_EVENTS:
            self._end = (event.mouse_region_x + 0.5, event.mouse_region_y + 0.5)
            changed = True
            if self.kind == 'LASSO':
                self._step = shapes.lasso_append(self._path, self._end, self._step)
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._preview.remove()
            return self._commit(context)
        if changed:
            self._preview.update(self._outline())
        return {'RUNNING_MODAL'}

    def _commit(self, context):
        if self.kind == 'LASSO':
            points = list(self._path)
            if points[-1] != self._end:
                points.append(self._end)
        else:
            points = list(self._corners())
        self.points.clear()
        for point in points:
            self.points.add().co = point
        region, rv3d = context.region, context.region_data
        self.region_size = (region.width, region.height)
        self.view_matrix = rv3d.view_matrix
        self.projection_matrix = rv3d.window_matrix
        return self.execute(context)

    def execute(self, context):
        target, reason = session.resolve_target(context)
        if target is None:
            self.report({'WARNING'}, session.label(session.State(reason=reason)))
            return {'CANCELLED'}
        if target.object is None:
            self.report({'WARNING'}, "No object to select on")
            return {'CANCELLED'}
        points = [tuple(point.co) for point in self.points]
        if shapes.degenerate(self.kind, points):
            return {'CANCELLED'}
        # The op stores the view relative to the object. For a flat
        # object, that view could not be inverted later.
        if not raster.invertible(target.object.matrix_world):
            self.report({'WARNING'}, "Can't select on an object scaled to zero")
            return {'CANCELLED'}
        target.tree.selection.add_op(
            self.kind, self.mode, 'VIEW',
            points=points,
            feather=self.feather,
            antialias=self.antialias,
            through=self.through,
            region_size=tuple(self.region_size),
            view_matrix=_flat(Matrix(self.view_matrix) @ target.object.matrix_world),
            projection_matrix=_flat(Matrix(self.projection_matrix)),
            object=target.object,
            uv_map=target.uv_map,
        )
        push_undo(context, self.bl_label)
        session.notify()
        return {'FINISHED'}

    def cancel(self, context):
        self._preview.remove()


class PAINTSYSTEM_OT_select_box(_ShapeSelect, Operator):
    bl_idname = "paint_system.select_box"
    bl_label = "Rectangle Selection"
    bl_description = "Select a rectangle of the painted surface"
    kind = 'BOX'


class PAINTSYSTEM_OT_select_ellipse(_ShapeSelect, Operator):
    bl_idname = "paint_system.select_ellipse"
    bl_label = "Ellipse Selection"
    bl_description = "Select an ellipse of the painted surface"
    kind = 'ELLIPSE'


class PAINTSYSTEM_OT_select_lasso(_ShapeSelect, Operator):
    bl_idname = "paint_system.select_lasso"
    bl_label = "Lasso Selection"
    bl_description = "Select a free-hand outline of the painted surface"
    kind = 'LASSO'


classes = (
    PAINTSYSTEM_PG_tool_point,
    PAINTSYSTEM_OT_select_box,
    PAINTSYSTEM_OT_select_ellipse,
    PAINTSYSTEM_OT_select_lasso,
)

register, unregister = register_classes_factory(classes)
