"""Bake a node's output into its cache image (the hybrid part of the model).

The compiler builds a temporary shader group whose outputs are the target
node's live Color/Alpha (ignoring its own cache). A throwaway material routes
that through an Emission shader and Cycles bakes it twice: once for color,
once for alpha, merged with numpy. On success the node's ``cache_hash`` is
set to the subtree fingerprint so the next compile substitutes the image.
"""
from __future__ import annotations

import contextlib

import bpy
import numpy as np

from .core import build_ir, mark_dirty, BAKE_TREE_NAME


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
    """Compile *node*'s live subtree into the shared bake group. Returns (group, subtree_hash)."""
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


@contextlib.contextmanager
def _temporary_material(obj, material):
    if len(obj.material_slots) == 0:
        obj.data.materials.append(None)
    saved = [slot.material for slot in obj.material_slots]
    try:
        for slot in obj.material_slots:
            slot.material = material
        yield
    finally:
        for slot, mat in zip(obj.material_slots, saved):
            slot.material = mat


def _merge_alpha(color_image, alpha_image) -> None:
    count = color_image.size[0] * color_image.size[1] * 4
    color = np.empty(count, dtype=np.float32)
    alpha = np.empty(count, dtype=np.float32)
    color_image.pixels.foreach_get(color)
    alpha_image.pixels.foreach_get(alpha)
    color[3::4] = alpha[0::4]
    color_image.pixels.foreach_set(color)
    color_image.update()


def bake_node_cache(context, tree, node, obj, *, width: int = 2048, height: int = 2048,
                    margin: int = 8, uv_map: str = "") -> bpy.types.Image:
    if obj is None or obj.type != 'MESH':
        raise RuntimeError("Bake needs an active mesh object")
    if len(obj.data.uv_layers) == 0:
        raise RuntimeError(f"'{obj.name}' has no UV map")

    bake_tree, subtree_hash = build_bake_tree(tree, node)

    image = node.cache_image
    if image is None:
        image = create_managed_image(f"{tree.name} {node.name} Cache", width, height)
    elif tuple(image.size) != (width, height):
        image.scale(width, height)
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

    scene = context.scene
    view_layer = context.view_layer
    try:
        with _temporary_material(obj, mat), _bake_settings(scene) as bake:
            bake.margin = margin
            for o in view_layer.objects:
                o.select_set(o == obj)
            view_layer.objects.active = obj
            with context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
                # Pass 1: color
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
        image.pack()
    finally:
        bpy.data.materials.remove(mat)
        bpy.data.images.remove(alpha_image)

    node.cache_image = image
    node.cache_hash = subtree_hash
    if uv_map:
        node.cache_uv_map = uv_map
    node.cache_stale = False
    mark_dirty(tree)
    return image
