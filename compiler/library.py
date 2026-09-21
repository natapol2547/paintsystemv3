"""Static, shared shader node groups used by compiled trees.

Library groups are stateless building blocks (for example the layer blend
group). They are generated in Python on first use and rebuilt only when
``LIBRARY_VERSION`` changes. Per-layer state never lives here; it is passed
in through sockets on the instancing group node.

They have no fake user, so a group no compiled tree uses is not saved with
the file and does not clutter it; it is generated again when a layer needs
it. Undo steps keep datablocks without users, so undo still finds them.
"""
from __future__ import annotations

import functools
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
    if tree.get(_VERSION_KEY) != LIBRARY_VERSION:
        build(tree)
        tree[_VERSION_KEY] = LIBRARY_VERSION
    return tree


# -- parts shared by the mix groups -----------------------------------

# ShaderNodeMix socket indices (name lookup is ambiguous: "A"/"B" repeat per data type).
MIX_IN_FACTOR = 0
MIX_IN_A_FLOAT = 2
MIX_IN_B_FLOAT = 3
MIX_IN_A_COLOR = 6
MIX_IN_B_COLOR = 7
MIX_OUT_FLOAT = 0
MIX_OUT_COLOR = 2


def _link(b, source, to_identifier, to_socket) -> None:
    """Link a ``(node, socket)`` pair into another node's socket."""
    b.link_nodes(source[0], to_identifier, source[1], to_socket)


def _math(b, identifier, operation, lhs, rhs, *, clamp=False):
    """Math node on two operands, each a ``(node, socket)`` pair or a constant."""
    inputs = {}
    for index, operand in enumerate((lhs, rhs)):
        if isinstance(operand, tuple):
            _link(b, operand, identifier, index)
        else:
            inputs[index] = {'default_value': operand}
    b.add_node(identifier, 'ShaderNodeMath', inputs=inputs,
               properties={'operation': operation, 'use_clamp': clamp})
    return (identifier, 0)


def _mix_interface(b, amount_name: str, color_default, alpha_default) -> None:
    """Declare the sockets of a mix group and add its Group Input and Output.

    Inputs: Prev Color, Prev Alpha, Color, Alpha, *amount_name*, Mask, Clip.
    Outputs: Color, Alpha. The groups differ only in the amount's name and
    the defaults of the source Color and Alpha.
    """
    b.add_socket('INPUT', 'NodeSocketColor', 'Prev Color', default_value=(0.0, 0.0, 0.0, 0.0))
    b.add_socket('INPUT', 'NodeSocketFloat', 'Prev Alpha', default_value=0.0,
                 min_value=0.0, max_value=1.0)
    b.add_socket('INPUT', 'NodeSocketColor', 'Color', default_value=color_default)
    b.add_socket('INPUT', 'NodeSocketFloat', 'Alpha', default_value=alpha_default,
                 min_value=0.0, max_value=1.0)
    b.add_socket('INPUT', 'NodeSocketFloat', amount_name, default_value=1.0,
                 min_value=0.0, max_value=1.0, subtype='FACTOR')
    b.add_socket('INPUT', 'NodeSocketFloat', 'Mask', default_value=1.0,
                 min_value=0.0, max_value=1.0)
    b.add_socket('INPUT', 'NodeSocketFloat', 'Clip', default_value=0.0,
                 min_value=0.0, max_value=1.0, subtype='FACTOR')
    b.add_socket('OUTPUT', 'NodeSocketColor', 'Color')
    b.add_socket('OUTPUT', 'NodeSocketFloat', 'Alpha')

    b.add_node('in', 'NodeGroupInput')
    b.add_node('out', 'NodeGroupOutput')


def _kept(b):
    """Share of the source that survives: 1 unclipped, the backdrop's alpha clipped."""
    b.add_node('kept', 'ShaderNodeMix', properties={
        'data_type': 'FLOAT', 'clamp_factor': True,
    }, inputs={MIX_IN_A_FLOAT: {'default_value': 1.0}})
    b.link_nodes('in', 'kept', 'Clip', MIX_IN_FACTOR)
    b.link_nodes('in', 'kept', 'Prev Alpha', MIX_IN_B_FLOAT)
    return ('kept', MIX_OUT_FLOAT)


