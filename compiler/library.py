"""Static, shared shader node groups used by compiled trees.

Library groups are stateless building blocks (for example the layer blend
group). They are generated in Python on first use, stored with a fake user,
and rebuilt only when ``LIBRARY_VERSION`` changes. Per-layer state never lives
here; it is passed in through sockets on the instancing group node.
"""
from __future__ import annotations

from typing import Callable

import bpy

from ..nodes.builder import NodeTreeBuilder


LIBRARY_VERSION = 1
LIBRARY_PREFIX = ".PS Lib "
_VERSION_KEY = "ps_lib_version"


def get_library_group(key: str, build: Callable[[bpy.types.NodeTree], None]) -> bpy.types.NodeTree:
    name = f"{LIBRARY_PREFIX}{key}"
    tree = bpy.data.node_groups.get(name)
    if tree is not None and tree.bl_idname != 'ShaderNodeTree':
        tree = None
    if tree is None:
        tree = bpy.data.node_groups.new(name, 'ShaderNodeTree')
        tree.use_fake_user = True
    if tree.get(_VERSION_KEY) != LIBRARY_VERSION:
        build(tree)
        tree[_VERSION_KEY] = LIBRARY_VERSION
    return tree


def is_library_group(tree: bpy.types.NodeTree) -> bool:
    return tree.name.startswith(LIBRARY_PREFIX)


# -- layer blend ------------------------------------------------------

# ShaderNodeMix socket indices (name lookup is ambiguous: "A"/"B" repeat per data type).
MIX_IN_FACTOR = 0
MIX_IN_A_COLOR = 6
MIX_IN_B_COLOR = 7
MIX_OUT_COLOR = 2


def layer_blend_group(blend_type: str) -> bpy.types.NodeTree:
    """Group that composites one layer over the previous stack result.

    Inputs: Prev Color, Prev Alpha, Color, Alpha, Opacity, Mask.
    Outputs: Color, Alpha.
    """
    return get_library_group(
        f"Layer Blend [{blend_type}]",
        lambda tree: _build_layer_blend(tree, blend_type),
    )


def _build_layer_blend(tree: bpy.types.NodeTree, blend_type: str) -> None:
    b = NodeTreeBuilder(tree)
    b.add_socket('INPUT', 'NodeSocketColor', 'Prev Color', default_value=(0.0, 0.0, 0.0, 0.0))
    b.add_socket('INPUT', 'NodeSocketFloat', 'Prev Alpha', default_value=0.0,
                 min_value=0.0, max_value=1.0)
    b.add_socket('INPUT', 'NodeSocketColor', 'Color', default_value=(0.0, 0.0, 0.0, 1.0))
    b.add_socket('INPUT', 'NodeSocketFloat', 'Alpha', default_value=1.0,
                 min_value=0.0, max_value=1.0)
    b.add_socket('INPUT', 'NodeSocketFloat', 'Opacity', default_value=1.0,
                 min_value=0.0, max_value=1.0, subtype='FACTOR')
    b.add_socket('INPUT', 'NodeSocketFloat', 'Mask', default_value=1.0,
                 min_value=0.0, max_value=1.0)
    b.add_socket('OUTPUT', 'NodeSocketColor', 'Color')
    b.add_socket('OUTPUT', 'NodeSocketFloat', 'Alpha')

    b.add_node('in', 'NodeGroupInput')
    b.add_node('out', 'NodeGroupOutput')

    # effective alpha = Alpha * Opacity * Mask
    b.add_node('a_opacity', 'ShaderNodeMath', properties={'operation': 'MULTIPLY'})
    b.add_node('a_mask', 'ShaderNodeMath', properties={'operation': 'MULTIPLY'})
    b.link_nodes('in', 'a_opacity', 'Alpha', 0)
    b.link_nodes('in', 'a_opacity', 'Opacity', 1)
    b.link_nodes('a_opacity', 'a_mask', 0, 0)
    b.link_nodes('in', 'a_mask', 'Mask', 1)

    # color = mix(prev, color, effective alpha)
    b.add_node('mix', 'ShaderNodeMix', properties={
        'data_type': 'RGBA', 'blend_type': blend_type,
        'clamp_factor': True, 'clamp_result': False,
    })
    b.link_nodes('a_mask', 'mix', 0, MIX_IN_FACTOR)
    b.link_nodes('in', 'mix', 'Prev Color', MIX_IN_A_COLOR)
    b.link_nodes('in', 'mix', 'Color', MIX_IN_B_COLOR)
    b.link_nodes('mix', 'out', MIX_OUT_COLOR, 'Color')

    # alpha = prev + eff * (1 - prev)
    b.add_node('inv', 'ShaderNodeMath', properties={'operation': 'SUBTRACT'},
               inputs={0: {'default_value': 1.0}})
    b.add_node('a_mul', 'ShaderNodeMath', properties={'operation': 'MULTIPLY'})
    b.add_node('a_add', 'ShaderNodeMath', properties={'operation': 'ADD'})
    b.link_nodes('in', 'inv', 'Prev Alpha', 1)
    b.link_nodes('a_mask', 'a_mul', 0, 0)
    b.link_nodes('inv', 'a_mul', 0, 1)
    b.link_nodes('in', 'a_add', 'Prev Alpha', 0)
    b.link_nodes('a_mul', 'a_add', 0, 1)
    b.link_nodes('a_add', 'out', 0, 'Alpha')

    b.build()
