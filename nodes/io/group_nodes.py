
from bpy.types import Node
from bpy.props import BoolProperty
from bpy.utils import register_classes_factory
from ..base_node import PaintSystemBaseNode
from ...common import blender_icon, icon_kwargs
from ...nodetree.stack_ops import channel_sockets
from ...props.channel import interface_socket_specs


class PaintSystemGroupNode(PaintSystemBaseNode):
    header_color = (0.38, 0.26, 0.29)

    def init(self, context):
        super().init(context)
        self.id_data.sync_group_node_sockets()


class PaintSystemGroupInputNode(PaintSystemGroupNode, Node):
    bl_idname = 'PaintSystemGroupInputNode'
    bl_label = 'Group Input'
    bl_icon = blender_icon('GROUP_UVS')

    def hash_parts(self, ctx):
        # What this node gives depends on the channel options: Use Alpha
        # decides between the alpha input and an opaque 1, and a cache
        # bakes the inputs at their defaults, which Limit Range sets. So a
        # cache above this node is stale when they change. Keyed by socket
        # and without the names, so renaming or moving a channel, which
        # changes no pixels, keeps the caches.
        return {sock.identifier: [spec[1:] for spec in interface_socket_specs([channel])]
                for channel, sock in channel_sockets(self.outputs, self.id_data.channels)}

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
        base = None
        for channel, sock in channel_sockets(self.inputs, self.id_data.channels):
            if channel.use_alpha:
                ctx.link_channel(sock, channel, nid)
                continue
            # The stack of a channel without alpha is flattened onto the
            # channel's input. A Group Input of its own reads that input
            # even when the tree has no Group Input node.
            if base is None:
                base = ctx.emit_node(self, 'base', 'NodeGroupInput')
            ctx.link_flattened(sock, channel, nid, (base, channel.name))


classes = (
    PaintSystemGroupInputNode,
    PaintSystemGroupOutputNode,
)


register, unregister = register_classes_factory(classes)
