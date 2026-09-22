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
uses the same UV map as the derived image. The names decide first,
because they need no mesh:

- Nothing names a map. Every layer uses the active render map, whatever
  it is, so they agree.
- Every layer below names the same map. They agree on it, and a filter
  layer with its own UV Map left empty follows them.
- Two different names. They disagree whatever the mesh.

A mesh is needed only when one map is named and some layers below leave
theirs empty. Those use the mesh's active render map, and only the mesh
can say whether that is the named one. The mesh is the one stored on the
filter layer (its Object), else the active one. So a layer resolves the
same way whatever is selected once it has been built. `surface_of` has
the rules, and `keep_surface` stores the mesh when a build goes ahead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import bpy

from ..context import get_ps_object, uses_tree
from ..gpu_passes.core import gpu_known
from ..gpu_passes.texel_map import resolve_uv_map
from ..nodetree.stack_ops import below_input, consumer_input, feeding_link, is_layer, link_index
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

    *uv_map* is the map the result is laid out in. It is "" only when
    nothing names a map, so the result uses the active render map too.

    *surface* is the mesh the plan was resolved against, or None when it
    needed none. A build stores it on the layer (`keep_surface`).
    """
    path: str
    source: bpy.types.Node
    chain: composite.ChainPlan | None
    uv_map: str
    reason: str
    surface: bpy.types.Object | None

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
            socket = consumer_input(current)
            if socket is None:
                return None
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

    link = feeding_link(below_input(node))
    if link is None:
        raise Refused(f"There is nothing below '{node.name}' to filter")
    source = link.from_node

    try:
        chain = composite.plan_below(node)
    except composite.Unsupported as error:
        return _bake_plan(context, tree, node, source, str(error))

    if not chain.layers:
        raise Refused(f"There is nothing below '{node.name}' to filter")
    if gpu_known() is False:
        return _bake_plan(context, tree, node, source,
                          "this Blender has no GPU context to composite in")
    return _composite_plan(context, tree, node, source, chain)


def _composite_plan(context, tree, node, source, chain) -> InputPlan:
    """The composite plan, after checking that the stack shares one UV map.

    The module docstring gives the rules. They read the names first, so
    a mesh is asked only when the names cannot settle it.
    """
    below = set(chain.uv_maps)
    below_named = sorted(below - {""})
    if len(below_named) > 1:
        raise Refused(f"Layers below '{node.name}' use different UV maps "
                      f"({below_named[0]!r} and {below_named[1]!r}), "
                      "so filtering them needs a Cycles bake")
    named = sorted(set(below_named) | ({node.uv_map} - {""}))
    if len(named) > 1:
        # The layers below agree, so the layer's own UV Map is the odd one.
        raise Refused(f"'{node.name}' is set to UV map {node.uv_map!r}, but the layers below "
                      f"use {below_named[0]!r}. {_FOLLOW_BELOW}")
    if not named:
        return InputPlan(COMPOSITE, source, chain, "", "", None)
    name = named[0]
    if "" not in below:
        # The mesh is not asked whether it has this map. That would make
        # the answer depend on the selection, and on a mesh without the
        # map the layers below show wrong anyway.
        return InputPlan(COMPOSITE, source, chain, name, "", None)

    obj, problem = surface_of(context, tree, node)
    if obj is None:
        raise Refused(f"'{node.name}' needs a mesh to tell which UV map the layers below use, "
                      f"but {problem}. {_PICK_MESH}")
    if resolve_uv_map(obj, name) is None:
        raise Refused(f"'{obj.name}' has no UV map named {name!r}")
    render = resolve_uv_map(obj, "")
    if render != name and not below_named:
        # Every layer below uses the render map, and only the layer's own
        # UV Map names another.
        raise Refused(f"'{node.name}' is set to UV map {name!r}, but the layers below use "
                      f"the render map {render!r} on '{obj.name}'. {_FOLLOW_BELOW}")
    if render != name:
        raise Refused(f"Layers below '{node.name}' use different UV maps on '{obj.name}' "
                      f"({name!r} and the render map {render!r}), "
                      "so filtering them needs a Cycles bake")
    return InputPlan(COMPOSITE, source, chain, name, "", obj)


def _bake_plan(context, tree, node, source, reason: str) -> InputPlan:
    """The bake plan, after checking the bake's own requirements.

    A bake renders the mesh, so it always needs one. It renders each
    layer through its own UV map, so only the filter layer's own map
    has to exist.
    """
    obj, problem = surface_of(context, tree, node)
    if obj is None:
        raise Refused(f"Filtering the layers below '{node.name}' needs a mesh to bake on, "
                      f"because {reason}, but {problem}. {_PICK_MESH}")
    resolved = resolve_uv_map(obj, node.uv_map)
    if resolved is None:
        raise Refused(f"Filtering the layers below '{node.name}' needs a Cycles bake, "
                      f"and '{obj.name}' has no UV map named {node.uv_map!r}")
    return InputPlan(BAKE, source, None, resolved, reason, obj)


# ── The mesh a layer resolves against ────────────────────────────────

_PICK_MESH = "Select a mesh that uses this Paint System tree, or set the layer's Object"
_FOLLOW_BELOW = "Clear its UV Map to follow them"


def surface_of(context, tree, node) -> tuple[bpy.types.Object | None, str]:
    """The mesh to resolve *node* against, and what is wrong when there is none.

    Returns ``(mesh, "")``, or ``(None, problem)`` where *problem* ends a
    sentence such as "but nothing is selected".

    The layer's own Object comes first, so the layer resolves the same
    way whatever is selected. The active object is the fallback, through
    `context.get_ps_object`, so an empty parented to the mesh counts as
    the mesh. Either one must be a mesh that uses *tree*. Any other mesh
    would answer for UV maps the material never sees.

    Nothing searches the file for a mesh that uses the tree. That would
    scan every object on every resolve, and it would have to guess when
    several meshes use the tree.
    """
    name = node.surface_name
    if name:
        stored = node.surface_object
        problem = "was deleted or renamed" if stored is None else unusable(stored, tree)
        if not problem:
            return stored, ""
    # A context without a screen has no `object` member. The automatic
    # refresh runs from a timer, whose context may have no screen, so it
    # falls back to the view layer's active object, which is the same one.
    active = getattr(context, 'object', None)
    view_layer = getattr(context, 'view_layer', None)
    if active is None and view_layer is not None:
        active = view_layer.objects.active
    mesh = get_ps_object(active)
    if mesh is not None and not unusable(mesh, tree):
        return mesh, ""
    if name:
        return None, f"its Object '{name}' {problem}"
    if active is None:
        return None, "nothing is selected"
    return None, f"'{active.name}' is not a mesh that uses this Paint System tree"


def unusable(obj, tree) -> str:
    """Why *obj* cannot be *tree*'s mesh, as the end of a sentence, or "".

    The layer's Object search lists only the objects this accepts.
    """
    if obj.type != 'MESH':
        return "is not a mesh"
    # Deleting an object in the viewport keeps it in the file while
    # anything else points at it, such as a modifier. It is then in no
    # collection. An object in a collection excluded from the view layer
    # is still in that collection, so it still counts.
    if not obj.users_collection:
        return "was deleted"
    if not uses_tree(obj, tree):
        return "does not use this Paint System tree"
    return ""


def keep_surface(node, plan) -> None:
    """Store the name of the mesh *plan* was resolved against as *node*'s Object.

    Called by a build once it goes ahead, never by `resolve_input`, which
    only reads. After that the layer resolves against the same mesh
    whatever is selected. A renamed mesh is stored again under its new
    name by the next build that resolves against it. On a linked tree the name lasts for the session, like the
    result the build writes next to it. Both go back to the library's
    values when the file opens again, so the two still agree.
    """
    obj = plan.surface
    if obj is None or node.surface_name == obj.name:
        return
    node.surface_name = obj.name
