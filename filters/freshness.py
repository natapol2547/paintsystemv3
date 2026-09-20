# SPDX-License-Identifier: GPL-3.0-or-later
"""Whether a filter layer's pixels still describe the layers below (PS-057).

This is the structural half: what the build was *asked* for, recomputed
on every compile and compared with what the image was stamped with. It is
free, because `compiler.core.CompileContext.subtree_hash` is memoised per
compile and already covers a layer added, removed, reordered or muted, a
fill colour, another layer's blend mode or opacity, a clip flag, and an
image datablock swapped or renamed.

Note what it hashes: the upstream of the filter's ``Color`` input, not
the filter node itself. Opacity, Amount, `enabled`, Mask and Clip are
outside it by construction, which is the point -- fading a built filter
must not make it ask to be rebuilt.

The parts are stamped as they are rather than as one hash, so that a
disagreement can say which one moved. "Out of date" with no reason is a
button the user has to press on faith.

The other half is pixels. `compiler.ir` reduces a datablock to its name,
so painting into an image below changes no hash at all and the structural
half is blind to it by construction. `note_image_changed` closes that:
Blender tags a painted image in the depsgraph at the end of a stroke, the
addon's own pixel writes say so directly, and either way every filter
layer reading that image is marked. The flag lives on the node rather than
on the image, because it is a statement about a build and not about the
pixels.

Which images a filter layer reads is worked out on demand rather than
recorded at build time. `filters.composite.plan_below` already answers it
exactly, the walk only runs when an image is actually tagged, and a
recorded set would be one more thing to invalidate -- while any change to
*which* images are below is a structural change the other half already
catches.
"""
from __future__ import annotations

import json
import logging

from ..compiler.core import ps_trees
from . import composite, derived
from .core import Refused
from .layer_specs import LAYER_FILTERS

log = logging.getLogger(__name__)

PIXEL_REASON = "the pixels below changed"

# Checked in this order, so the reason names the most useful difference
# when several moved at once.
REASONS = (
    ("below", "the layers below changed"),
    ("params", "the filter settings changed"),
    ("filter", "the filter changed"),
    ("size", "the resolution changed"),
    ("uv_map", "the UV map changed"),
    ("version", "Paint System was updated"),
)


def fingerprint_parts(ctx, node, below) -> dict:
    """What a build of *node* would be asked for, as named parts.

    *below* is the node feeding the filter's ``Color`` input, which is
    the whole stack the filter replaces. *ctx* is any
    `compiler.core.CompileContext`: the hash is a function of the tree,
    not of the context that walked it, so the one a build makes and the
    one a compile is holding agree.
    """
    kind = LAYER_FILTERS.get(node.filter_type)
    size = int(node.resolution)
    return {
        "version": derived.FILTER_VERSION,
        "filter": node.filter_type,
        # An unregistered kind has no parameters to read. The build
        # refuses such a layer outright; here it only has to not raise.
        "params": kind.params_of(node) if kind is not None else {},
        "size": [size, size],
        # The map as authored, not as resolved. Resolving needs a mesh,
        # and a compile serves every object the tree is on.
        "uv_map": node.uv_map,
        "below": ctx.subtree_hash(below) if below is not None else "empty",
    }


def stamp(parts: dict) -> str:
    """*parts* as the string stored in `derived.FINGERPRINT_KEY`."""
    return json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)


def structure_reason(stored: str, parts: dict) -> str:
    """Why *parts* disagrees with the stamped *stored*, or "" when it does not."""
    if not stored:
        return "it was not built from this stack"
    try:
        was = json.loads(stored)
    except ValueError:
        log.debug("unreadable filter fingerprint: %r", stored)
        return "its build could not be read"
    # json round-trips a tuple as a list, so compare the encodings rather
    # than the dicts: `params` holds a tuple on one side and a list on
    # the other, and every other value is already a string or a number.
    if stamp(was) == stamp(parts):
        return ""
    for key, reason in REASONS:
        if stamp(was.get(key)) != stamp(parts.get(key)):
            return reason
    return "something it was built from changed"


# ── The pixel half ───────────────────────────────────────────────────


def note_image_changed(uids) -> None:
    """Mark every built filter layer that reads one of *uids*.

    *uids* are `Image.session_uid` values: the datablock a name lookup
    would miss after a rename, and what the depsgraph hands over.

    Called from `handlers.node_tree_handlers.on_depsgraph_update_post`
    with whatever Blender tagged, and from the addon's own pixel writes,
    which are exact rather than tagged. Setting the flag is all this
    does -- nothing rebuilds from here, because a filter layer goes on
    rendering its previous pixels until something asks it not to.
    """
    uids = {int(uid) for uid in uids}
    if not uids:
        return
    for tree in ps_trees():
        for node in tree.nodes:
            if getattr(node, 'ps_type', "") != 'FILTER' or node.derived_stale_pixels:
                continue
            # An unbuilt layer has no claim about pixels to lose, and the
            # walk below is the expensive part.
            if not derived.is_built(node.derived_image) or not uids & source_uids(node):
                continue
            node.derived_stale_pixels = True
            log.debug("%s reads an image that changed", node.name)


def source_uids(node) -> frozenset[int]:
    """The `session_uid` of every image the layers below *node* read.

    Empty when the composite cannot plan the stack -- there is nothing
    below, or it holds something only a Cycles bake can draw. Both mean
    the layer was not built from what is there now, which the structural
    half already says.
    """
    try:
        chain = composite.plan_below(node)
    except (composite.Unsupported, Refused):
        return frozenset()
    return frozenset(image.session_uid for image in chain.images)
