"""Put scripted pixel writes into Blender's image undo.

Main entry point: `write_pixels`. Design:
docs/tickets/PS-090-pixel-undo-stack.md.

Blender stores image pixels only in image undo steps. Paint strokes and
the image operators that edit pixels push these steps. Pixels written
from Python with ``foreach_set`` are in no step. Ctrl+Z after such a
write restores nothing. Also, undoing a normal stroke made afterwards
reverts the scripted write too, because the stroke's step restores the
tiles as they were in the step before it.

``bpy.ops.image.invert`` with all four channels off changes no pixel,
but it still pushes a full image undo step. This module relies on that
alone. The addon keeps no pixel stack of its own. Blender undoes and
redoes the steps itself, in order with normal strokes and memfile steps.

Undo and redo restore the pixels stored in the step they land on. So an
image needs one step that holds its pixels from *before* the first
scripted write, or that write cannot be undone. `write_pixels` pushes
this baseline step the first time it writes to an image. The handlers
call `forget_baselines` after every undo and redo, and
`forget_undo_state` after a file read, because the addon's steps may no
longer be on the stack.

Rules for callers:

- An operator that writes pixels must not have the ``UNDO`` option. With
  it, Blender pushes a memfile step on top of the image step, and every
  write costs two Ctrl+Z.
- It must not change document data in the same call. A document change
  that belongs with a pixel write is pushed as its own step before the
  write.
- Nothing may work out pixel state from document state after undo.
  Owners of derived images rebuild them from their inputs in
  ``undo_post`` and ``redo_post``.
- After undo, fetch datablocks by name or ``session_uid``. Python
  references may point at freed data.
"""
import logging

import bpy
import numpy as np

log = logging.getLogger(__name__)

# ``session_uid`` of every image this session has pushed a step for. The
# uid stays the same across undo and changes on a file reload, so an entry
# cannot outlive its undo stack.
_baselines: set[int] = set()

_undo_stack_ready = False


def ensure_undo_stack() -> None:
    """Give a background Blender an undo stack before an image step is pushed.

    Blender creates the window manager's undo stack on the first push, and
    only does this by itself in an interactive session. ``image.invert``
    uses the stack without checking for null, so its first call in a
    ``-b`` session crashes Blender on 4.2 and 5.2. Reading a file frees
    the stack again, and `forget_undo_state` resets the flag.
    """
    global _undo_stack_ready
    if _undo_stack_ready or not bpy.app.background:
        return
    bpy.ops.ed.undo_push(message="Paint System")
    _undo_stack_ready = True


def forget_baselines() -> None:
    """Forget which images hold a baseline step. Call after undo and redo."""
    _baselines.clear()


def forget_undo_state() -> None:
    """Forget the stack as well as the baselines. Call after a file read."""
    global _undo_stack_ready
    _undo_stack_ready = False
    _baselines.clear()


def push_undo_step(image: bpy.types.Image) -> bool:
    """Push an image undo step holding the current pixels of *image*.

    Costs about 75-120 ms and 90 MB for a 4K byte image. The steps count
    against Blender's ``undo_memory_limit``. Edit > Undo History lists
    them as "Invert Channels".
    """
    ensure_undo_stack()
    try:
        with bpy.context.temp_override(edit_image=image):
            result = bpy.ops.image.invert(invert_r=False, invert_g=False,
                                          invert_b=False, invert_a=False)
    except RuntimeError as error:
        log.warning("Could not put image %r in the undo stack: %s", image.name, error)
        return False
    if 'FINISHED' not in result:
        log.warning("Could not put image %r in the undo stack: %s", image.name, result)
        return False
    _baselines.add(image.session_uid)
    return True


def write_pixels(image: bpy.types.Image, pixels) -> bool:
    """Write *pixels* into *image* so that one Ctrl+Z takes them back.

    *pixels* holds ``width * height * channels`` float values in Blender's
    own layout, bottom row first. The write marks the image dirty, so
    `handlers.node_tree_handlers.on_save_pre` saves or packs it with the
    blend file.

    Returns False when the pixels were written but no undo step could be
    pushed. The caller can then tell the user that the edit cannot be
    undone.
    """
    width, height = image.size
    if not width or not height:
        raise ValueError(f"Image {image.name!r} has no pixel buffer to write to")
    values = np.asarray(pixels, dtype=np.float32).ravel()
    expected = width * height * image.channels
    if values.size != expected:
        raise ValueError(f"Image {image.name!r} takes {expected} values, not {values.size}")

    registered = True
    if image.session_uid not in _baselines:
        # Nothing on the stack holds the pixels this write replaces.
        registered = push_undo_step(image)
    image.pixels.foreach_set(values)
    image.update()
    _note_filters(image)
    return push_undo_step(image) and registered


def _note_filters(image: bpy.types.Image) -> None:
    """Tell any filter layer that reads *image* that its pixels changed.

    This is done here because the depsgraph reports a paint stroke but
    not a scripted write. The import is inside the function because
    `filters.freshness` leads to `filters.core`, which imports this
    module.
    """
    from ..filters.freshness import note_image_changed
    note_image_changed([image.session_uid])
