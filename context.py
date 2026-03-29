import bpy

from bpy.props import PointerProperty
from bpy.utils import register_classes_factory


def is_ps_node_tree_poll(self, node_tree: bpy.types.NodeTree):
    return node_tree.bl_idname == 'PaintSystemNodeTree'


class PaintSystem(bpy.types.PropertyGroup):
    image: PointerProperty(type=bpy.types.Image)
    active_node_tree: PointerProperty(
        type=bpy.types.NodeTree,
        name="Active Paint System Tree",
        description="Currently active Paint System node tree",
        poll=is_ps_node_tree_poll,
    )


classes = (
    PaintSystem,
)

_register, _unregister = register_classes_factory(classes)


def register():
    _register()
    bpy.types.Scene.paint_system = PointerProperty(type=PaintSystem)


def unregister():
    del bpy.types.Scene.paint_system
    _unregister()
