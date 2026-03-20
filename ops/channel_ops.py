import bpy
from bpy.types import Operator
from bpy.props import EnumProperty, StringProperty
from bpy.utils import register_classes_factory

from ..nodes.group_nodes import sync_group_node_sockets
from ..props.channel import CHANNEL_SOCKET_TYPES


def _get_active_tree(context):
    """Return the active PaintSystemNodeTree depending on the current editor."""
    space = context.space_data
    if space and space.type == 'NODE_EDITOR' and hasattr(space, 'edit_tree'):
        tree = space.edit_tree
        if tree and tree.bl_idname == 'PaintSystemNodeTree':
            return tree
    return getattr(context.scene.paint_system, 'active_node_tree', None)


class PAINTSYSTEM_OT_add_channel(Operator):
    bl_idname = "paint_system.add_channel"
    bl_label = "Add Channel"
    bl_description = "Add a new channel to the Paint System node tree"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name="Name", default="Channel")
    socket_type: EnumProperty(
        name="Type",
        items=CHANNEL_SOCKET_TYPES,
        default='NodeSocketColor',
    )

    @classmethod
    def poll(cls, context):
        return _get_active_tree(context) is not None

    def execute(self, context):
        tree = _get_active_tree(context)
        ch = tree.channels.add()
        ch.name = self.name
        ch.socket_type = self.socket_type
        tree.active_channel_index = len(tree.channels) - 1
        sync_group_node_sockets(tree)
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "name")
        layout.prop(self, "socket_type")


class PAINTSYSTEM_OT_remove_channel(Operator):
    bl_idname = "paint_system.remove_channel"
    bl_label = "Remove Channel"
    bl_description = "Remove the active channel from the Paint System node tree"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        tree = _get_active_tree(context)
        return tree is not None and len(tree.channels) > 0

    def execute(self, context):
        tree = _get_active_tree(context)
        idx = tree.active_channel_index
        tree.channels.remove(idx)
        tree.active_channel_index = min(idx, len(tree.channels) - 1)
        sync_group_node_sockets(tree)
        return {'FINISHED'}


class PAINTSYSTEM_OT_move_channel(Operator):
    bl_idname = "paint_system.move_channel"
    bl_label = "Move Channel"
    bl_description = "Move the active channel up or down"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(
        name="Direction",
        items=[
            ('UP', "Up", ""),
            ('DOWN', "Down", ""),
        ],
    )

    @classmethod
    def poll(cls, context):
        tree = _get_active_tree(context)
        return tree is not None and len(tree.channels) > 1

    def execute(self, context):
        tree = _get_active_tree(context)
        idx = tree.active_channel_index
        if self.direction == 'UP' and idx > 0:
            tree.channels.move(idx, idx - 1)
            tree.active_channel_index -= 1
        elif self.direction == 'DOWN' and idx < len(tree.channels) - 1:
            tree.channels.move(idx, idx + 1)
            tree.active_channel_index += 1
        sync_group_node_sockets(tree)
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_add_channel,
    PAINTSYSTEM_OT_remove_channel,
    PAINTSYSTEM_OT_move_channel,
)


register, unregister = register_classes_factory(classes)
