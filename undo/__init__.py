"""Undo support for edits Blender's own undo does not cover.

`pixels` puts scripted pixel writes into Blender's image undo. The
helpers here decide when a data edit, such as a selection change or a
removed layer, gets a memfile undo step that restores it. The package
has no classes and is not registered as a submodule. Import from it
directly.
"""
import bpy

from ..common import is_newer_than


UNDO_OPTIONS = {'REGISTER', 'UNDO'} if is_newer_than(5, 1) else {'REGISTER'}
"""`bl_options` for operators that edit the selection. Use with `push_undo`.

Before Blender 5.1, undo in texture paint mode only steps through the
image undo stack. A step pushed there for a selection edit restores
nothing and costs the user a Ctrl+Z that does nothing. So those versions
leave out the 'UNDO' flag, and `push_undo` pushes the step only where it
works.
"""


UNDO_MODES = frozenset(('OBJECT', 'PAINT_TEXTURE'))
"""`context.mode` values where the selection operators can run.

Their polls check it through `undoable_mode`. An edit mode has an undo
stack of its own. A step pushed there for a selection edit restores
nothing on any version, and Ctrl+Z spends it.
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
    """Push the undo step that `UNDO_OPTIONS` leaves out.

    Pushes only before 5.1 and outside texture paint mode. On 5.1 and
    later the operator's 'UNDO' flag makes the step.
    """
    if 'UNDO' in UNDO_OPTIONS or context.mode == 'PAINT_TEXTURE':
        return
    bpy.ops.ed.undo_push(message=message)
