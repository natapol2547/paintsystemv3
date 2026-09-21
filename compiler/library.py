"""Shared shader node groups that compiled trees use.

Entry points: ``layer_blend_group`` and ``filter_mix_group``.

- Library groups are stateless building blocks, such as the layer blend
  group. Per-layer values never live in them. They come in through the
  sockets of the group node that uses the library group.
- A group is generated in Python on first use, and rebuilt only when
  ``LIBRARY_VERSION`` changes.
- Groups have no fake user. So a group that no compiled tree uses is not
  saved with the file and does not clutter it. It is generated again
  when a layer needs it. Undo steps keep datablocks that have no users,
  so undo still finds them.
"""
from __future__ import annotations

import functools
from typing import Callable

import bpy

from .ir import IR


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

# ShaderNodeMix socket indices. Names cannot be used, because "A" and "B"
# appear once per data type.
MIX_IN_FACTOR = 0
MIX_IN_A_FLOAT = 2
MIX_IN_B_FLOAT = 3
MIX_IN_A_COLOR = 6
MIX_IN_B_COLOR = 7
MIX_OUT_FLOAT = 0
MIX_OUT_COLOR = 2


def _math(ir, identifier, operation, lhs, rhs, *, clamp=False):
    """Add a Math node on two operands, and return its output ref.

    Each operand is a ``(node, socket)`` pair or a constant.
    """
    operands = list(enumerate((lhs, rhs)))
    inputs = {index: {'default_value': operand}
              for index, operand in operands if not isinstance(operand, tuple)}
    ir.add_node(identifier, 'ShaderNodeMath', inputs=inputs,
                properties={'operation': operation, 'use_clamp': clamp})
    # Link after the node exists, because ``IR.link`` checks both ends.
    for index, operand in operands:
        if isinstance(operand, tuple):
            ir.link(operand, identifier, index)
    return (identifier, 0)


def _mix_interface(ir, amount_name: str, color_default, alpha_default) -> None:
    """Declare the sockets of a mix group and add its Group Input and Output.

    Inputs: Prev Color, Prev Alpha, Color, Alpha, *amount_name*, Mask, Clip.
    Outputs: Color, Alpha. The groups differ only in the amount's name and
    the defaults of the source Color and Alpha.
    """
    ir.add_socket('INPUT', 'NodeSocketColor', 'Prev Color', default_value=(0.0, 0.0, 0.0, 0.0))
    ir.add_socket('INPUT', 'NodeSocketFloat', 'Prev Alpha', default_value=0.0,
                  min_value=0.0, max_value=1.0)
    ir.add_socket('INPUT', 'NodeSocketColor', 'Color', default_value=color_default)
    ir.add_socket('INPUT', 'NodeSocketFloat', 'Alpha', default_value=alpha_default,
                  min_value=0.0, max_value=1.0)
    ir.add_socket('INPUT', 'NodeSocketFloat', amount_name, default_value=1.0,
                  min_value=0.0, max_value=1.0, subtype='FACTOR')
    ir.add_socket('INPUT', 'NodeSocketFloat', 'Mask', default_value=1.0,
                  min_value=0.0, max_value=1.0)
    ir.add_socket('INPUT', 'NodeSocketFloat', 'Clip', default_value=0.0,
                  min_value=0.0, max_value=1.0, subtype='FACTOR')
    ir.add_socket('OUTPUT', 'NodeSocketColor', 'Color')
    ir.add_socket('OUTPUT', 'NodeSocketFloat', 'Alpha')

    ir.add_node('in', 'NodeGroupInput')
    ir.add_node('out', 'NodeGroupOutput')


def _kept(ir):
    """Add a node for the share of the source that is kept, and return it.

    It is 1 when unclipped, and the backdrop's alpha when clipped.
    """
    ir.add_node('kept', 'ShaderNodeMix', properties={
        'data_type': 'FLOAT', 'clamp_factor': True,
    }, inputs={MIX_IN_A_FLOAT: {'default_value': 1.0}})
    ir.link(('in', 'Clip'), 'kept', MIX_IN_FACTOR)
    ir.link(('in', 'Prev Alpha'), 'kept', MIX_IN_B_FLOAT)
    return ('kept', MIX_OUT_FLOAT)


