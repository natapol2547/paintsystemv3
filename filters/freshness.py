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

The other half -- painting into an image below, which changes no hash
because `compiler.ir` reduces a datablock to its name -- is not here yet.
Until it is, a stroke under a filter layer needs a manual Update.
"""
from __future__ import annotations

import json
import logging

from . import derived
from .layer_specs import LAYER_FILTERS

log = logging.getLogger(__name__)

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
