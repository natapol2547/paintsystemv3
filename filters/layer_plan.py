# SPDX-License-Identifier: GPL-3.0-or-later
"""Which path a filter layer's input comes down (PS-057).

There are two ways to get the picture under a filter layer. The GPU
composite (`filters.composite`) draws it in a handful of passes and is
what the auto refresh is allowed to run. The Cycles bake
(`compiler.bake.bake_subtree`) renders it, which is exact for anything --
group layers, the layers that evaluate surface data, a blend mode with no
parity test -- and takes seconds.

`resolve_input` picks between them and allocates nothing while doing it,
so a layer that cannot be built says why before any video memory is
spent. Two outcomes are distinct on purpose:

- an `InputPlan` on the ``BAKE`` path carries the reason in `reason`.
  Only the bake could build it, and the bake path is not built yet, so
  `filters.layer_build.steps` refuses it with that reason for now;
- `filters.core.Refused` means neither path can, and is shown to the
  user as written.

The UV map is the subtle one. The composite samples every source image by
normalised coordinate, which only lands in the right place while the
whole stack below shares one UV map with the derived image. Layers that
all leave `uv_map` empty share the active render map whatever it is, so
that case needs no object at all and a filter layer works with nothing
selected. As soon as one layer names a map, telling whether the others
resolve to the same one needs a mesh to ask.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import bpy

from ..gpu_passes.core import gpu_known
from ..gpu_passes.texel_map import resolve_uv_map
from ..nodetree.stack_ops import consumer_slot, feeding_link, is_layer, link_index
from . import composite
from .core import Refused

log = logging.getLogger(__name__)

COMPOSITE, BAKE = 'COMPOSITE', 'BAKE'


@dataclass(frozen=True)
class InputPlan:
    """How to get the picture below a filter layer, and what it costs.

    *source* is the node feeding the layer's ``Color`` input, which is
    what `compiler.core.build_ir` takes as its `bake_target` -- for a
    clipped filter layer that is its base's own content, which is what
    such a layer filters. *chain* is the composite plan on the composite
    path and None on the bake path, where *reason* says why.

    *uv_map* is the map the result is laid out in, resolved: "" only
    when nothing below names one and no object was needed to decide.
    """
    path: str
    source: bpy.types.Node
    chain: composite.ChainPlan | None
    uv_map: str
    reason: str

    @property
    def is_composite(self) -> bool:
        return self.path == COMPOSITE


def channel_of(tree, node):
    """The channel *node*'s stack feeds, or None when it reaches no output.

    Walks up the way the compiler does, so a layer inside a folder
    reports the folder's channel.
    """
    output = tree.get_output_node()
    if output is None:
        return None
    visited = {node.name}
    with link_index(tree):
        current = node
        while True:
            slot = consumer_slot(current)
            if slot is None:
                return None
            socket = slot[0]
            if socket.node == output:
                return next((channel for channel in tree.channels
                             if channel.name == socket.name), None)
            consumer = socket.node
            if not is_layer(consumer) or consumer.name in visited:
                return None
            visited.add(consumer.name)
            current = consumer


def resolve_input(context, tree, node) -> InputPlan:
    """Decide how the stack below *node* is turned into pixels.

    Raises `filters.core.Refused` when neither path can run, naming the
    layer or image at fault.
    """
    channel = channel_of(tree, node)
    if channel is not None and channel.type != 'COLOR':
        raise Refused("Filter layers only work on colour channels")

    link = feeding_link(node.inputs['Color'])
    if link is None:
        raise Refused(f"There is nothing below '{node.name}' to filter")
    source = link.from_node

    obj = getattr(context, 'object', None)
    if obj is not None and obj.type != 'MESH':
        obj = None

    try:
        chain = composite.plan_below(node)
    except composite.Unsupported as error:
        return _bake_plan(node, source, obj, str(error))

    if not chain.layers:
        raise Refused(f"There is nothing below '{node.name}' to filter")
    if gpu_known() is False:
        return _bake_plan(node, source, obj,
                          "this Blender has no GPU context to composite in")

    authored = set(chain.uv_maps) | {node.uv_map}
    if authored == {""}:
        # Every layer renders through the active render UV map, so they
        # agree whatever it is and no mesh has to be asked.
        return InputPlan(COMPOSITE, source, chain, "", "")
    if obj is None:
        raise Refused(f"The layers below '{node.name}' name a UV map, so filtering them "
                      "needs an active mesh object to resolve it against")
    resolved = {resolve_uv_map(obj, name) for name in authored}
    if None in resolved:
        missing = sorted(name for name in authored if resolve_uv_map(obj, name) is None)
        raise Refused(f"'{obj.name}' has no UV map named {missing[0]!r}")
    if len(resolved) > 1:
        names = sorted(resolved)
        raise Refused(f"Layers below use different UV maps ({names[0]!r} and {names[1]!r}), "
                      "so filtering them needs a Cycles bake")
    return InputPlan(COMPOSITE, source, chain, resolved.pop(), "")


def _bake_plan(node, source, obj, reason: str) -> InputPlan:
    """The fallback, with the bake's own preconditions checked."""
    if obj is None:
        raise Refused(f"Filtering the layers below '{node.name}' needs an active mesh "
                      f"object with a UV map, because {reason}")
    resolved = resolve_uv_map(obj, node.uv_map)
    if resolved is None:
        raise Refused(f"Filtering the layers below '{node.name}' needs a Cycles bake, "
                      f"and '{obj.name}' has no UV map named {node.uv_map!r}")
    return InputPlan(BAKE, source, None, resolved, reason)
