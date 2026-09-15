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


LIBRARY_VERSION = 2
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
MIX_IN_A_FLOAT = 2
MIX_IN_B_FLOAT = 3
MIX_IN_A_COLOR = 6
MIX_IN_B_COLOR = 7
MIX_OUT_FLOAT = 0
MIX_OUT_COLOR = 2


def layer_blend_group(blend_type: str) -> bpy.types.NodeTree:
    """Group that composites one layer over the previous stack result.

    Inputs: Prev Color, Prev Alpha, Color, Alpha, Opacity, Mask, Clip.
    Outputs: Color, Alpha.
    """
    return get_library_group(
        f"Layer Blend [{blend_type}]",
        lambda tree: _build_layer_blend(tree, blend_type),
    )


def _build_layer_blend(tree: bpy.types.NodeTree, blend_type: str) -> None:
    """Porter-Duff compositing of a straight-alpha layer with a blend mode.

    With backdrop ``cb, ab`` (Prev), source ``cs`` and source coverage
    ``es = Alpha * Opacity * Mask``, a texel splits into three regions:

    - source over backdrop, weight ``es * ab``, colour ``B(cb, cs)``
    - source alone, weight ``es * (1 - ab)``, colour ``cs``; dropped when
      clipped
    - backdrop alone, weight ``ab * (1 - es)``, colour ``cb``

    The output alpha is the sum of the weights and the output colour their
    weighted average (the backdrop colour where the alpha is 0). Unclipped
    this is W3C source-over with a blend mode; clipped it is source-atop.
    MIX has ``B = cs`` and skips the blend nodes.
    """
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
    b.add_socket('INPUT', 'NodeSocketFloat', 'Clip', default_value=0.0,
                 min_value=0.0, max_value=1.0, subtype='FACTOR')
    b.add_socket('OUTPUT', 'NodeSocketColor', 'Color')
    b.add_socket('OUTPUT', 'NodeSocketFloat', 'Alpha')

    b.add_node('in', 'NodeGroupInput')
    b.add_node('out', 'NodeGroupOutput')

    def link(source, to_identifier, to_socket):
        b.link_nodes(source[0], to_identifier, source[1], to_socket)

    def math(identifier, operation, lhs, rhs, *, clamp=False):
        """Math node on two operands, each a (node, socket) pair or a constant."""
        inputs = {}
        for index, operand in enumerate((lhs, rhs)):
            if isinstance(operand, tuple):
                link(operand, identifier, index)
            else:
                inputs[index] = {'default_value': operand}
        b.add_node(identifier, 'ShaderNodeMath', inputs=inputs,
                   properties={'operation': operation, 'use_clamp': clamp})
        return (identifier, 0)

    # es: source coverage
    es = math('source_alpha', 'MULTIPLY',
              math('opacity', 'MULTIPLY', ('in', 'Alpha'), ('in', 'Opacity')),
              ('in', 'Mask'), clamp=True)

    # kept: share of the source that survives, 1 unclipped, ab clipped
    b.add_node('kept', 'ShaderNodeMix', properties={
        'data_type': 'FLOAT', 'clamp_factor': True,
    }, inputs={MIX_IN_A_FLOAT: {'default_value': 1.0}})
    b.link_nodes('in', 'kept', 'Clip', MIX_IN_FACTOR)
    b.link_nodes('in', 'kept', 'Prev Alpha', MIX_IN_B_FLOAT)
    kept = ('kept', MIX_OUT_FLOAT)

    source_weight = math('source_weight', 'MULTIPLY', es, kept)
    backdrop_weight = math('backdrop_weight', 'MULTIPLY', ('in', 'Prev Alpha'),
                           math('backdrop_keep', 'SUBTRACT', 1.0, es))
    alpha = math('alpha', 'ADD', source_weight, backdrop_weight)
    # Math DIVIDE returns 0 for a zero divisor, so a transparent result
    # takes the backdrop colour instead of NaN.
    source_share = math('source_share', 'DIVIDE', source_weight, alpha)

    source_color = ('in', 'Color')
    if blend_type != 'MIX':
        b.add_node('blend', 'ShaderNodeMix', properties={
            'data_type': 'RGBA', 'blend_type': blend_type,
            'clamp_factor': True, 'clamp_result': False,
        }, inputs={MIX_IN_FACTOR: {'default_value': 1.0}})
        b.link_nodes('in', 'blend', 'Prev Color', MIX_IN_A_COLOR)
        b.link_nodes('in', 'blend', 'Color', MIX_IN_B_COLOR)
        # Share of the surviving source that lies over the backdrop:
        # ab unclipped, 1 clipped.
        blended_share = math('blended_share', 'DIVIDE', ('in', 'Prev Alpha'), kept)
        b.add_node('source', 'ShaderNodeMix', properties={
            'data_type': 'RGBA', 'blend_type': 'MIX',
            'clamp_factor': True, 'clamp_result': False,
        })
        link(blended_share, 'source', MIX_IN_FACTOR)
        b.link_nodes('in', 'source', 'Color', MIX_IN_A_COLOR)
        b.link_nodes('blend', 'source', MIX_OUT_COLOR, MIX_IN_B_COLOR)
        source_color = ('source', MIX_OUT_COLOR)

    b.add_node('composite', 'ShaderNodeMix', properties={
        'data_type': 'RGBA', 'blend_type': 'MIX',
        'clamp_factor': True, 'clamp_result': False,
    })
    link(source_share, 'composite', MIX_IN_FACTOR)
    b.link_nodes('in', 'composite', 'Prev Color', MIX_IN_A_COLOR)
    link(source_color, 'composite', MIX_IN_B_COLOR)

    b.link_nodes('composite', 'out', MIX_OUT_COLOR, 'Color')
    link(alpha, 'out', 'Alpha')

    b.build()
