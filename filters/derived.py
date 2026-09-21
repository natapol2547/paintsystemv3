# SPDX-License-Identifier: GPL-3.0-or-later
"""The stamps (ID properties) on a filter layer's derived image (PS-057).

The stamps are stored on the image, not on the node, for the same reason
the compiler gives for its artifact. Whichever copy of the node and of
the image undo restores, the stamp describes the pixels stored next to
it. This also keeps duplicating a layer and copying an image correct.

A build writes all stamps together, and only after the pixels are
packed. An unpacked generated image loses its pixels in ``image.copy()``
and comes back black after undo then redo, but its ID properties survive
both. So a stamp written before the pack could claim pixels that are
gone. `is_built` checks for the stamp and also for a place the pixels
could still be, so an image in that state still reads as unbuilt.

Undo can also split stamps from pixels. A memfile undo restores the
packed file and the stamps, but Blender keeps an image's decoded buffer
and GPU texture across it. After an undo past a rebuild, the viewport
would keep showing the newer pixels under the older stamp.
`free_stale_buffers` compares each result's stamp with the build this
session last packed, and frees the buffers of the ones an undo changed.
"""
from __future__ import annotations

import bpy

from ..compiler.core import ps_trees


# Increase when a build would produce different pixels from the same
# inputs. Every stamp from an older build then reads as out of date.
# Do not increase it for a difference nobody can see, such as some
# values moving by one step in 255. That would only make every file
# rebuild its filter layers when opened.
FILTER_VERSION = 1

# "<tree uuid>:<node uuid>" of the layer that built these pixels.
OWNER_KEY = "ps_filter_owner"
# The structural fingerprint: what the build was asked for, not the
# pixels it was computed from.
FINGERPRINT_KEY = "ps_filter_fingerprint"
# The only stamp the compiler's hashes read, so it must change exactly
# when the pixels do. It combines the fingerprint with a digest of the
# result. Painting on a layer below changes no property, so builds
# before and after a stroke have the same fingerprint but different
# pixels. With the fingerprint alone, a cache above would keep showing
# the old pixels.
BUILD_KEY = "ps_filter_build"
# The UV map the pixels were laid out in. The compiled Image Texture
# node must use this map, even if the layer's setting has changed since.
UV_MAP_KEY = "ps_filter_uv_map"

# The build stamp of each result when this session last packed it, by
# ``session_uid``. A ``session_uid`` stays the same across undo and is
# new after a file read, so the table is cleared on load and never
# outlives its images' buffers. A result missing from the table has only
# ever decoded its own packed file, so an undo cannot have changed it.
_packed: dict[int, str] = {}


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

    A packed image counts even before anything has read it. ``has_data``
    is False until Blender loads the buffer, and a duplicated layer's
    image arrives packed but not loaded. The compile is often the first
    to ask, so without the packed check a Shift+D copy of a built layer
    would be a pass-through. The ``has_data`` check is for a generated
    image whose pixels an undo or a copy dropped. Such an image has no
    packed file either, so it still reads as unbuilt.
    """
    if not build_stamp(image):
        return False
    return image.packed_file is not None or image.has_data


def note_packed(image: bpy.types.Image) -> None:
    """Record that *image*'s packed file now holds the build it is stamped with."""
    _packed[image.session_uid] = build_stamp(image)


def free_stale_buffers() -> int:
    """Free the buffers of every result an undo moved to another build, and say how many.

    Called from ``undo_post`` and ``redo_post``. Freeing is cheap, and the
    decode it causes happens at the next draw. Only results whose stamp
    changed are freed. Freeing all of them on every Ctrl+Z would decode
    every filter layer in the file again, even for an undo that touched
    none of them.
    """
    freed = 0
    for image in bpy.data.images:
        stamp = build_stamp(image)
        known = _packed.get(image.session_uid)
        if not stamp or known is None or known == stamp:
            continue
        image.buffers_free()
        _packed[image.session_uid] = stamp
        freed += 1
    return freed


def forget_packed() -> None:
    """Drop the table of packed builds, for a file read that renews every ``session_uid``."""
    _packed.clear()


def cleanup_orphan_derived() -> int:
    """Remove filter results that no filter layer points at, and say how many.

    Works like `compiler.core.cleanup_orphan_artifacts`, and is called
    from the same place for the same reason. A file read is the one
    moment with no undo stack to break. Removing them at save time could
    lead to the name coming back attached to different pixels.

    It covers what the remove-layer operator cannot: a node deleted in
    the node editor, a channel removed while its layers still exist, and
    a file written by a crash or by an older version of the addon.
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
