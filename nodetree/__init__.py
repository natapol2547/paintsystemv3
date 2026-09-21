import nodeitems_utils
from nodeitems_utils import NodeCategory, NodeItem

from bpy.utils import register_submodule_factory


class PaintSystemNodeCategory(NodeCategory):
    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == 'PaintSystemNodeTree'


def node_categories():
    # Imported here, at registration, not at the top. The layer node
    # package imports ``nodetree.tree``, so it may load while this package
    # is still initialising.
    from ..nodes.layers.registry import layer_types
    layer_items = [NodeItem(cls.bl_idname) for cls in layer_types()]
    return [
        PaintSystemNodeCategory('PAINTSYSTEM_LAYERS', "Layers", items=[
            *layer_items,
            NodeItem('PaintSystemGroupLayerNode'),
        ]),
        PaintSystemNodeCategory('PAINTSYSTEM_IO', "Group", items=[
            NodeItem('PaintSystemGroupInputNode'),
            NodeItem('PaintSystemGroupOutputNode'),
        ]),
    ]


submodules = (
    "tree",
)


_register, _unregister = register_submodule_factory(__name__, submodules)


def register():
    _register()
    nodeitems_utils.register_node_categories('PAINTSYSTEM_NODES', node_categories())


def unregister():
    nodeitems_utils.unregister_node_categories('PAINTSYSTEM_NODES')
    _unregister()
