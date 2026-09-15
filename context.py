from dataclasses import dataclass

import bpy

from bpy.props import PointerProperty
from bpy.utils import register_classes_factory

from .nodetree.stack_ops import StackItem


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


def get_ps_object(obj) -> bpy.types.Object | None:
    """The mesh Paint System works on for *obj*: itself, or an empty's parent mesh."""
    if obj is None:
        return None
    if obj.type == 'EMPTY' and obj.parent is not None:
        obj = obj.parent
    return obj if obj.type == 'MESH' else None


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
    obj = get_ps_object(getattr(context, 'object', None))
    mat = obj.active_material if obj is not None else None
    if mat is not None and mat.paint_system.tree is not None:
        return mat.paint_system.tree
    tree = context.scene.paint_system.active_node_tree
    if tree is not None and tree.bl_idname == 'PaintSystemNodeTree':
        return tree
    return None


@dataclass
class PSContext:
    """Everything a panel or operator resolves from ``bpy.context``, in one read."""
    scene_settings: PaintSystemSceneSettings
    active_object: bpy.types.Object | None
    ps_object: bpy.types.Object | None
    ps_objects: list[bpy.types.Object]
    material: bpy.types.Material | None
    material_settings: PaintSystemMaterialSettings | None
    tree: bpy.types.NodeTree | None
    channel: bpy.types.PropertyGroup | None
    layer: bpy.types.Node | None
    stack_item: StackItem | None


def parse_context(context) -> PSContext:
    active_object = getattr(context, 'object', None)
    ps_object = get_ps_object(active_object)
    ps_objects = []
    for obj in getattr(context, 'selected_objects', None) or ():
        obj = get_ps_object(obj)
        if obj is not None and obj not in ps_objects:
            ps_objects.append(obj)
    material = ps_object.active_material if ps_object is not None else None
    tree = get_active_tree(context)
    channel = tree.active_channel if tree is not None else None
    layer = tree.nodes.active if tree is not None else None
    if not getattr(layer, 'is_layer_node', False):
        layer = None
    stack_item = None
    if layer is not None and channel is not None:
        stack_item = next((item for item in tree.stack(channel.name) if item.node == layer), None)
    return PSContext(
        scene_settings=context.scene.paint_system,
        active_object=active_object,
        ps_object=ps_object,
        ps_objects=ps_objects,
        material=material,
        material_settings=material.paint_system if material is not None else None,
        tree=tree,
        channel=channel,
        layer=layer,
        stack_item=stack_item,
    )
