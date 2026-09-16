import bpy

from .common import add_keymap_entry, unregister_keymap_entries


def register() -> None:
    # Entries go into the addon keyconfig only; nothing is removed from
    # the default or user keyconfigs.
    kc = getattr(getattr(bpy.context, 'window_manager', None),
                 'keyconfigs', None)
    kc = getattr(kc, 'addon', None)
    if not kc:
        return

    # No mouse button is bound in Image Paint. Blender's Image Paint keymap
    # gives RIGHTMOUSE to paint.grab_clone and brush.stencil_control, with
    # and without every modifier, and an addon item would be matched first
    # and take them away. Stencil control is also what PS-091 paints
    # through. The colour sampler (I) and erase toggle (E) are added by
    # PS-036 together with their operators.

    # Node editor: enter/exit group (TAB).
    add_keymap_entry(
        kc,
        name='Node Editor',
        space_type='NODE_EDITOR',
        idname='paint_system.enter_exit_node_group',
        key='TAB',
    )


def unregister() -> None:
    unregister_keymap_entries()
