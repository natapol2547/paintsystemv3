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

    km_name = 'Image Paint'
    space = 'EMPTY'

    add_keymap_entry(
        kc,
        name=km_name,
        space_type=space,
        idname='wm.call_panel',
        key='RIGHTMOUSE',
        value='PRESS',
        properties={'name': 'MAT_PT_TexPaintRMBMenu'},
    )

    # Color Sampler ('I') and Toggle Erase Alpha ('E')
    add_keymap_entry(
        kc,
        name=km_name,
        space_type=space,
        idname='paint_system.color_sample',
        key='I',
    )
    add_keymap_entry(
        kc,
        name=km_name,
        space_type=space,
        idname='paint_system.toggle_brush_erase_alpha',
        key='E',
    )

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
