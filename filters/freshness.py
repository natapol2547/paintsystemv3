# SPDX-License-Identifier: GPL-3.0-or-later
"""Whether a filter layer's pixels still describe the layers below (PS-057).

This is the structural half: what the build was *asked* for, recomputed
on every compile and compared with what the image was stamped with. It is
free, because `compiler.core.CompileContext.subtree_hash` is memoised per
compile and already covers a layer added, removed, reordered or muted, a
fill colour, another layer's blend mode or opacity, a clip flag, and an
image datablock swapped or renamed.

Note what it hashes: the upstream of the filter's ``Color`` input, not
the filter node itself. Opacity, Amount, `enabled` and Mask are outside
it by construction, which is the point -- fading a built filter must not
make it ask to be rebuilt.

Clip is the exception, and looks like one of those until you follow what
the socket carries. A clipped layer's ``Color`` input is its clip base's
own content rather than the stack below, because the base holds its
blend for the top of the run to make. So toggling Clip on a filter layer
swaps what it filters without moving one property upstream, and
`subtree_hash` cannot see it: the node feeding the socket is the same
node either way.

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

A build in flight is the one reader the flag cannot serve on its own. The
layer is usually marked already -- that is why it is being built -- so a
second stroke landing partway through would change nothing, and the
commit clearing the flag would then claim pixels the build never saw.
`reading` covers that span: while a build of a layer is running, every
stroke below it is counted in `changes`, marked or not, and the commit
clears the flag only when the count is the one it started with.
"""
from __future__ import annotations

import contextlib
import json
import logging

from ..compiler.core import ps_trees
from ..nodetree.stack_ops import clip_base
from . import composite, derived
from .core import Refused
from .layer_specs import LAYER_FILTERS

log = logging.getLogger(__name__)

PIXEL_REASON = "the pixels below changed"

# Checked in this order, so the reason names the most useful difference
# when several moved at once. The resolution comes before the settings
# because a Painterly layer's settings record its blur in texels, which
# a new resolution changes as well.
REASONS = (
    ("below", "the layers below changed"),
    ("clip", "clipping changed what it filters"),
    ("size", "the resolution changed"),
    ("params", "the filter settings changed"),
    ("filter", "the filter changed"),
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
    parts = {
        "version": derived.FILTER_VERSION,
        "filter": node.filter_type,
        # See `LayerFilterSpec.fingerprint`. An unregistered kind has
        # nothing to read -- the build refuses such a layer outright, so
        # here it only has to not raise.
        "params": kind.fingerprint(node) if kind is not None else [],
        "size": [size, size],
        # The map as authored, not as resolved. Resolving needs a mesh,
        # and a compile serves every object the tree is on.
        "uv_map": node.uv_map,
        "below": ctx.subtree_hash(below) if below is not None else "empty",
    }
    # `clip_base` rather than `is_clip`, because a clipped layer with no
    # unclipped layer under it composites as if it were not clipped and
    # filters the same stack.
    #
    # Present only when it is clipped, so that a stamp written before
    # this part existed still matches for the unclipped layer it was
    # already right about. A clipped layer whose stamp predates the part
    # reads as out of date instead. Its pixels were most likely right --
    # both build paths have always honoured the clip -- but the stamp
    # cannot say whether the layer was clipped before or after the build,
    # and clipped afterwards is exactly the case this part exists to
    # catch. One rebuild is the cost of not being able to tell.
    if clip_base(node) is not None:
        parts["clip"] = True
    return parts


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

# Node uuid to the number of builds of that layer in flight. The auto job
# skips a layer listed here, so in practice the count is 0 or 1.
_reading: dict[str, int] = {}
# Node uuid to the strokes below that layer noticed so far. Only ever
# compared with an earlier value of itself, so it is never reset.
_changes: dict[str, int] = {}


@contextlib.contextmanager
def reading(uuid: str):
    """Count every stroke below the layer *uuid* for as long as this is open.

    Held by a build from before it reads anything until after it commits,
    so that a stroke landing in between is seen even when the layer is
    already marked.
    """
    _reading[uuid] = _reading.get(uuid, 0) + 1
    try:
        yield
    finally:
        left = _reading[uuid] - 1
        if left:
            _reading[uuid] = left
        else:
            del _reading[uuid]


def building(uuid: str) -> bool:
    """True while a build of the layer *uuid* is running."""
    return uuid in _reading


def changes(uuid: str) -> int:
    """How many strokes below the layer *uuid* have been noticed, to compare later."""
    return _changes.get(uuid, 0)


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
            if getattr(node, 'ps_type', "") != 'FILTER':
                continue
            read = building(node.uuid)
            # A marked layer has nothing more to learn, unless a build of
            # it is running: that build may have read the pixels already.
            if node.derived_stale_pixels and not read:
                continue
            # An unbuilt layer has no claim about pixels to lose, and the
            # walk below is the expensive part. One being built for the
            # first time is about to make such a claim.
            if not (read or derived.is_built(node.derived_image)):
                continue
            if not uids & source_uids(node):
                continue
            _changes[node.uuid] = _changes.get(node.uuid, 0) + 1
            if not node.derived_stale_pixels:
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