def _composite(b, source_share, source_color, alpha) -> None:
    """Output the backdrop colour mixed toward *source_color* by *source_share*, with *alpha*."""
    b.add_node('composite', 'ShaderNodeMix', properties={
        'data_type': 'RGBA', 'blend_type': 'MIX',
        'clamp_factor': True, 'clamp_result': False,
    })
    _link(b, source_share, 'composite', MIX_IN_FACTOR)
    b.link_nodes('in', 'composite', 'Prev Color', MIX_IN_A_COLOR)
    _link(b, source_color, 'composite', MIX_IN_B_COLOR)

    b.link_nodes('composite', 'out', MIX_OUT_COLOR, 'Color')
    _link(b, alpha, 'out', 'Alpha')


# -- layer blend ------------------------------------------------------


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
    _mix_interface(b, 'Opacity', (0.0, 0.0, 0.0, 1.0), 1.0)

    link = functools.partial(_link, b)
    math = functools.partial(_math, b)

    # es: source coverage
    es = math('source_alpha', 'MULTIPLY',
              math('opacity', 'MULTIPLY', ('in', 'Alpha'), ('in', 'Opacity')),
              ('in', 'Mask'), clamp=True)
    kept = _kept(b)

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

    _composite(b, source_share, source_color, alpha)
    b.build()


# -- filter mix -------------------------------------------------------


def filter_mix_group() -> bpy.types.NodeTree:
    """Group that replaces the stack below with a filter layer's own pixels.

    Inputs: Prev Color, Prev Alpha, Color, Alpha, Amount, Mask, Clip.
    Outputs: Color, Alpha.
    """
    return get_library_group("Filter Mix", _build_filter_mix)


def _build_filter_mix(tree: bpy.types.NodeTree) -> None:
    """Dry/wet crossfade between the stack below and a filtered copy of it.

    With backdrop ``cb, ab`` (Prev), filtered source ``cs, as``, strength
    ``f = clamp(Amount * Mask)`` and ``kept = mix(1, ab, Clip)``::

        a     = ab * (1 - f) + as * f * kept
        share = as * f * kept / a
        Color = mix(cb, cs, share)
        Alpha = a

    At ``f == 1`` unclipped the result is exactly the filtered pixels and
    at ``f == 0`` exactly the stack below, because the two premultiplied
    weights sum to ``a``. Like ``_build_layer_blend`` this relies on Math
    DIVIDE returning 0 for a zero divisor, so a fully transparent result
    takes the backdrop colour rather than NaN.

    The differences from ``_build_layer_blend`` are both deliberate. The
    source alpha is not part of the coverage term: a filter replaces the
    stack it was computed from rather than compositing over it, so the two
    alphas must not compound. And ``kept`` weights only the source, while
    the backdrop keeps ``1 - f``, which is what holds a clipped filter's
    output alpha down to the backdrop's instead of letting the filter add
    coverage outside the layer it is clipped to.
    """
    b = NodeTreeBuilder(tree)
    _mix_interface(b, 'Amount', (0.0, 0.0, 0.0, 0.0), 0.0)
    math = functools.partial(_math, b)

    kept = _kept(b)
    strength = math('strength', 'MULTIPLY', ('in', 'Amount'), ('in', 'Mask'), clamp=True)
    applied = math('applied', 'MULTIPLY', strength, kept)
    source_weight = math('source_weight', 'MULTIPLY', ('in', 'Alpha'), applied)
    backdrop_weight = math('backdrop_weight', 'MULTIPLY', ('in', 'Prev Alpha'),
                           math('backdrop_keep', 'SUBTRACT', 1.0, strength))
    alpha = math('alpha', 'ADD', source_weight, backdrop_weight)
    source_share = math('source_share', 'DIVIDE', source_weight, alpha)

    _composite(b, source_share, ('in', 'Color'), alpha)
    b.build()
