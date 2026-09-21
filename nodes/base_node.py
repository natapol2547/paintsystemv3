import uuid

import bpy
from bpy.props import StringProperty

from ..compiler.core import mark_dirty
from .header_draw import draw_header


def mark_tree_dirty(self, context=None):
    """``update=`` callback for properties that change the compiled tree."""
    mark_dirty(self.id_data)


class PaintSystemBaseNode:
    """Mixin for every node in a PaintSystemNodeTree.

    Nodes are pure data. They never create or own shader datablocks.
    Instead they implement ``emit(ctx)``, which describes their shader graph
    in the IR.
    """
    uuid: StringProperty(name="UUID")

    # A layer's name is the built-in ``Node.name``, which Blender keeps
    # unique in the tree on every version. Do not redefine ``name`` here. A
    # Python property would hide the built-in one, and before Blender 5.2
    # ``nodes.get`` still looks up the built-in name, so the two can differ.

    # No ``update`` override. When links change, Blender calls
    # ``Node.update`` on every node of the tree and then
    # ``NodeTree.update`` once. That last call compiles.

    is_layer_node = False
    is_folder = False
    # Property names that ``compiler.core._hashed_props`` leaves out of the
    # node's hash.
    ps_unhashed_props: tuple[str, ...] = ()
    # For display only. It is a plain class attribute, not a property, so
    # it stays out of compiler fingerprints.
    header_color = None

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == 'PaintSystemNodeTree'

    def init(self, context):
        self.uuid = str(uuid.uuid4())
        if self.header_color is not None:
            self.use_custom_color = True
            # ``nodes.new()`` calls ``init`` with context=None, so read
            # ``bpy.context`` instead.
            self.color = bpy.context.preferences.themes[0].node_editor.node_backdrop[:3]

    def copy(self, node):
        # Blender calls this on the new node, with the source node as
        # *node*. The copy needs its own uuid.
        self.uuid = str(uuid.uuid4())

    def draw_label(self):
        draw_header(self)
        return self.bl_label

    # -- compiler protocol --------------------------------------------------

    def emit(self, ctx):
        """Append this node's shader graph to ``ctx.ir`` and register outputs."""
        pass

    def hash_parts(self, ctx):
        """Return extra data to add to this node's subtree hash.

        See ``CompileContext.subtree_hash``.
        """
        return []
