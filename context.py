from dataclasses import dataclass

import bpy

from bpy.props import CollectionProperty, PointerProperty
from bpy.utils import register_classes_factory

from .nodetree.stack_ops import StackItem
from .props.stencil import PaintSystemStencilMeshBackup


def is_ps_node_tree_poll(self, node_tree: bpy.types.NodeTree):
    return node_tree.bl_idname == 'PaintSystemNodeTree'


class PaintSystemSceneSettings(bpy.types.PropertyGroup):
    active_node_tree: PointerProperty(
        type=bpy.types.NodeTree,
        name="Active Paint System Tree",
        description="Currently active Paint System node tree",
        poll=is_ps_node_tree_poll,
    )
    # Stencil UV maps the selection replaced (`selection/stencil.py`).
    stencil_meshes: CollectionProperty(type=PaintSystemStencilMeshBackup)


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


def layer_uv_layer(obj, layer) -> bpy.types.MeshUVLoopLayer | None:
    """The UV map of *obj*'s mesh that *layer*'s image is placed with, or None when it is missing.

    An empty UV map name renders with the active render UV map, so that
    one is the layer's.
    """
    uv_layers = obj.data.uv_layers
    uv_name = getattr(layer, 'uv_map', '')
    if uv_name:
        return uv_layers.get(uv_name)
    return next((uv for uv in uv_layers if uv.active_render), None)


def update_active_image(context) -> None:
    """Point texture painting at the active layer (PS-060).

    The canvas becomes the layer's ``paint_image``, or none for a locked
    layer or one without an image, and the mesh's active UV map the one the
    layer's image is placed with. In texture paint mode the brush keeps
    alpha when the layer locks it. Called whenever the active layer, tree
    or object changes; never by the compiler. Writes only what differs.

    The live selection applies to the active layer, so this also notifies
    the selection session, whether or not there is a tree (PS-091).
    """
    # Imported here: the session imports this module. The sync it
    # schedules runs after this function returns.
    from .selection import session
    session.notify()
    ps = parse_context(context)
    if ps.tree is None:
        return
    layer = ps.layer
    image_paint = context.scene.tool_settings.image_paint
    if image_paint.mode == 'MATERIAL':
        image_paint.mode = 'IMAGE'
    image = layer.paint_image if layer is not None and not layer.lock_layer else None
    if image_paint.canvas != image:
        image_paint.canvas = image

    if image is not None and ps.ps_object is not None:
        uv_layers = ps.ps_object.data.uv_layers
        uv_layer = layer_uv_layer(ps.ps_object, layer)
        if uv_layer is not None and uv_layers.active != uv_layer:
            uv_layers.active = uv_layer

    if layer is not None and context.mode == 'PAINT_TEXTURE':
        brush = image_paint.brush
        use_alpha = not layer.lock_alpha
        if brush is not None and brush.use_alpha != use_alpha:
            brush.use_alpha = use_alpha
