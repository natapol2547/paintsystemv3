import bpy

from bpy.props import PointerProperty
from bpy.utils import register_classes_factory


def is_ps_node_tree_poll(self, node_tree: bpy.types.NodeTree):
    return node_tree.bl_idname == 'PaintSystemNodeTree'


class PaintSystemSceneSettings(bpy.types.PropertyGroup):
    active_node_tree: PointerProperty(
        type=bpy.types.NodeTree,
        name="Active Paint System Tree",
        description="Currently active Paint System node tree",
        poll=is_ps_node_tree_poll,
    )


class PaintSystemMaterialSettings(bpy.types.PropertyGroup):
    tree: PointerProperty(
        type=bpy.types.NodeTree,
        name="Paint System Tree",
        description="Paint System tree driving this material",
        poll=is_ps_node_tree_poll,
    )


classes = (
    PaintSystemSceneSettings,
    PaintSystemMaterialSettings,
)

_register, _unregister = register_classes_factory(classes)


def register():
    _register()
    bpy.types.Scene.paint_system = PointerProperty(type=PaintSystemSceneSettings)
    bpy.types.Material.paint_system = PointerProperty(type=PaintSystemMaterialSettings)


def unregister():
    del bpy.types.Material.paint_system
    del bpy.types.Scene.paint_system
    _unregister()


def get_active_tree(context) -> bpy.types.NodeTree | None:
    """Resolve the tree the UI should act on.

    Node editor: the edited tree. Elsewhere: the active object's active
    material tree, falling back to the scene-level selection.
    """
    space = getattr(context, 'space_data', None)
    if space is not None and space.type == 'NODE_EDITOR':
        tree = getattr(space, 'edit_tree', None)
        if tree is not None and tree.bl_idname == 'PaintSystemNodeTree':
            return tree
    obj = getattr(context, 'object', None)
    mat = obj.active_material if obj is not None else None
    if mat is not None and mat.paint_system.tree is not None:
        return mat.paint_system.tree
    tree = context.scene.paint_system.active_node_tree
    if tree is not None and tree.bl_idname == 'PaintSystemNodeTree':
        return tree
    return None
