"""Bake a node's output into its cache image.

In the compiled tree, a cached node and everything upstream of it are
replaced by this image. The rest of the tree stays live.

The compiler builds a temporary shader group whose Color and Alpha outputs
are the two halves of the target node's live Color output, ignoring the
node's own cache. A throwaway
material sends that through an Emission shader. Cycles bakes it twice,
once for colour and once for alpha, and numpy merges the two images.

Entry points:

- ``bake_subtree`` runs the bake and writes into any image it is given.
- ``bake_node_cache`` adds the cache bookkeeping. On success it sets the
  node's ``cache_hash`` to the subtree hash, so the next compile uses
  the image in place of the live nodes.
"""
from __future__ import annotations

import contextlib

import bpy
import numpy as np

from ..nodetree.stack_ops import channel_of
from ..props.channel import image_colorspace
from .core import build_ir, mark_dirty


BAKE_TREE_NAME = ".PS Bake Target"
BAKE_MATERIAL_NAME = ".PS Bake Material"
BAKE_ALPHA_IMAGE_NAME = ".PS Bake Alpha"
PS_IMAGE_KEY = "ps_managed"


def create_managed_image(name: str, width: int, height: int, *,
                         alpha: bool = True, float_buffer: bool = False,
                         colorspace: str = 'sRGB') -> bpy.types.Image:
    image = bpy.data.images.new(name, width, height, alpha=alpha, float_buffer=float_buffer)
    image.generated_color = (0.0, 0.0, 0.0, 0.0)
    image.colorspace_settings.name = colorspace
    image[PS_IMAGE_KEY] = True
    return image


def build_bake_tree(tree, node) -> tuple[bpy.types.NodeTree, str]:
    """Compile *node*'s live subtree into the shared bake group.

    Returns the group and the subtree hash.
    """
    ir = build_ir(tree, bake_target=node)
    subtree_hash = ir.ctx.subtree_hash(node)
    bake_tree = bpy.data.node_groups.get(BAKE_TREE_NAME)
    if bake_tree is None or bake_tree.bl_idname != 'ShaderNodeTree':
        bake_tree = bpy.data.node_groups.new(BAKE_TREE_NAME, 'ShaderNodeTree')
    ir.apply(bake_tree, arrange=False)
    return bake_tree, subtree_hash


@contextlib.contextmanager
def _bake_settings(scene):
    render = scene.render
    bake = render.bake
    saved = {
        'engine': render.engine,
        'samples': getattr(scene.cycles, 'samples', None),
        'use_adaptive': getattr(scene.cycles, 'use_adaptive_sampling', None),
        'device': getattr(scene.cycles, 'device', None),
        'target': bake.target,
        'use_clear': bake.use_clear,
        'margin': bake.margin,
        'use_selected_to_active': bake.use_selected_to_active,
    }
    try:
        render.engine = 'CYCLES'
        scene.cycles.samples = 1
        scene.cycles.use_adaptive_sampling = False
        scene.cycles.device = 'CPU'
        bake.target = 'IMAGE_TEXTURES'
        bake.use_clear = True
        bake.use_selected_to_active = False
        yield bake
    finally:
        render.engine = saved['engine']
        if saved['samples'] is not None:
            scene.cycles.samples = saved['samples']
        if saved['use_adaptive'] is not None:
            scene.cycles.use_adaptive_sampling = saved['use_adaptive']
        if saved['device'] is not None:
            scene.cycles.device = saved['device']
        bake.target = saved['target']
        bake.use_clear = saved['use_clear']
        bake.margin = saved['margin']
        bake.use_selected_to_active = saved['use_selected_to_active']


def _remove_last_slot(context, obj) -> None:
    """Remove the slot that ``_temporary_material`` added.

    ``obj.data.materials.pop`` removes the material from the mesh's list,
    but the object keeps its slot count. That leaves an extra empty slot.
    Only the ``material_slot_remove`` operator removes both.
    """
    try:
        obj.active_material_index = len(obj.material_slots) - 1
        with context.temp_override(object=obj, active_object=obj,
                                   selected_objects=[obj]):
            bpy.ops.object.material_slot_remove()
    except RuntimeError:
        # Do not raise here. This runs in a ``finally``, and a new error
        # would hide the one from the bake. An extra empty slot is the
        # smaller problem.
        if len(obj.data.materials):
            obj.data.materials.pop(index=len(obj.data.materials) - 1)


@contextlib.contextmanager
def _temporary_material(context, obj, material):
    """Put *material* in every slot of *obj*, and restore the slots after."""
    appended = len(obj.material_slots) == 0
    if appended:
        obj.data.materials.append(None)
    saved = [slot.material for slot in obj.material_slots]
    try:
        for slot in obj.material_slots:
            slot.material = material
        yield
    finally:
        for slot, mat in zip(obj.material_slots, saved):
            slot.material = mat
        if appended:
            _remove_last_slot(context, obj)


