"""Put scripted pixel writes into Blender's image undo (PS-090).

Blender records image pixels only in image undo steps, which paint strokes
and the pixel-editing image operators push. Pixels written from Python
through ``foreach_set`` belong to no step: Ctrl+Z after a scripted write
restores nothing, and undoing a native stroke made afterwards reverts the
scripted write along with it, because the stroke's step restores the tiles
of the step before it.

``bpy.ops.image.invert`` with all four channels off changes no pixel and
pushes a full image undo step. That is the whole mechanism here: the addon
keeps no pixel stack of its own, and Blender undoes and redoes the steps
itself, in order with native strokes and memfile steps.

Undo and redo restore the pixels recorded by the step they arrive at, so an
image needs one step holding the pixels from *before* the first scripted
write, or that write is not undoable. `write_pixels` pushes that baseline
the first time it touches an image, which is why the handlers call
`forget_baselines` after every undo, redo and file read: the addon's steps
may no longer be on the stack.

Rules for callers:

- An operator that writes pixels has no ``UNDO`` option. With it Blender
  pushes a memfile step on top of the image step and every write costs two
  Ctrl+Z.
- It changes no document data in the same call. A document change that
  belongs with a pixel write is pushed as its own step before the write.
- Nothing infers pixel state from document state after undo. Owners of
  derived images rebuild them from their inputs in ``undo_post`` and
  ``redo_post``.
- Datablocks are fetched by name or ``session_uid`` after undo; Python
  references may dangle.
"""
import logging

import bpy
import numpy as np

log = logging.getLogger(__name__)

# ``session_uid`` of every image this session has pushed a step for. Stable
# across undo, new after a file reload, so it cannot outlive its stack.
_baselines: set[int] = set()

_undo_stack_ready = False


def ensure_undo_stack() -> None:
    """Give a background Blender an undo stack before an image step is pushed.

    Blender builds the window manager's undo stack when something first
    pushes to it, and does that for itself only in an interactive session.
    ``image.invert`` dereferences the stack without a null check, so the
    first call in a ``-b`` session crashes Blender on 4.2 and 5.2. Reading a
    file frees the stack again; `forget_undo_state` clears the flag.
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

    Costs about 75-120 ms and 90 MB on a 4K byte image (PS-096); the steps
    live in Blender's ``undo_memory_limit``. Edit > Undo History labels them
    "Invert Channels".
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
    PS-056 saves or packs it with the blend file.

    Returns False when the write happened but could not be registered, so
    the caller can tell the user the edit is there but not undoable.
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
    """Tell any filter layer reading *image* that its pixels moved.

    Deferred import: `filters.freshness` reaches `filters.core`, which
    imports this module. Exact rather than waiting for the depsgraph,
    which reports a stroke but says nothing about a scripted write.
    """
    from ..filters.freshness import note_image_changed
    note_image_changed([image.session_uid])


def pack_write_once(image: bpy.types.Image) -> bool:
    """Pack an image that is written once and never registered.

    Undoing past the creation of a generated image and redoing brings the
    datablock back with black pixels unless it was packed (PS-096). Images
    the addon fills once and then only reads - a wand result, a paste
    source - are packed right after that fill instead of taking a step of
    their own.
    """
    try:
        image.pack()
    except RuntimeError as error:
        log.warning("Could not pack image %r: %s", image.name, error)
        return False
    return True
