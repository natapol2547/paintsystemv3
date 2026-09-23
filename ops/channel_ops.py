from bpy.types import Operator
from bpy.props import EnumProperty, StringProperty
from bpy.utils import register_classes_factory

from ..props.channel import CHANNEL_SOCKET_TYPES, get_next_unique_name
from ..common import icon_kwargs
from ..context import get_active_tree as _get_active_tree
from ..templates import CHANNEL_TEMPLATES, add_template_channel
from ..undo import undo_restores_data


CUSTOM_CHANNEL = ('CUSTOM', "Custom", "A channel with the name and type you pick")
CHANNEL_TEMPLATE_ITEMS = [CUSTOM_CHANNEL] + [
    (key, template.name, template.description) for key, template in CHANNEL_TEMPLATES.items()]


class PAINTSYSTEM_OT_add_channel(Operator):
    bl_idname = "paint_system.add_channel"
    bl_label = "Add Channel"
    bl_options = {'REGISTER', 'UNDO'}

    # A template makes its channel with its options, and connects it in
    # every material that runs the tree (``templates.add_template_channel``).
    template: EnumProperty(name="Template", items=CHANNEL_TEMPLATE_ITEMS, default='CUSTOM',
                           options={'SKIP_SAVE'})
    name: StringProperty(name="Name", default="Channel")
    type: EnumProperty(
        name="Type",
        items=CHANNEL_SOCKET_TYPES,
        default='COLOR',
    )

    @classmethod
    def description(cls, context, properties):
        if properties.template == 'CUSTOM':
            return "Add a channel with the name and type you pick"
        template = CHANNEL_TEMPLATES[properties.template]
        return f"{template.description}. Connected in every material that runs the tree"

    @classmethod
    def poll(cls, context):
        return _get_active_tree(context) is not None

    def execute(self, context):
        tree = _get_active_tree(context)
        if self.template == 'CUSTOM':
            tree.create_channel(self.name, self.type)
            return {'FINISHED'}
        name = CHANNEL_TEMPLATES[self.template].name
        if any(channel.name == name for channel in tree.channels):
            self.report({'ERROR'}, f'The tree already has a "{name}" channel')
            return {'CANCELLED'}
        _channel, connected = add_template_channel(tree, self.template)
        if connected == 0:
            self.report({'INFO'}, f'Added "{name}". No material has a shader node to paint it into')
        return {'FINISHED'}

    def invoke(self, context, event):
        if self.template != 'CUSTOM':
            return self.execute(context)
        tree = _get_active_tree(context)
        self.name = get_next_unique_name(
            self.name, [channel.name for channel in tree.channels])
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        # A template picks the name and type itself.
        if self.template != 'CUSTOM':
            return
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

    def invoke(self, context, event):
        channel = _get_active_tree(context).active_channel
        if channel is None:
            return {'CANCELLED'}
        self.channel_name = channel.name
        self.undo_restores = undo_restores_data(context)
        return context.window_manager.invoke_props_dialog(self, confirm_text="Remove")

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Remove '{self.channel_name}'?", **icon_kwargs('ERROR'))
        if not self.undo_restores:
            layout.label(text="Undo cannot bring it back in this mode.")

    def execute(self, context):
        tree = _get_active_tree(context)
        tree.delete_active_channel()
        return {'FINISHED'}


class ChannelMoveOperator:
    """Move the active channel one row. `offset` is -1 for up and 1 for down."""
    bl_options = {'REGISTER', 'UNDO'}
    offset = 0

    @classmethod
    def poll(cls, context):
        tree = _get_active_tree(context)
        return tree is not None and tree.can_move_active_channel(cls.offset)

    def execute(self, context):
        tree = _get_active_tree(context)
        index = tree.active_channel_index
        tree.move_channel(index, index + self.offset)
        return {'FINISHED'}


class PAINTSYSTEM_OT_move_channel_up(ChannelMoveOperator, Operator):
    bl_idname = "paint_system.move_channel_up"
    bl_label = "Move Channel Up"
    bl_description = "Move the active channel up"
    offset = -1


class PAINTSYSTEM_OT_move_channel_down(ChannelMoveOperator, Operator):
    bl_idname = "paint_system.move_channel_down"
    bl_label = "Move Channel Down"
    bl_description = "Move the active channel down"
    offset = 1


classes = (
    PAINTSYSTEM_OT_add_channel,
    PAINTSYSTEM_OT_remove_channel,
    PAINTSYSTEM_OT_move_channel_up,
    PAINTSYSTEM_OT_move_channel_down,
)


register, unregister = register_classes_factory(classes)
