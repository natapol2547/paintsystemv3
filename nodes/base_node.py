import uuid

from bpy.props import StringProperty

from ..compiler.core import mark_dirty


def mark_tree_dirty(self, context=None):
    """Shared ``update=`` callback for node properties that affect the compiled tree."""
    mark_dirty(self.id_data)


class PaintSystemBaseNode:
    """Mixin for every node in a PaintSystemNodeTree.

    Nodes are pure data. They never create or own shader datablocks; instead
    they implement ``emit(ctx)`` which describes their shader graph in the IR.
    """
    uuid: StringProperty(name="UUID")

    # Layer names are the built-in Node.name, which Blender keeps unique
    # within the tree on every version. Do not redefine ``name`` here: a
    # Python property shadows it and drifts from the name ``nodes.get``
    # looks up before Blender 5.2.

    is_layer_node = False

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == 'PaintSystemNodeTree'

    def init(self, context):
        self.uuid = str(uuid.uuid4())

    def copy(self, node):
        # Called on the new node with the source node; the copy needs its own identity.
        self.uuid = str(uuid.uuid4())

    def update(self):
        # Blender calls this when links attached to this node change.
        mark_dirty(self.id_data)

    def free(self):
        pass

    # -- compiler protocol --------------------------------------------------

    def emit(self, ctx):
        """Append this node's shader graph to ``ctx.ir`` and register outputs."""
        pass

    def hash_parts(self, ctx):
        """Extra data folded into this node's subtree hash (see CompileContext)."""
        return []
