from dataclasses import dataclass

import bpy

from bl_ui.properties_paint_common import UnifiedPaintPanel
from bpy.props import CollectionProperty, PointerProperty
from bpy.utils import register_classes_factory

from .gpu_passes.texel_map import resolve_uv_map
from .nodetree.stack_ops import StackItem, tree_references
from .props.stencil import PaintSystemStencilMeshBackup


def is_ps_node_tree_poll(self, node_tree: bpy.types.NodeTree):
    return node_tree.bl_idname == 'PaintSystemNodeTree'


def paint_settings(context):
    """The paint settings that Blender's own brush UI reads in this context.

    From Blender 5.3 they come from the active tool, not from the mode.
    Code that must agree with what the brush would do reads them from
    here, such as the Brush and Color panels and the colour a Fill stores.

    Returns None when the context has no space data. Blender finds the
    mode through the active tool of the space, so there is no answer in a
    background session, a timer, or a script run without an area
    override. A caller that needs an answer there picks the mode itself.
    """
    if getattr(context, 'space_data', None) is None:
        return None
    from_active_tool = getattr(UnifiedPaintPanel, 'paint_settings_from_active_tool', None)
    if from_active_tool is not None:
        return from_active_tool(context)
    return UnifiedPaintPanel.paint_settings(context)


class PaintSystemSceneSettings(bpy.types.PropertyGroup):
    active_node_tree: PointerProperty(
        type=bpy.types.NodeTree,
        name="Active Paint System Tree",
        description="Currently active Paint System node tree",
        poll=is_ps_node_tree_poll,
    )
    # The stencil UV maps that the selection replaced, so they can be put
    # back (`selection/stencil.py`).
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


# A material's group node stores the uuid of the tree it runs under this key.
MATERIAL_GROUP_KEY = "ps_tree_uuid"


def find_material_group_node(material, tree) -> bpy.types.ShaderNodeGroup | None:
    """The group node that runs *tree*'s compiled shader in *material*, or None."""
    if material is None or material.node_tree is None:
        return None
    for node in material.node_tree.nodes:
        if node.bl_idname == 'ShaderNodeGroup' and node.get(MATERIAL_GROUP_KEY) == tree.uuid:
            return node
    return None


def material_input(context, tree, name: str) -> bpy.types.NodeSocket | None:
    """The unlinked input *name* of *tree*'s group node, or None.

    The group node is looked for in the active material of the object
    being painted. Such an input's value is what a channel's stack starts
    from. A nested tree has no group node there, so it gets None.
    """
    obj = get_ps_object(getattr(context, 'object', None))
    group = find_material_group_node(obj.active_material if obj is not None else None, tree)
    socket = group.inputs.get(name) if group is not None else None
    return socket if socket is not None and not socket.is_linked else None


def uses_tree(obj, tree) -> bool:
    """True when one of *obj*'s materials shows *tree*.

    A material shows the tree its ``paint_system.tree`` points at, and
    every tree nested in that one through group layers, because each
    nested tree compiles into the outer tree's group.
    """
    if obj is None or tree is None:
        return False
    for slot in obj.material_slots:
        material = slot.material
        if material is not None and tree_references(material.paint_system.tree, tree):
            return True
    return False


def node_editor_tree(context) -> bpy.types.NodeTree | None:
    """The Paint System tree the context's node editor is editing, or None."""
    space = getattr(context, 'space_data', None)
    if space is None or space.type != 'NODE_EDITOR':
        return None
    tree = getattr(space, 'edit_tree', None)
    if tree is not None and tree.bl_idname == 'PaintSystemNodeTree':
        return tree
    return None


def get_active_tree(context) -> bpy.types.NodeTree | None:
    """The Paint System tree the UI should act on, or None.

    In the node editor this is the edited tree. Elsewhere it is the tree
    of the active object's active material, else the scene's
    `active_node_tree`.
    """
    tree = node_editor_tree(context)
    if tree is not None:
        return tree
    obj = get_ps_object(getattr(context, 'object', None))
    mat = obj.active_material if obj is not None else None
    if mat is not None and mat.paint_system.tree is not None:
        return mat.paint_system.tree
    tree = context.scene.paint_system.active_node_tree
    if tree is not None and tree.bl_idname == 'PaintSystemNodeTree':
        return tree
    return None


def button_layer(context, tree) -> bpy.types.Node | None:
    """The layer a button acts on, or None when that node is not a layer.

    A button that a node draws in the node editor gets that node as
    ``context.node``. Anywhere else the button acts on *tree*'s active
    node.
    """
    node = getattr(context, 'node', None)
    if node is None and tree is not None:
        node = tree.nodes.active
    return node if getattr(node, 'is_layer_node', False) else None


@dataclass
class PSContext:
    """Everything a panel or operator resolves from ``bpy.context``, in one read."""
    ps_object: bpy.types.Object | None
    tree: bpy.types.NodeTree | None
    channel: bpy.types.PropertyGroup | None
    layer: bpy.types.Node | None
    stack_item: StackItem | None


def parse_context(context) -> PSContext:
    ps_object = get_ps_object(getattr(context, 'object', None))
    tree = get_active_tree(context)
    channel = tree.active_channel if tree is not None else None
    layer = tree.nodes.active if tree is not None else None
    if not getattr(layer, 'is_layer_node', False):
        layer = None
    stack_item = None
    if layer is not None and channel is not None:
        stack_item = next((item for item in tree.stack(channel.name) if item.node == layer), None)
    return PSContext(
        ps_object=ps_object,
        tree=tree,
        channel=channel,
        layer=layer,
        stack_item=stack_item,
    )


def layer_uv_layer(obj, layer) -> bpy.types.MeshUVLoopLayer | None:
    """The UV map of *obj*'s mesh that *layer*'s image uses, or None.

    `resolve_uv_map` picks the map: the one the layer names, else the
    active render map. Returns None when that map does not exist.
    """
    name = resolve_uv_map(obj, getattr(layer, 'uv_map', ''))
    return obj.data.uv_layers.get(name) if name else None


def update_active_image(context) -> None:
    """Point texture painting at the active layer.

    Sets the paint canvas to the layer's ``paint_image``. A locked layer,
    or a layer without an image, gets no canvas. Makes the UV map that
    the layer's image uses the mesh's active UV map. In texture paint
    mode, sets the brush's `use_alpha` to the opposite of the layer's
    `lock_alpha`.

    Call it whenever the active layer, tree or object changes. The
    compiler never calls it. It only writes values that differ.

    The live selection applies to the active layer, so this also notifies
    the selection session, even when there is no tree.
    """
    # Imported here because the session module imports this one. The
    # sync that `notify` schedules runs after this function returns.
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
