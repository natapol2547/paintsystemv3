from bpy.types import Operator
from bpy.utils import register_classes_factory

from ..context import node_editor_tree


_GROUP_NODE_ID = 'PaintSystemGroupLayerNode'


def _target_node(context):
    """The group node a button/keymap acted on (draw_buttons sets context.node)."""
    node = getattr(context, 'node', None)
    if node is None:
        node = getattr(context, 'active_node', None)
    return node


def _enter_group(context, node) -> bool:
    if not node or not getattr(node, 'node_tree', None):
        return False
    context.space_data.path.append(node.node_tree, node=node)
    return True


class PAINTSYSTEM_OT_edit_node_group(Operator):
    bl_idname = "paint_system.edit_node_group"
    bl_label = "Edit Node Group"
    bl_description = "Open the wrapped tree for editing"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return node_editor_tree(context) is not None

    def execute(self, context):
        node = _target_node(context)
        if not _enter_group(context, node):
            self.report({'WARNING'}, "No group tree to edit")
            return {'CANCELLED'}
        return {'FINISHED'}


class PAINTSYSTEM_OT_enter_exit_node_group(Operator):
    bl_idname = "paint_system.enter_exit_node_group"
    bl_label = "Enter/Exit Node Group"
    bl_description = "Enter the active group node, or step out if none is active (TAB)"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return node_editor_tree(context) is not None

    def execute(self, context):
        node = getattr(context, 'active_node', None)
        if node and node.bl_idname == _GROUP_NODE_ID and node.node_tree:
            _enter_group(context, node)
        elif len(context.space_data.path) > 1:
            context.space_data.path.pop()
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_edit_node_group,
    PAINTSYSTEM_OT_enter_exit_node_group
)


register, unregister = register_classes_factory(classes)
