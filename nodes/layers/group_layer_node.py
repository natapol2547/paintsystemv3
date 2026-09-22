import bpy

from bpy.types import NodeTree
from bpy.props import PointerProperty
from bpy.utils import register_classes_factory

from ..base_node import PaintSystemBaseNode
from ...common import blender_icon, icon_kwargs
from ...props.channel import channel_socket_specs
from ...nodetree.stack_ops import channel_sockets, tree_references
from ...nodetree.tree import sync_sockets
from ...compiler.core import compile_tree, compile_wrapped_tree, mark_dirty


def is_ps_node_tree_poll(self, node_tree: bpy.types.NodeTree):
    return node_tree.bl_idname == 'PaintSystemNodeTree' and not tree_references(node_tree, self.id_data)


def _on_tree_changed(self, context):
    self.sync_sockets()
    mark_dirty(self.id_data)


class PaintSystemGroupLayerNode(PaintSystemBaseNode, bpy.types.NodeCustomGroup):
    """A layer that wraps another PaintSystemNodeTree.

    Each channel of the wrapped tree becomes one RGBA input and one output
    on this node. At compile time the wrapped tree is compiled first and
    instanced as a ShaderNodeGroup.
    """
    bl_idname = 'PaintSystemGroupLayerNode'
    bl_label = 'Group'
    bl_icon = blender_icon('NODETREE')
    bl_width_default = 200
    header_color = (0.285, 0.335, 0.49)

    node_tree: PointerProperty(
        type=NodeTree,
        name="Node Tree",
        description="The Paint System tree this group node wraps",
        update=_on_tree_changed,
        poll=is_ps_node_tree_poll,
    )

    def poll_instance(self, node_tree):
        return not tree_references(self.node_tree, node_tree)

    def init(self, context):
        super().init(context)
        self.sync_sockets()

    def sync_sockets(self):
        if not self.node_tree:
            return
        specs = channel_socket_specs(self.node_tree.channels)
        sync_sockets(self.inputs, specs)
        sync_sockets(self.outputs, specs)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.template_ID(self, "node_tree")
        row.operator("paint_system.edit_node_group", text="", **icon_kwargs('NODETREE'))

    def draw_label(self):
        super().draw_label()
        if self.node_tree:
            return self.node_tree.name
        return "Group"

    # -- compiler -----------------------------------------------------------------

    def hash_parts(self, ctx):
        """Compile the wrapped tree and return its fingerprint.

        So an edit inside the wrapped tree changes this node's subtree hash.
        """
        if not self.node_tree:
            return []
        return [compile_wrapped_tree(self.node_tree)]

    def emit(self, ctx):
        child = self.node_tree
        if child is None:
            return
        compile_tree(child)
        gid = ctx.emit_node(self, 'group', 'ShaderNodeGroup',
                            properties={'node_tree': child.compiled})
        for channel, sock in channel_sockets(self.inputs, child.channels):
            ctx.link_channel(sock, channel, gid)
        for channel, sock in channel_sockets(self.outputs, child.channels):
            ctx.set_channel_output(sock, channel, gid)


classes = (
    PaintSystemGroupLayerNode,
)


register, unregister = register_classes_factory(classes)
