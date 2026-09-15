from bpy.types import Operator
from bpy.props import EnumProperty, StringProperty
from bpy.utils import register_classes_factory

from ..props.channel import CHANNEL_SOCKET_TYPES
from ..common import get_next_unique_name
from ..context import get_active_tree as _get_active_tree


class PAINTSYSTEM_OT_add_channel(Operator):
    bl_idname = "paint_system.add_channel"
    bl_label = "Add Channel"
    bl_description = "Add a new channel to the Paint System node tree"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name="Name", default="Channel")
    type: EnumProperty(
        name="Type",
        items=CHANNEL_SOCKET_TYPES,
        default='COLOR',
    )

    @classmethod
    def poll(cls, context):
        return _get_active_tree(context) is not None

    def execute(self, context):
        tree = _get_active_tree(context)
        tree.create_channel(self.name, self.type)
        return {'FINISHED'}

    def invoke(self, context, event):
        tree = _get_active_tree(context)
        self.name = get_next_unique_name(
            self.name, [channel.name for channel in tree.channels])
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "name")
        layout.prop(self, "type")


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
        tree.delete_active_channel()
        return {'FINISHED'}


class PAINTSYSTEM_OT_move_channel_up(Operator):
    bl_idname = "paint_system.move_channel_up"
    bl_label = "Move Channel Up"
    bl_description = "Move the active channel up"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        tree = _get_active_tree(context)
        return tree is not None and len(tree.channels) > 1 and tree.channels_manager.is_valid_move("UP")

    def execute(self, context):
        tree = _get_active_tree(context)
        channel_manager = tree.channels_manager
        channel_manager.move(channel_manager.active_index,
                             channel_manager.active_index - 1)
        tree.sync_group_node_sockets()
        return {'FINISHED'}


class PAINTSYSTEM_OT_move_channel_down(Operator):
    bl_idname = "paint_system.move_channel_down"
    bl_label = "Move Channel Down"
    bl_description = "Move the active channel down"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        tree = _get_active_tree(context)
        return tree is not None and len(tree.channels) > 1 and tree.channels_manager.is_valid_move("DOWN")

    def execute(self, context):
        tree = _get_active_tree(context)
        channel_manager = tree.channels_manager
        channel_manager.move(channel_manager.active_index,
                             channel_manager.active_index + 1)
        tree.sync_group_node_sockets()
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_add_channel,
    PAINTSYSTEM_OT_remove_channel,
    PAINTSYSTEM_OT_move_channel_up,
    PAINTSYSTEM_OT_move_channel_down,
)


register, unregister = register_classes_factory(classes)
