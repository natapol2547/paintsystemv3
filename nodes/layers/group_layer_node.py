import bpy

from bpy.types import NodeTree
from bpy.props import PointerProperty
from bpy.utils import register_classes_factory

from ..base_node import PaintSystemBaseNode
from ...nodetree.tree import sync_sockets_to_channels


GROUP_LAYER_COLOR = (0.149763, 0.170138, 0.235291)


def _tree_references(tree, target, _visited=None):
    """Return True if *target* is *tree* or is nested anywhere inside it.

    Walks down through every PaintSystemGroupLayerNode's wrapped tree, guarding
    against already-cyclic data so the recursion always terminates.
    """
    if tree is None:
        return False
    if tree is target:
        return True
    if _visited is None:
        _visited = set()
    key = tree.as_pointer()
    if key in _visited:
        return False
    _visited.add(key)
    for node in tree.nodes:
        if node.bl_idname == 'PaintSystemGroupLayerNode':
            if _tree_references(node.node_tree, target, _visited):
                return True
    return False


def is_ps_node_tree_poll(self, node_tree: bpy.types.NodeTree):
    return node_tree.bl_idname == 'PaintSystemNodeTree' and not _tree_references(node_tree, self.id_data)


class PaintSystemGroupLayerNode(PaintSystemBaseNode, bpy.types.NodeCustomGroup):
    """A layer that wraps another PaintSystemNodeTree (Smart Material / node group).

    The wrapped tree's channels become this node's input/output socket pairs, so
    the same PaintSystemNodeTree can be authored at top level *and* reused nested.
    """
    bl_idname = 'PaintSystemGroupLayerNode'
    bl_label = 'Group'
    bl_icon = 'NODETREE'
    bl_width_default = 200

    node_tree: PointerProperty(
        type=NodeTree,
        name="Node Tree",
        description="The node tree this group node wraps",
        update=lambda self, context: self.sync_sockets(),
        poll=is_ps_node_tree_poll
    )

    def poll_instance(self, node_tree):
        # Prevent circular references: this group wraps self.node_tree, so it
        # can't be placed into a tree that self.node_tree already contains
        # (directly or nested) — that would form a cycle.
        return not _tree_references(self.node_tree, node_tree)

    def init(self, context):
        super().init(context)
        self.use_custom_color = True
        self.color = GROUP_LAYER_COLOR
        self.sync_sockets()

    def copy(self, node):
        super().copy(node)

    # -- channel toggles + socket sync --------------------------------------

    def sync_sockets(self):
        """Reconcile toggles with the nested tree's channels, then sockets."""
        if not self.node_tree:
            return
        channels = self.node_tree.channels
        sync_sockets_to_channels(self.inputs, channels)
        sync_sockets_to_channels(self.outputs, channels)

    def get_channel_input_socket(self, name):
        return self.inputs.get(name)

    def get_channel_output_socket(self, name):
        return self.outputs.get(name)

    # -- ui ------------------------------------------------------------------

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.template_ID(self, "node_tree")

    def draw_label(self):
        if self.node_tree:
            return self.node_tree.name
        return "Group"


classes = (
    PaintSystemGroupLayerNode,
)


register, unregister = register_classes_factory(classes)
