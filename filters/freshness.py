# SPDX-License-Identifier: GPL-3.0-or-later
"""Whether a filter layer's pixels still match the layers below (PS-057).

There are two halves: structure and pixels.

The structural half recomputes what a build would be asked for
(`fingerprint_parts`) on every compile, and compares it with the stamp on
the image. This costs almost nothing, because
`compiler.core.CompileContext.subtree_hash` is memoised per compile. That
hash already covers a layer added, removed, reordered or muted, a fill
colour, another layer's blend mode or opacity, a clip flag, and an image
datablock swapped or renamed.

- It hashes what feeds the filter's ``Color`` input, not the filter node
  itself. So Opacity, Amount, `enabled` and Mask are left out on
  purpose. Fading a built filter must not make it ask for a rebuild.
- Clip on the filter layer is the exception. A clipped layer's ``Color``
  input carries its clip base's own content, not the stack below,
  because the base leaves its blend to the top of the clip run. So
  toggling Clip changes what the layer filters, but the node feeding the
  socket stays the same, and `subtree_hash` cannot see it. Clip gets its
  own part.
- The parts are stamped separately, not as one hash, so a mismatch can
  say which part changed. "Out of date" with no reason is a button the
  user has to press on faith.

The pixel half covers painting. `compiler.ir` reduces a datablock to its
name, so painting into an image below changes no hash at all.
`note_image_changed` handles this. Blender tags a painted image in the
depsgraph at the end of a stroke, and the addon's own pixel writes call
it directly. Either way, every filter layer reading that image is
marked. The flag lives on the node, not the image, because it describes
a build and not the pixels.

- Which images a filter layer reads is worked out when needed
  (`source_uids`), not recorded at build time.
  `filters.composite.plan_below` already answers it exactly, and it only
  runs when an image is tagged. A recorded set would be one more thing
  to invalidate. A change in which images are below is already a
  structural change.
- A build in flight needs more than the flag. The layer is usually
  already marked, which is why it is being built, so a second stroke
  during the build would change nothing. The commit would then clear the
  flag for pixels the build never saw. `reading` covers that time. While
  a build of a layer runs, every stroke below it adds to `changes`,
  marked or not. The commit clears the flag only when the count still
  matches the one it started with.
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
# when several changed at once. `params` comes after `filter` and `size`
# because it changes along with either one. Switching the filter changes
# the settings too, and a Painterly layer's settings record its blur in
# texels, which a new resolution changes.
REASONS = (
    ("below", "the layers below changed"),
    ("clip", "clipping changed what it filters"),
    ("filter", "the filter changed"),
    ("size", "the resolution changed"),
    ("params", "the filter settings changed"),
    ("uv_map", "the UV map changed"),
    ("version", "Paint System was updated"),
)


def fingerprint_parts(ctx, node, below) -> dict:
    """What a build of *node* would be asked for, as a dict of named parts.

    *below* is the node feeding the filter's ``Color`` input, which is
    the whole stack the filter replaces. *ctx* can be any
    `compiler.core.CompileContext`. The hash depends only on the tree,
    not on the context that walked it, so a build's context and a
    compile's context give the same result.
    """
    kind = LAYER_FILTERS.get(node.filter_type)
    size = int(node.resolution)
    parts = {
        "version": derived.FILTER_VERSION,
        "filter": node.filter_type,
        # See `LayerFilterSpec.fingerprint`. An unregistered kind has
        # nothing to record. The build refuses such a layer anyway, so
        # this only has to avoid raising.
        "params": kind.fingerprint(node) if kind is not None else [],
        "size": [size, size],
        # The map name as set on the layer, not resolved. Resolving needs
        # a mesh, and one compile serves every object that uses the tree.
        "uv_map": node.uv_map,
        "below": ctx.subtree_hash(below) if below is not None else "empty",
    }
    # Uses `clip_base`, not `is_clip`. A clipped layer with no unclipped
    # layer under it composites as if it were not clipped, and filters
    # the same stack.
    #
    # The part is only present when the layer is clipped. So a stamp
    # written without this part still matches for an unclipped layer. A
    # clipped layer with such a stamp reads as out of date. Its pixels
    # are probably right, because both build paths honour the clip. But
    # the stamp cannot say whether the layer was clipped before or after
    # the build, and clipping after the build is the case this part
    # exists to catch. One rebuild is the price.
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
    # JSON turns a tuple into a list, so compare the encoded strings, not
    # the dicts. `params` holds a tuple on one side and a list on the
    # other. Every other value is already a string or a number.
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
# Node uuid to the number of strokes noticed below that layer. It is only
# compared with an earlier value of itself, so it is never reset.
_changes: dict[str, int] = {}


@contextlib.contextmanager
def reading(uuid: str):
    """Count every stroke below the layer *uuid* for as long as this is open.

    A build holds this from before it reads anything until after it
    commits. A stroke in between is then counted even when the layer is
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
    """Mark every built filter layer that reads one of the images *uids*.

    *uids* are `Image.session_uid` values. They still find the image after
    a rename, where a name lookup would miss, and they are what the
    depsgraph provides.

    Called from `handlers.node_tree_handlers.on_depsgraph_update_post`
    with the images Blender tagged, and directly from the addon's own
    pixel writes. This only sets the flag and counts the stroke. It never
    rebuilds anything itself. A filter layer keeps rendering its previous
    pixels until something asks it to rebuild.
    """
    uids = {int(uid) for uid in uids}
    if not uids:
        return
    for tree in ps_trees():
        for node in tree.nodes:
            if getattr(node, 'ps_type', "") != 'FILTER':
                continue
            read = building(node.uuid)
            # A marked layer needs nothing more, unless a build of it is
            # running. That build may have read the pixels already.
            if node.derived_stale_pixels and not read:
                continue
            # Skip an unbuilt layer. It makes no claim about pixels, and
            # the walk below is the expensive part. A layer being built
            # for the first time is about to make such a claim, so it is
            # not skipped.
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

    Empty when the composite cannot plan the stack, because there is
    nothing below or it holds something only a Cycles bake can draw.
    Both mean the layer was not built from what is there now, and the
    structural half already reports that.
    """
    try:
        chain = composite.plan_below(node)
    except (composite.Unsupported, Refused):
        return frozenset()
    return frozenset(image.session_uid for image in chain.images)
