"""Shared shader node groups that compiled trees use.

Entry points: ``layer_blend_group``, ``filter_mix_group``,
``world_to_tangent_group``, ``tangent_to_world_group`` and
``normal_map_inverse_group``.

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
MIX_IN_A_VECTOR = 4
MIX_IN_B_VECTOR = 5
MIX_IN_A_COLOR = 6
MIX_IN_B_COLOR = 7
MIX_OUT_FLOAT = 0
MIX_OUT_VECTOR = 1
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


# -- tangent frame ----------------------------------------------------


def world_to_tangent_group() -> bpy.types.NodeTree:
    """Group that gives a world-space vector in the tangent frame of a UV map.

    Inputs: Vector, Tangent, Bitangent. Output: Vector.
    """
    return get_library_group("World To Tangent", _build_world_to_tangent)


def tangent_to_world_group() -> bpy.types.NodeTree:
    """Group that gives a vector in the tangent frame of a UV map in world space.

    Inputs: Vector, Tangent, Bitangent. Output: Vector.
    """
    return get_library_group("Tangent To World", _build_tangent_to_world)


def _vector_math(ir, identifier, operation, *vectors, scale=None):
    """Add a Vector Math node on *vectors*, and return its output ref.

    Each vector is a ``(node, socket)`` pair, and so is *scale*, the
    factor of SCALE. The ref is the Value output of a dot product, and
    the Vector output otherwise.
    """
    ir.add_node(identifier, 'ShaderNodeVectorMath', properties={'operation': operation})
    for index, vector in enumerate(vectors):
        ir.link(vector, identifier, index)
    if scale is not None:
        ir.link(scale, identifier, 'Scale')
    return (identifier, 'Value' if operation == 'DOT_PRODUCT' else 'Vector')


def _tangent_frame(ir):
    """Declare the sockets of a tangent group, and return its frame as (T, B, N) refs.

    The Tangent input is the Tangent node's output, and Bitangent is the
    Normal Map node's reading of pure +Y. A group cannot pick a UV map, so
    the compiled tree adds those two nodes, both on the same UV map.

    T is Tangent made perpendicular to the shading normal, as EEVEE's
    Tangent node does not do that. N is the surface's own normal: the
    shading normal, turned back round on a back face, where it faces the
    viewer. B is N x T, turned round where Bitangent points the other way.
    That is where the UV map is mirrored, and the Normal Map node then
    reads green the other way round too. So a vector means the same seen
    from either side, and the frame is orthonormal, so the two tangent
    groups undo each other.
    """
    ir.add_socket('INPUT', 'NodeSocketVector', 'Vector')
    ir.add_socket('INPUT', 'NodeSocketVector', 'Tangent')
    ir.add_socket('INPUT', 'NodeSocketVector', 'Bitangent')
    ir.add_socket('OUTPUT', 'NodeSocketVector', 'Vector')
    ir.add_node('in', 'NodeGroupInput')
    ir.add_node('out', 'NodeGroupOutput')
    ir.add_node('geometry', 'ShaderNodeNewGeometry')
    vmath = functools.partial(_vector_math, ir)

    shading = ('geometry', 'Normal')
    along = vmath('tangent_along', 'DOT_PRODUCT', ('in', 'Tangent'), shading)
    normal_part = vmath('tangent_normal_part', 'SCALE', shading, scale=along)
    tangent = vmath('tangent', 'NORMALIZE', vmath('tangent_flat', 'SUBTRACT', ('in', 'Tangent'), normal_part))
    # 1 on a front face and -1 on a back face.
    facing = _math(ir, 'facing', 'ADD', _math(ir, 'back', 'MULTIPLY', ('geometry', 'Backfacing'), -2.0), 1.0)
    normal = vmath('normal', 'SCALE', shading, scale=facing)
    cross = vmath('cross', 'CROSS_PRODUCT', shading, tangent)
    # 1 or -1. On a back face the Normal Map node turns Bitangent round,
    # as the cross product with the shading normal turns. Where the mesh
    # has no UV map of the name the nodes were given, Cycles' Tangent node
    # gives 0 and the Normal Map node the plain normal, so the frame has
    # neither T nor B.
    handedness = _math(ir, 'handedness', 'SIGN',
                       vmath('bitangent_along', 'DOT_PRODUCT', ('in', 'Bitangent'), cross), 0.0)
    bitangent = vmath('bitangent', 'SCALE', cross, scale=_math(ir, 'bitangent_sign', 'MULTIPLY', handedness, facing))
    return tangent, bitangent, normal


def _build_world_to_tangent(tree: bpy.types.NodeTree) -> None:
    """(V . T, V . B, V . N), with the frame of ``_tangent_frame``."""
    ir = IR()
    frame = _tangent_frame(ir)
    ir.add_node('combine', 'ShaderNodeCombineXYZ')
    for axis, (name, direction) in enumerate(zip("xyz", frame)):
        ir.link(_vector_math(ir, name, 'DOT_PRODUCT', ('in', 'Vector'), direction), 'combine', axis)
    ir.link(('combine', 'Vector'), 'out', 'Vector')
    ir.apply(tree)


def _build_tangent_to_world(tree: bpy.types.NodeTree) -> None:
    """V.x * T + V.y * B + V.z * N, with the frame of ``_tangent_frame``."""
    ir = IR()
    frame = _tangent_frame(ir)
    ir.add_node('separate', 'ShaderNodeSeparateXYZ')
    ir.link(('in', 'Vector'), 'separate', 'Vector')
    x, y, z = (_vector_math(ir, name, 'SCALE', direction, scale=('separate', axis))
               for axis, (name, direction) in enumerate(zip("xyz", frame)))
    total = _vector_math(ir, 'sum', 'ADD', _vector_math(ir, 'sum_xy', 'ADD', x, y), z)
    ir.link(total, 'out', 'Vector')
    ir.apply(tree)


# -- normal maps ------------------------------------------------------

# The colours whose readings ``normal_map_inverse_group`` takes, by input.
NORMAL_MAP_PROBES = (
    ('X', (1.0, 0.5, 0.5, 1.0)),
    ('Y', (0.5, 1.0, 0.5, 1.0)),
    ('Z', (0.5, 0.5, 1.0, 1.0)),
    ('XYZ', (1.0, 1.0, 1.0, 1.0)),
)

# Below this, the readings span no volume, and the inverse gives flat.
_SINGULAR = 1e-4


def normal_map_inverse_group() -> bpy.types.NodeTree:
    """Group that gives the colour a Normal Map node reads as a given normal.

    Inputs: Normal, then one per ``NORMAL_MAP_PROBES`` colour, which is
    what that same node reads it as. Output: Color, as a vector.
    """
    return get_library_group("Normal Map Inverse", _build_normal_map_inverse)


def _build_normal_map_inverse(tree: bpy.types.NodeTree) -> None:
    """The colour from the Normal Map node's readings of +X, +Y, +Z and (1, 1, 1).

    In every space and in both engines, the node reads a colour c as
    normalize(F (2c - 1)), where F is a matrix of the point being shaded:
    the UV map's tangent frame, the object's matrix, or none, and turned
    round on a back face where the engine does that. So the readings X, Y
    and Z are F's columns, normalised, and XYZ is their sum, normalised.
    Part i of the colour is N . R_i over XYZ . R_i, where R_i is the cross
    product of the other two readings. That is part i of F^-1 N, up to a
    scale that the normalising cancels, along with the lengths the
    readings lost and the sign of F's determinant.

    The node then reads the colour back as N exactly, even where F is not
    a rotation, as on an unevenly scaled object. Where F has no inverse,
    as with a UV map the mesh lacks, the colour is flat (0.5, 0.5, 1).
    """
    ir = IR()
    ir.add_socket('INPUT', 'NodeSocketVector', 'Normal')
    for name, _color in NORMAL_MAP_PROBES:
        ir.add_socket('INPUT', 'NodeSocketVector', name)
    ir.add_socket('OUTPUT', 'NodeSocketVector', 'Color')
    ir.add_node('in', 'NodeGroupInput')
    ir.add_node('out', 'NodeGroupOutput')
    vmath = functools.partial(_vector_math, ir)

    readings = [('in', name) for name in "XYZ"]
    ir.add_node('parts', 'ShaderNodeCombineXYZ')
    rows = []
    for axis, name in enumerate("xyz"):
        row = vmath(f"row_{name}", 'CROSS_PRODUCT', readings[(axis + 1) % 3], readings[(axis + 2) % 3])
        rows.append(row)
        part = _math(ir, f"part_{name}", 'DIVIDE',
                     vmath(f"normal_{name}", 'DOT_PRODUCT', ('in', 'Normal'), row),
                     vmath(f"sum_{name}", 'DOT_PRODUCT', ('in', 'XYZ'), row))
        ir.link(part, 'parts', axis)
    direction = vmath('direction', 'NORMALIZE', ('parts', 'Vector'))

    # The determinant of the readings, Z . (X x Y).
    volume = _math(ir, 'volume', 'ABSOLUTE', vmath('determinant', 'DOT_PRODUCT', readings[2], rows[2]), 0.0)
    ir.add_node('invertible', 'ShaderNodeMix', properties={'data_type': 'VECTOR', 'clamp_factor': True},
                inputs={MIX_IN_A_VECTOR: {'default_value': (0.0, 0.0, 1.0)}})
    ir.link(_math(ir, 'has_inverse', 'GREATER_THAN', volume, _SINGULAR), 'invertible', MIX_IN_FACTOR)
    ir.link(direction, 'invertible', MIX_IN_B_VECTOR)

    # From -1..1 to the 0..1 of a colour.
    ir.add_node('encode', 'ShaderNodeVectorMath', properties={'operation': 'MULTIPLY_ADD'},
                inputs={1: {'default_value': (0.5, 0.5, 0.5)}, 2: {'default_value': (0.5, 0.5, 0.5)}})
    ir.link(('invertible', MIX_OUT_VECTOR), 'encode', 0)
    ir.link(('encode', 'Vector'), 'out', 'Color')
    ir.apply(tree)
