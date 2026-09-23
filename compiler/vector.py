"""The spaces of a vector channel (PS-006).

A vector channel's material input and output are world-space vectors,
which is what every shader socket that takes a vector expects. Its layers
work on other values: the vector in the channel's ``paint_space``, and
for normals stored as colours, the way a normal map stores them
(``vector_kind``). So each value is converted twice. ``from_world`` makes
the value the stack starts from, and ``to_world`` reads the finished
stack back. A group layer converts the same way at its edges, because the
tree it wraps may paint in another space.

- Normals are stored as the colours of a normal map. They are read back
  with a Normal Map node, so a painted layer looks exactly as the same
  image would through a plain Normal Map node. A world-space normal
  becomes the colour that node reads as it, through the library's
  inverse group.
- Vectors are stored as they are. In tangent space they are in the frame
  of the UV map ``tangent_uv_map``: X along the UV map's U, Y along its V,
  Z out of the surface. The library's tangent groups convert into it and
  out of it.

An empty ``tangent_uv_map`` means the mesh's active render UV map, which
is also what a layer with no UV map of its own paints on.
"""
from __future__ import annotations

from .ir import Ref
from .library import (MIX_IN_A_VECTOR, MIX_IN_B_VECTOR, MIX_IN_FACTOR, MIX_OUT_VECTOR, NORMAL_MAP_PROBES,
                      normal_map_inverse_group, tangent_to_world_group, world_to_tangent_group)


# A normal input shorter than this is taken as unlinked. Unlinked, the
# input reads (0, 0, 0): that is its default, it hides its value field so
# nothing can be typed over it, and switching a channel to Normals clears
# a value typed before.
_UNLINKED_LENGTH = 1e-5


def space_settings(channel) -> list:
    """The settings *channel*'s conversions follow, for a hash."""
    if channel.type != 'VECTOR':
        return []
    uv_map = channel.tangent_uv_map if channel.paint_space == 'TANGENT' else ""
    return [channel.vector_kind, channel.paint_space, uv_map]


def input_value(ctx, channel, vector: Ref) -> Ref:
    """Turn *vector*, *channel*'s material input, into the value its stack starts from.

    An unlinked normal input is replaced by the shading normal, so an
    empty stack gives the surface back as it is. The nodes belong to the
    channel.
    """
    if channel.vector_kind == 'NORMAL':
        vector = _or_shading_normal(ctx, channel, vector)
    return from_world(ctx, channel, 'input', channel, vector)


def from_world(ctx, owner, role: str, channel, vector: Ref) -> Ref:
    """Convert the world-space *vector* into *channel*'s layer value, and return the result.

    The nodes are *owner*'s, with roles that start with *role*. Anything
    but a vector channel is returned as it is.
    """
    if channel.type != 'VECTOR':
        return vector
    if channel.vector_kind == 'NORMAL':
        return _encode_normal(ctx, owner, role, channel, vector)
    if channel.paint_space == 'TANGENT':
        return _through_tangent_group(ctx, owner, role, channel, world_to_tangent_group(), vector)
    if channel.paint_space == 'OBJECT':
        return _vector_transform(ctx, owner, role, vector, 'WORLD', 'OBJECT')
    return vector


def to_world(ctx, owner, role: str, channel, value: Ref) -> Ref:
    """Convert *value*, a layer value of *channel*, into a world-space vector, and return the result.

    The nodes are *owner*'s, with roles that start with *role*. Anything
    but a vector channel is returned as it is.
    """
    if channel.type != 'VECTOR':
        return value
    if channel.vector_kind == 'NORMAL':
        # One node reads a colour back in each space, and switching the
        # space only changes its settings.
        decode = ctx.emit_node(owner, f"{role}:decode", 'ShaderNodeNormalMap',
                               properties=_normal_map_settings(channel))
        ctx.link(value, decode, 'Color')
        return (decode, 'Normal')
    if channel.paint_space == 'TANGENT':
        return _through_tangent_group(ctx, owner, role, channel, tangent_to_world_group(), value)
    if channel.paint_space == 'OBJECT':
        return _vector_transform(ctx, owner, role, value, 'OBJECT', 'WORLD')
    return value


