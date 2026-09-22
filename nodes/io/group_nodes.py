
from bpy.types import Node
from bpy.props import BoolProperty
from bpy.utils import register_classes_factory
from ..base_node import PaintSystemBaseNode
from ...common import blender_icon, icon_kwargs
from ...nodetree.stack_ops import channel_sockets


class PaintSystemGroupNode(PaintSystemBaseNode):
    header_color = (0.38, 0.26, 0.29)

    def init(self, context):
        super().init(context)
        self.id_data.sync_group_node_sockets()


class PaintSystemGroupInputNode(PaintSystemGroupNode, Node):
    bl_idname = 'PaintSystemGroupInputNode'
    bl_label = 'Group Input'
    bl_icon = blender_icon('GROUP_UVS')

    def emit(self, ctx):
        nid = ctx.emit_node(self, 'in', 'NodeGroupInput')
        for channel, sock in channel_sockets(self.outputs, self.id_data.channels):
            ctx.set_channel_output(sock, channel, nid)


class PaintSystemGroupOutputNode(PaintSystemGroupNode, Node):
    bl_idname = 'PaintSystemGroupOutputNode'
    bl_label = 'Group Output'
    bl_icon = blender_icon('GROUP_UVS')

    is_active_output: BoolProperty(name="Is Active Output", default=False)

    def copy(self, node):
        super().copy(node)
        self.is_active_output = False

    def draw_buttons(self, context, layout):
        if not self.is_active_output:
            warning_box = layout.box()
            warning_box.label(text="Inactive Output", **icon_kwargs('ERROR'))

    def emit(self, ctx):
        nid = ctx.emit_node(self, 'out', 'NodeGroupOutput')
        for channel, sock in channel_sockets(self.inputs, self.id_data.channels):
            ctx.link_channel(sock, channel, nid)


classes = (
    PaintSystemGroupInputNode,
    PaintSystemGroupOutputNode,
)


register, unregister = register_classes_factory(classes)