def _composite(ir, source_share, source_color, alpha) -> None:
    """Mix the backdrop colour toward *source_color*, and link the outputs.

    *source_share* is the mix factor. *alpha* goes to the Alpha output.
    """
    ir.add_node('composite', 'ShaderNodeMix', properties={
        'data_type': 'RGBA', 'blend_type': 'MIX',
        'clamp_factor': True, 'clamp_result': False,
    })
    ir.link(source_share, 'composite', MIX_IN_FACTOR)
    ir.link(('in', 'Prev Color'), 'composite', MIX_IN_A_COLOR)
    ir.link(source_color, 'composite', MIX_IN_B_COLOR)

    ir.link(('composite', MIX_OUT_COLOR), 'out', 'Color')
    ir.link(alpha, 'out', 'Alpha')


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

    Straight alpha means the colours are not premultiplied. With backdrop
    ``cb, ab`` (the Prev inputs), source ``cs`` and source coverage
    ``es = Alpha * Opacity * Mask``, each texel splits into three regions:

    - source over backdrop: weight ``es * ab``, colour ``B(cb, cs)``.
    - source alone: weight ``es * (1 - ab)``, colour ``cs``. Dropped when
      clipped.
    - backdrop alone: weight ``ab * (1 - es)``, colour ``cb``.

    The output alpha is the sum of the weights. The output colour is their
    weighted average, or the backdrop colour where the alpha is 0.
    Unclipped, this is W3C source-over with a blend mode. Clipped, it is
    source-atop. MIX has ``B = cs`` and skips the blend nodes.
    """
    ir = IR()
    _mix_interface(ir, 'Opacity', (0.0, 0.0, 0.0, 1.0), 1.0)
    math = functools.partial(_math, ir)

    # es: source coverage
    es = math('source_alpha', 'MULTIPLY',
              math('opacity', 'MULTIPLY', ('in', 'Alpha'), ('in', 'Opacity')),
              ('in', 'Mask'), clamp=True)
    kept = _kept(ir)

    source_weight = math('source_weight', 'MULTIPLY', es, kept)
    backdrop_weight = math('backdrop_weight', 'MULTIPLY', ('in', 'Prev Alpha'),
                           math('backdrop_keep', 'SUBTRACT', 1.0, es))
    alpha = math('alpha', 'ADD', source_weight, backdrop_weight)
    # Math DIVIDE returns 0 for a zero divisor, so a transparent result
    # takes the backdrop colour instead of NaN.
    source_share = math('source_share', 'DIVIDE', source_weight, alpha)

    source_color = ('in', 'Color')
    if blend_type != 'MIX':
        ir.add_node('blend', 'ShaderNodeMix', properties={
            'data_type': 'RGBA', 'blend_type': blend_type,
            'clamp_factor': True, 'clamp_result': False,
        }, inputs={MIX_IN_FACTOR: {'default_value': 1.0}})
        ir.link(('in', 'Prev Color'), 'blend', MIX_IN_A_COLOR)
        ir.link(('in', 'Color'), 'blend', MIX_IN_B_COLOR)
        # Share of the kept source that lies over the backdrop. It is ab
        # unclipped, and 1 clipped.
        blended_share = math('blended_share', 'DIVIDE', ('in', 'Prev Alpha'), kept)
        ir.add_node('source', 'ShaderNodeMix', properties={
            'data_type': 'RGBA', 'blend_type': 'MIX',
            'clamp_factor': True, 'clamp_result': False,
        })
        ir.link(blended_share, 'source', MIX_IN_FACTOR)
        ir.link(('in', 'Color'), 'source', MIX_IN_A_COLOR)
        ir.link(('blend', MIX_OUT_COLOR), 'source', MIX_IN_B_COLOR)
        source_color = ('source', MIX_OUT_COLOR)

    _composite(ir, source_share, source_color, alpha)
    ir.apply(tree)


# -- filter mix -------------------------------------------------------


def filter_mix_group() -> bpy.types.NodeTree:
    """Group that replaces the stack below with a filter layer's own pixels.

    Inputs: Prev Color, Prev Alpha, Color, Alpha, Amount, Mask, Clip.
    Outputs: Color, Alpha.
    """
    return get_library_group("Filter Mix", _build_filter_mix)


def _build_filter_mix(tree: bpy.types.NodeTree) -> None:
    """Dry/wet crossfade between the stack below and a filtered copy of it.

    With backdrop ``cb, ab`` (the Prev inputs), filtered source ``cs, as``,
    strength ``f = clamp(Amount * Mask)`` and ``kept = mix(1, ab, Clip)``::

        a     = ab * (1 - f) + as * f * kept
        share = as * f * kept / a
        Color = mix(cb, cs, share)
        Alpha = a

    Unclipped, ``f == 1`` gives exactly the filtered pixels and ``f == 0``
    exactly the stack below, because the two premultiplied weights add up
    to ``a``. Like ``_build_layer_blend``, this relies on Math DIVIDE
    returning 0 for a zero divisor. So a fully transparent result takes
    the backdrop colour, not NaN.

    It differs from ``_build_layer_blend`` in two deliberate ways:

    - The source alpha is not part of the coverage. A filter replaces the
      stack it was computed from instead of compositing over it, so the
      two alphas must not multiply together.
    - ``kept`` weights only the source, while the backdrop keeps
      ``1 - f``. This keeps a clipped filter's output alpha at or below
      the backdrop's, so the filter adds no coverage outside the layer it
      is clipped to.
    """
    ir = IR()
    _mix_interface(ir, 'Amount', (0.0, 0.0, 0.0, 0.0), 0.0)
    math = functools.partial(_math, ir)

    kept = _kept(ir)
    strength = math('strength', 'MULTIPLY', ('in', 'Amount'), ('in', 'Mask'), clamp=True)
    applied = math('applied', 'MULTIPLY', strength, kept)
    source_weight = math('source_weight', 'MULTIPLY', ('in', 'Alpha'), applied)
    backdrop_weight = math('backdrop_weight', 'MULTIPLY', ('in', 'Prev Alpha'),
                           math('backdrop_keep', 'SUBTRACT', 1.0, strength))
    alpha = math('alpha', 'ADD', source_weight, backdrop_weight)
    source_share = math('source_share', 'DIVIDE', source_weight, alpha)

    _composite(ir, source_share, ('in', 'Color'), alpha)
    ir.apply(tree)
