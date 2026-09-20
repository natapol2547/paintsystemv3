# SPDX-License-Identifier: GPL-3.0-or-later
"""The stamps a filter layer's derived image carries (PS-057).

They live on the image rather than on the node for the reason the
compiler already gives for the artifact: whichever copy of the node and
whichever copy of the image undo restores, the stamp describes the pixels
sitting next to it. It is also what makes duplicating a layer and copying
an image behave.

A build writes all of them together, and only after the pixels are
packed. An unpacked generated image loses its pixels through
``image.copy()`` and comes back black after undo-then-redo, while its ID
properties survive both, so a stamp written before the pack could claim
pixels that are no longer there. `is_built` asks for the stamp *and* for
somewhere the pixels could still be, so the one case that slips through
still reads as unbuilt.
"""
from __future__ import annotations

import bpy

from ..compiler.core import ps_trees


# Bumped when a build would produce different pixels from the same
# inputs, so that every stamp an older build wrote reads as out of date.
FILTER_VERSION = 1

# "<tree uuid>:<node uuid>" of the layer that built these pixels.
OWNER_KEY = "ps_filter_owner"
# The structural token: what was asked for, not what it was computed from.
FINGERPRINT_KEY = "ps_filter_fingerprint"
# The only stamp the compiler's hashes read, so it has to change exactly
# when the pixels do. That is the fingerprint plus a digest of the result
# itself: painting on a layer below moves no property, so two builds
# around a brush stroke ask for the same thing and produce different
# pixels, and a fingerprint alone would let a cache above go on showing
# the old ones.
BUILD_KEY = "ps_filter_build"
# The UV map the pixels were laid out in, which is what the compiled
# Image Texture has to use however the layer's setting has moved since.
UV_MAP_KEY = "ps_filter_uv_map"


def build_stamp(image: bpy.types.Image | None) -> str:
    """The build token of *image*, or "" when it holds no filter result."""
    if image is None:
        return ""
    return str(image.get(BUILD_KEY, ""))


def stamped_uv_map(image: bpy.types.Image | None) -> str:
    """The UV map *image* was built in ("" means the active render map)."""
    if image is None:
        return ""
    return str(image.get(UV_MAP_KEY, ""))


def is_built(image: bpy.types.Image | None) -> bool:
    """True when *image* holds pixels a filter layer built and still has them.

    A packed image counts before anything has read it. ``has_data`` is
    False until Blender loads the buffer, and a duplicate arrives packed
    and unloaded -- while the compile is often the first thing to ask,
    which would make a Shift+D of a built layer a pass-through. The case
    ``has_data`` is here for, a generated image whose pixels an undo or a
    copy dropped, has no packed file either.
    """
    if not build_stamp(image):
        return False
    return image.packed_file is not None or image.has_data


def cleanup_orphan_derived() -> int:
    """Remove filter results no filter layer points at, and say how many.

    Modelled on `compiler.core.cleanup_orphan_artifacts`, and called from
    the same place for the same reason: a file read is the one moment
    with no undo stack to break. Sweeping at save time would set up the
    case where the name comes back attached to different pixels.

    It covers what the remove-layer operator cannot -- a node deleted in
    the node editor, a channel removed with its layers still alive, and a
    file written by a crash or an older build.
    """
    live = {node.derived_image.session_uid
            for tree in ps_trees() for node in tree.nodes
            if getattr(node, 'ps_type', "") == 'FILTER' and node.derived_image is not None}
    removed = 0
    for image in list(bpy.data.images):
        if image.get(OWNER_KEY) is None or image.session_uid in live:
            continue
        if image.users == 0 or (image.users == 1 and image.use_fake_user):
            bpy.data.images.remove(image)
            removed += 1
    return removed
