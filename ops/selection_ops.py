import bpy
from bpy.props import EnumProperty
from bpy.types import Operator
from bpy.utils import register_classes_factory

from ..context import get_active_tree
from ..selection import session


UNDO_OPTIONS = {'REGISTER', 'UNDO'} if bpy.app.version >= (5, 1, 0) else {'REGISTER'}
"""`bl_options` for operators that edit the selection; pair with `push_undo`.

Before Blender 5.1, undo in texture paint mode steps through the image
undo stack only. A step pushed there for a selection edit restores
nothing and costs the user a Ctrl+Z that does nothing, so those versions
leave out the 'UNDO' flag and `push_undo` pushes the step where it works.
"""


UNDO_MODES = frozenset(('OBJECT', 'PAINT_TEXTURE'))
"""`context.mode` values where selection operators run; their polls check `undoable_mode`.

An edit mode has an undo stack of its own: a step pushed there for a
selection edit restores nothing on any version, and Ctrl+Z spends it.
"""


def undoable_mode(context) -> bool:
    """Whether a selection edit made now gets an undo step that restores it."""
    return context.mode in UNDO_MODES


def push_undo(context, message: str) -> None:
    """Push the undo step `UNDO_OPTIONS` leaves out: before 5.1, outside texture paint mode."""
    if 'UNDO' in UNDO_OPTIONS or context.mode == 'PAINT_TEXTURE':
        return
    bpy.ops.ed.undo_push(message=message)


class PAINTSYSTEM_OT_select_all(Operator):
    bl_idname = "paint_system.select_all"
    bl_label = "(De)select All"
    bl_options = UNDO_OPTIONS

    action: EnumProperty(
        name="Action",
        items=(
            ('SELECT', "All", "Select the whole layer"),
            ('DESELECT', "None", "Clear the selection"),
            ('INVERT', "Invert", "Invert the selection"),
        ),
        default='SELECT',
    )

    @classmethod
    def description(cls, context, properties):
        return cls.bl_rna.properties['action'].enum_items[properties.action].description

    @classmethod
    def poll(cls, context):
        return undoable_mode(context) and get_active_tree(context) is not None

    def execute(self, context):
        selection = get_active_tree(context).selection
        ops = selection.ops
        if self.action == 'SELECT':
            if len(ops) == 1 and ops[0].kind == 'ALL':
                return {'CANCELLED'}
            selection.add_op('ALL')
        elif self.action == 'DESELECT':
            if not len(ops):
                return {'CANCELLED'}
            selection.clear()
        else:
            selection.invert()
        push_undo(context, self.bl_label)
        session.notify()
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_select_all,
)

register, unregister = register_classes_factory(classes)
