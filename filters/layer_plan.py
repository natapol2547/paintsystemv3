# SPDX-License-Identifier: GPL-3.0-or-later
"""Chooses how a filter layer gets the picture below it (PS-057).

The GPU composite (`filters.composite`) draws the stack in a few
passes. It is the only path built so far. A stack it cannot draw, such
as one with a group layer, a layer that evaluates surface data or a
blend mode with no parity test, would need a Cycles bake (Path B in
PS-057). That is not built yet, so such a stack is refused with the
reason, and the message says a bake is what it needs.

`resolve_input` allocates nothing while it decides, so a layer that
cannot be built says why before any video memory is spent. It returns
an `InputPlan`, or raises `filters.core.Refused` with a message that is
shown to the user as written.

UV maps need care. The composite samples every source image by
normalised coordinate, which is only correct while the whole stack below
uses the same UV map as the derived image. The names decide first,
because they need no mesh:

- Nothing names a map. Every layer uses the active render map, whatever
  it is, so they agree.
- Every layer below names the same map. They agree on it, and a filter
  layer with its own UV Map left empty follows them.
- Two different names. They disagree whatever the mesh.

For the UV maps, a mesh is needed only when one map is named and some
layers below leave theirs empty. Those use the mesh's active render map,
and only the mesh can say whether that is the named one. A kind that
`needs_surface`, such as the painter, needs one whatever the maps say.
`_seam_surface` checks it before the build starts.

The mesh is the one stored on the filter layer (its Object), else the
active one. So a layer resolves the same way whatever is selected once
it has been built. `surface_of` has the rules, and `keep_surface` stores
the mesh when a build goes ahead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import bpy

from ..context import get_ps_object, uses_tree
from ..gpu_passes.core import gpu_known
from ..gpu_passes.texel_map import resolve_uv_map
from ..nodetree.stack_ops import below_input, channel_of, feeding_link
from . import composite
from .core import Refused
from .layer_specs import LAYER_FILTERS

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InputPlan:
    """How to get the picture below a filter layer.

    *source* is the node feeding the layer's ``Color`` input. For a
    clipped filter layer it is the base's own content, which is what such
    a layer filters. *chain* is the composite plan.

    *uv_map* is the map the result is laid out in. It is "" only when
    nothing names a map, so the result uses the active render map too.

    *surface* is the mesh the plan was resolved against, or None when it
    needed none. A kind that `needs_surface` always has one. A build
    stores it on the layer (`keep_surface`).
    """
    source: bpy.types.Node
    chain: composite.ChainPlan
    uv_map: str
    surface: bpy.types.Object | None

    @property
    def reads_render_map(self) -> bool:
        """Whether the result depends on which UV map *surface* renders with.

        Layers below that leave their UV map empty use that map, and the
        plan checked that it is the named one. A kind that `needs_surface`
        reads the mesh through that map when nothing names one. A stack
        that names its map, with no layer below leaving it empty, uses
        neither, whatever the mesh renders with.
        """
        return self.surface is not None and (not self.uv_map or "" in self.chain.uv_maps)


def resolve_input(context, tree, node) -> InputPlan:
    """Decide how the stack below *node* is turned into pixels.

    Raises `filters.core.Refused` when the composite cannot draw it,
    naming the layer or image at fault.
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
        raise _needs_bake(node, str(error)) from error

    if not chain.layers:
        raise Refused(f"There is nothing below '{node.name}' to filter")
    if gpu_known() is False:
        raise _needs_bake(node, "this Blender has no GPU context to composite in")
    plan = _composite_plan(context, tree, node, source, chain)
    # An unregistered kind is refused by the build, with its own message.
    kind = LAYER_FILTERS.get(node.filter_type)
    if kind is not None and kind.needs_surface:
        plan = _seam_surface(context, tree, node, plan)
    return plan


def _needs_bake(node, reason: str) -> Refused:
    """The refusal of a stack only a Cycles bake could draw.

    It asks for no mesh, although a bake would render one, because
    selecting a mesh cannot help until the bake is built.
    """
    return Refused(f"Filtering the layers below '{node.name}' needs a Cycles bake, "
                   f"because {reason}")


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
        return InputPlan(source, chain, "", None)
    name = named[0]
    if "" not in below:
        # The mesh is not asked whether it has this map. That would make
        # the answer depend on the selection, and on a mesh without the
        # map the layers below show wrong anyway.
        return InputPlan(source, chain, name, None)

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
    return InputPlan(source, chain, name, obj)


def _seam_surface(context, tree, node, plan) -> InputPlan:
    """*plan*, with the mesh whose UV seams *node*'s strokes are to follow.

    Reuses the mesh the UV maps were settled on, if any, instead of
    looking it up again. Everything the mesh can be refused for is
    checked here, before the build starts. So a layer that cannot be
    built says why and waits, with Auto Refresh still on.
    """
    obj = plan.surface
    if obj is None:
        obj, problem = surface_of(context, tree, node)
        if obj is None:
            raise Refused(f"'{node.name}' needs a mesh to continue its strokes across UV seams, "
                          f"but {problem}. {_PICK_MESH}")
    name = resolve_uv_map(obj, plan.uv_map)
    if name is None and plan.uv_map:
        raise Refused(f"'{obj.name}' has no UV map named {plan.uv_map!r}")
    if name is None:
        raise Refused(f"'{obj.name}' has no UV map, so '{node.name}' cannot find its UV seams")
    # Also true while another object sharing the mesh is in Edit Mode.
    # The mesh then holds what it had before Edit Mode, not what is shown.
    if obj.data.is_editmode:
        raise Refused(f"The mesh of '{obj.name}' is in Edit Mode, so its UV seams cannot be read")
    # The evaluated mesh is the one that renders, so it is the one
    # checked. An object disabled in the viewport, or in a collection
    # excluded from the view layer, is not evaluated, and `evaluated_get`
    # then returns the object without its modifiers.
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    if not evaluated.is_evaluated:
        raise Refused(f"'{obj.name}' is disabled in the viewport or excluded from the view layer, "
                      "so its UV seams cannot be read")
    # Some modifiers, such as Remesh, leave the mesh without UV maps.
    uv = evaluated.data.attributes.get(name)
    if uv is None or uv.domain != 'CORNER' or uv.data_type != 'FLOAT2':
        raise Refused(f"The modifiers on '{obj.name}' remove UV map {name!r}, "
                      "so its UV seams cannot be read")
    return replace(plan, surface=obj)


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
