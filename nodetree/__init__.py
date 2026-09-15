import nodeitems_utils
from nodeitems_utils import NodeCategory, NodeItem

from bpy.utils import register_submodule_factory


class PaintSystemNodeCategory(NodeCategory):
    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == 'PaintSystemNodeTree'


node_categories = [
    PaintSystemNodeCategory('PAINTSYSTEM_LAYERS', "Layers", items=[
        NodeItem('PaintSystemImageLayerNode'),
        NodeItem('PaintSystemSolidColorLayerNode'),
        NodeItem('PaintSystemFolderLayerNode'),
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
    nodeitems_utils.register_node_categories('PAINTSYSTEM_NODES', node_categories)


def unregister():
    nodeitems_utils.unregister_node_categories('PAINTSYSTEM_NODES')
    _unregister()