def _normal_map_settings(channel) -> dict:
    space = channel.paint_space
    return {'space': space, 'uv_map': channel.tangent_uv_map if space == 'TANGENT' else ""}


def _encode_normal(ctx, owner, role: str, channel, normal: Ref) -> Ref:
    """The colour that ``to_world``'s Normal Map node reads as the world-space *normal*.

    Four more Normal Map nodes, set up as that one, read the colours the
    library's inverse group needs (``normal_map_inverse_group``). So the
    colour is exact wherever the node is, whatever each engine does on
    back faces, mirrored UV maps and unevenly scaled objects.
    """
    inverse = ctx.emit_node(owner, f"{role}:encode", 'ShaderNodeGroup',
                            properties={'node_tree': normal_map_inverse_group()})
    ctx.link(normal, inverse, 'Normal')
    for name, color in NORMAL_MAP_PROBES:
        probe = ctx.emit_node(owner, f"{role}:probe_{name.lower()}", 'ShaderNodeNormalMap',
                              properties=_normal_map_settings(channel),
                              inputs={'Color': {'default_value': color}})
        ctx.link((probe, 'Normal'), inverse, name)
    return (inverse, 'Color')


def _or_shading_normal(ctx, channel, vector: Ref) -> Ref:
    """*vector*, or the shading normal where *vector* has no length."""
    length = ctx.emit_node(channel, 'input:length', 'ShaderNodeVectorMath',
                           properties={'operation': 'LENGTH'})
    ctx.link(vector, length, 0)
    unlinked = ctx.emit_node(channel, 'input:unlinked', 'ShaderNodeMath',
                             properties={'operation': 'LESS_THAN'},
                             inputs={1: {'default_value': _UNLINKED_LENGTH}})
    ctx.link((length, 'Value'), unlinked, 0)
    geometry = ctx.emit_node(channel, 'input:geometry', 'ShaderNodeNewGeometry')
    fallback = ctx.emit_node(channel, 'input:fallback', 'ShaderNodeMix',
                             properties={'data_type': 'VECTOR', 'clamp_factor': True})
    ctx.link((unlinked, 'Value'), fallback, MIX_IN_FACTOR)
    ctx.link(vector, fallback, MIX_IN_A_VECTOR)
    ctx.link((geometry, 'Normal'), fallback, MIX_IN_B_VECTOR)
    return (fallback, MIX_OUT_VECTOR)


def _vector_transform(ctx, owner, role: str, vector: Ref, convert_from: str, convert_to: str) -> Ref:
    transform = ctx.emit_node(owner, f"{role}:transform", 'ShaderNodeVectorTransform', properties={
        'vector_type': 'VECTOR', 'convert_from': convert_from, 'convert_to': convert_to,
    })
    ctx.link(vector, transform, 'Vector')
    return (transform, 'Vector')


def _through_tangent_group(ctx, owner, role: str, channel, group, vector: Ref) -> Ref:
    """Send *vector* through *group*, a tangent group, on *channel*'s tangent frame."""
    uv_map = channel.tangent_uv_map
    tangent = ctx.emit_node(owner, f"{role}:tangent", 'ShaderNodeTangent',
                            properties={'direction_type': 'UV_MAP', 'uv_map': uv_map})
    # Pure +Y, which the Normal Map node reads as the bitangent the way
    # the UV map is mirrored. The group reads only that from it.
    bitangent = ctx.emit_node(owner, f"{role}:bitangent", 'ShaderNodeNormalMap',
                              properties={'space': 'TANGENT', 'uv_map': uv_map},
                              inputs={'Color': {'default_value': (0.5, 1.0, 0.5, 1.0)}})
    frame = ctx.emit_node(owner, f"{role}:frame", 'ShaderNodeGroup', properties={'node_tree': group})
    ctx.link(vector, frame, 'Vector')
    ctx.link((tangent, 'Tangent'), frame, 'Tangent')
    ctx.link((bitangent, 'Normal'), frame, 'Bitangent')
    return (frame, 'Vector')
