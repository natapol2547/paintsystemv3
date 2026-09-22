# SPDX-License-Identifier: GPL-3.0-or-later
"""Chooses how a filter layer gets the picture below it (PS-057).

There are two paths. The GPU composite (`filters.composite`) draws the
stack in a few passes, and is the only path the auto refresh may run.
The Cycles bake (`compiler.bake.bake_subtree`) renders it. The bake is
exact for anything, including group layers, layers that evaluate
surface data, and blend modes with no parity test, but takes seconds.

`resolve_input` picks the path and allocates nothing while doing it, so
a layer that cannot be built says why before any video memory is spent.
It has two different outcomes on purpose:

- An `InputPlan` on the ``BAKE`` path, with the cause in `reason`. Only
  the bake could build it. The bake path is not implemented yet, so
  `filters.layer_build.steps` refuses it with that reason for now.
- `filters.core.Refused`, which means neither path can build it. The
  message is shown to the user as written.

UV maps need care. The composite samples every source image by
normalised coordinate, which is only correct while the whole stack below
uses the same UV map as the derived image. Layers that all leave
`uv_map` empty use the active render map, whatever it is. That case
needs no object, so a filter layer works with nothing selected. Once any
layer names a map, a mesh is needed to tell whether the others resolve
to the same one.
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
    """How to get the picture below a filter layer.

    *source* is the node feeding the layer's ``Color`` input, which
    `compiler.core.build_ir` takes as its `bake_target`. For a clipped
    filter layer it is the base's own content, which is what such a layer
    filters. *chain* is the composite plan on the composite path. On the
    bake path it is None, and *reason* says why.

    *uv_map* is the resolved map the result is laid out in. It is "" only
    when nothing below names a map and no object was needed to decide.
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

    # A context without a screen has no `object` member. The automatic
    # refresh runs from a timer, whose context may have no screen, so it
    # falls back to the view layer's active object, which is the same one.
    obj = getattr(context, 'object', None)
    view_layer = getattr(context, 'view_layer', None)
    if obj is None and view_layer is not None:
        obj = view_layer.objects.active
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
        # Every layer uses the active render UV map, so they agree
        # whatever it is, and no mesh is needed.
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
    """The bake plan, after checking the bake's own requirements."""
    if obj is None:
        raise Refused(f"Filtering the layers below '{node.name}' needs an active mesh "
                      f"object with a UV map, because {reason}")
    resolved = resolve_uv_map(obj, node.uv_map)
    if resolved is None:
        raise Refused(f"Filtering the layers below '{node.name}' needs a Cycles bake, "
                      f"and '{obj.name}' has no UV map named {node.uv_map!r}")
    return InputPlan(BAKE, source, None, resolved, reason)
