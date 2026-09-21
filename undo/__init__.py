"""Undo support for edits Blender's own undo does not cover.

`pixels` puts scripted pixel writes into Blender's image undo. The
helpers here decide when a data edit, such as a selection change or a
removed layer, gets a memfile undo step that restores it. The package
holds no classes and is not a registered submodule; import from it
directly.
"""
import bpy

from ..common import is_newer_than


UNDO_OPTIONS = {'REGISTER', 'UNDO'} if is_newer_than(5, 1) else {'REGISTER'}
"""`bl_options` for operators that edit the selection; pair with `push_undo`.

Before Blender 5.1, undo in texture paint mode steps through the image
undo stack only. A step pushed there for a selection edit restores
nothing and costs the user a Ctrl+Z that does nothing, so those versions
leave out the 'UNDO' flag and `push_undo` pushes the step where it works.
"""


UNDO_MODES = frozenset(('OBJECT', 'PAINT_TEXTURE'))
"""`context.mode` values where selection operators run; their polls check `undoable_mode`.

An edit mode has an undo stack of its own: a step pushed there for a
selection edit restores nothing on any version, and Ctrl+Z spends it.
"""


def undoable_mode(context) -> bool:
    """Whether a selection edit made now gets an undo step that restores it."""
    return context.mode in UNDO_MODES


def undo_restores_data(context) -> bool:
    """Whether Ctrl+Z can restore a data edit made now, such as a removed layer.

    It cannot in an edit mode on any version, nor in texture paint mode
    before 5.1, for the reasons `UNDO_MODES` and `UNDO_OPTIONS` give.
    """
    if context.mode.startswith('EDIT'):
        return False
    return is_newer_than(5, 1) or context.mode != 'PAINT_TEXTURE'


def push_undo(context, message: str) -> None:
    """Push the undo step `UNDO_OPTIONS` leaves out: before 5.1, outside texture paint mode."""
    if 'UNDO' in UNDO_OPTIONS or context.mode == 'PAINT_TEXTURE':
        return
    bpy.ops.ed.undo_push(message=message)