@contextlib.contextmanager
def _borrowed_selection(view_layer, obj):
    """Select only *obj* and make it active, then restore the user's selection.

    ``bpy.ops.object.bake`` bakes the selected objects, so the selection
    has to change for the bake.
    """
    previous_active = view_layer.objects.active
    previous_selected = [o for o in view_layer.objects if o.select_get()]
    try:
        for o in view_layer.objects:
            o.select_set(o == obj)
        view_layer.objects.active = obj
        yield
    finally:
        for o in view_layer.objects:
            o.select_set(o in previous_selected)
        view_layer.objects.active = previous_active


def _merge_alpha(color_image, alpha_image) -> None:
    """Copy the red channel of *alpha_image* into the alpha of *color_image*.

    The alpha pass bakes alpha as an emission colour, so the value sits in
    the red channel.
    """
    count = color_image.size[0] * color_image.size[1] * 4
    color = np.empty(count, dtype=np.float32)
    alpha = np.empty(count, dtype=np.float32)
    color_image.pixels.foreach_get(color)
    alpha_image.pixels.foreach_get(alpha)
    color[3::4] = alpha[0::4]
    color_image.pixels.foreach_set(color)
    color_image.update()


def check_bake_object(obj) -> None:
    """Raise if *obj* cannot be baked onto."""
    if obj is None or obj.type != 'MESH':
        raise RuntimeError("Bake needs an active mesh object")
    if len(obj.data.uv_layers) == 0:
        raise RuntimeError(f"'{obj.name}' has no UV map")


def bake_subtree(context, tree, node, obj, image, *, margin: int = 8,
                 uv_map: str = "") -> str:
    """Bake *node*'s live subtree on *obj* into *image*, and return its hash.

    *image* is written in place and not packed. The caller decides whether
    the result is saved inside the .blend file. The render settings, the
    object's material slots and the selection are all restored before this
    returns.
    """
    check_bake_object(obj)

    bake_tree, subtree_hash = build_bake_tree(tree, node)
    alpha_image = create_managed_image(BAKE_ALPHA_IMAGE_NAME, image.size[0], image.size[1],
                                       alpha=False, colorspace='Non-Color')

    mat = bpy.data.materials.new(BAKE_MATERIAL_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    group = nt.nodes.new('ShaderNodeGroup')
    group.node_tree = bake_tree
    emission = nt.nodes.new('ShaderNodeEmission')
    output = nt.nodes.new('ShaderNodeOutputMaterial')
    nt.links.new(emission.outputs['Emission'], output.inputs['Surface'])
    target = nt.nodes.new('ShaderNodeTexImage')
    if uv_map:
        uv_node = nt.nodes.new('ShaderNodeUVMap')
        uv_node.uv_map = uv_map
        nt.links.new(uv_node.outputs['UV'], target.inputs['Vector'])
    nt.nodes.active = target

    try:
        with _temporary_material(context, obj, mat), _bake_settings(context.scene) as bake, \
                _borrowed_selection(context.view_layer, obj):
            bake.margin = margin
            with context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
                # Pass 1: colour
                nt.links.new(group.outputs['Color'], emission.inputs['Color'])
                target.image = image
                bpy.ops.object.bake(type='EMIT')
                # Pass 2: alpha
                for link in list(emission.inputs['Color'].links):
                    nt.links.remove(link)
                nt.links.new(group.outputs['Alpha'], emission.inputs['Color'])
                target.image = alpha_image
                bpy.ops.object.bake(type='EMIT')
        _merge_alpha(image, alpha_image)
    finally:
        bpy.data.materials.remove(mat)
        bpy.data.images.remove(alpha_image)
    return subtree_hash


def bake_node_cache(context, tree, node, obj, *, width: int = 2048, height: int = 2048,
                    margin: int = 8, uv_map: str = "") -> bpy.types.Image:
    # Check before creating the image, so an object that cannot be baked
    # does not leave an unused image datablock behind.
    check_bake_object(obj)

    channel = channel_of(tree, node)
    # A vector channel's values can be negative, which a byte image would
    # clamp to 0, and encoded normals keep more precision in float. They
    # are data, so they are stored as they are, whatever colour space the
    # channel's layers paint in.
    float_buffer = channel is not None and channel.type == 'VECTOR'
    colorspace = 'Non-Color' if float_buffer else image_colorspace(channel)
    old = image = node.cache_image
    if image is None or image.is_float != float_buffer:
        image = create_managed_image(f"{tree.name} {node.name} Cache", width, height,
                                     float_buffer=float_buffer, colorspace=colorspace)
    elif tuple(image.size) != (width, height):
        image.scale(width, height)

    subtree_hash = bake_subtree(context, tree, node, obj, image,
                                margin=margin, uv_map=uv_map)
    image.pack()

    node.cache_image = image
    if old is not None and old != image and old.get(PS_IMAGE_KEY) and old.users == 0:
        # The cache from before the channel changed type, which nothing
        # else uses.
        bpy.data.images.remove(old)
    node.cache_hash = subtree_hash
    if uv_map:
        node.cache_uv_map = uv_map
    node.cache_stale = False
    mark_dirty(tree)
    return image
