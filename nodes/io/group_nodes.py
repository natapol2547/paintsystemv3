
from bpy.types import Node
from bpy.props import BoolProperty
from bpy.utils import register_classes_factory
from ..base_node import PaintSystemBaseNode
from ...common import blender_icon, icon_kwargs
from ...compiler.vector import space_settings, to_world
from ...nodetree.stack_ops import channel_sockets
from ...props.channel import PREVIEW_OUTPUT, channel_alpha_name, interface_socket_specs


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
        # decides between the alpha input and an opaque 1, a cache bakes
        # the inputs at their defaults, which Limit Range sets, and a
        # vector channel's settings pick the layer value its input becomes.
        # So a cache above this node is stale when they change. Keyed by
        # socket and without the names, so renaming or moving a channel,
        # which changes no pixels, keeps the caches. Hiding a value field
        # changes none either.
        return {sock.identifier: [(socket_type, {key: value for key, value in props.items() if key != 'hide_value'})
                                  for _name, socket_type, props in interface_socket_specs([channel])]
                + [space_settings(channel)]
                for channel, sock in channel_sockets(self.outputs, self.id_data.channels)}

    def emit(self, ctx):
        for channel, sock in channel_sockets(self.outputs, self.id_data.channels):
            ctx.set_output(sock, *ctx.channel_base(channel))


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
        tree = self.id_data
        nid = ctx.emit_node(self, 'out', 'NodeGroupOutput')
        previewed = tree.active_channel if tree.preview_channel else None
        for channel, sock in channel_sockets(self.inputs, tree.channels):
            if channel.use_alpha:
                # An unlinked channel is transparent, so it has no colour.
                color, alpha = ctx.upstream(sock) or (None, 0.0)
            else:
                # The stack of a channel without alpha is flattened onto the
                # channel's input, even when the tree has no Group Input node.
                color, alpha = ctx.flattened(sock, channel), None
            if color is not None:
                ctx.link(to_world(ctx, self, f"out:{sock.identifier}", channel, color), nid, channel.name)
                if alpha is not None:
                    ctx.link(alpha, nid, channel_alpha_name(channel.name))
            # The preview shows the layer values, as the layers hold them.
            if channel == previewed:
                with ctx.ir.adding_preview():
                    self._emit_preview(ctx, nid, channel, color, alpha)

    def _emit_preview(self, ctx, nid, channel, color, alpha):
        """Show *channel*'s *color* as light on the Preview output, so the scene's lights do not change it.

        *color* is a ref, or None for a transparent channel. With an
        *alpha*, a checker shows through where the colour is not opaque, as
        an image editor shows transparency.
        """
        if color is not None and channel.type == 'FLOAT':
            # A link into a float socket turns the colour into the value
            # the material's float output gets, so the preview shows that.
            value = ctx.emit_node(self, 'preview:value', 'ShaderNodeMath', properties={'operation': 'ADD'},
                                  inputs={1: {'default_value': 0.0}})
            ctx.link(color, value, 0)
            color = (value, 0)
        if alpha is not None:
            checker = (ctx.emit_node(self, 'preview:checker', 'ShaderNodeTexChecker', inputs={
                'Color1': {'default_value': (0.2, 0.2, 0.2, 1.0)},
                'Color2': {'default_value': (0.4, 0.4, 0.4, 1.0)},
            }), 'Color')
            # A transparent channel has no colour, so only the checker shows.
            color = checker if color is None else ctx.mix_colors(self, 'preview:over', alpha, checker, color)
        emission = ctx.emit_node(self, 'preview', 'ShaderNodeEmission')
        ctx.link(color, emission, 'Color')
        ctx.link((emission, 0), nid, PREVIEW_OUTPUT)


classes = (
    PaintSystemGroupInputNode,
    PaintSystemGroupOutputNode,
)


register, unregister = register_classes_factory(classes)
