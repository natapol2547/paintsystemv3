from bpy.props import BoolProperty, EnumProperty
from bpy.types import Operator
from bpy.utils import register_classes_factory

from ..context import get_active_tree
from ..selection import session
from ..undo import UNDO_OPTIONS, push_undo, undoable_mode


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
    # Set only by the click item of the selection tools' keymaps
    # (tools/workspace_tools.py); execute ignores it. A button's tooltip
    # names the first keymap item whose properties equal the button's
    # exactly, and the active tool's keymap is searched before Image
    # Paint. Without this property the Selection section's None button
    # would name that click (Left Mouse) instead of Ctrl D while one of
    # the tools is active.
    from_tool_click: BoolProperty(
        name="From Tool Click",
        description="Set by the click of a selection tool; changes nothing",
        options={'HIDDEN', 'SKIP_SAVE'},
    )

    @classmethod
    def description(cls, context, properties):
        # The operator's own properties live on `properties`, not on `cls.bl_rna`.
        return properties.bl_rna.properties['action'].enum_items[properties.action].description

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
