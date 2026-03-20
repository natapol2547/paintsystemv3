import bpy

from .common import add_keymap_entry, unregister_keymap_entries


def register() -> None:
    try:
        kc = getattr(getattr(bpy.context, 'window_manager', None),
                     'keyconfigs', None)
        kc = getattr(kc, 'addon', None)
        if not kc:
            return

        km_name = 'Image Paint'
        space = 'EMPTY'

        # Tool-specific keymap names vary slightly across versions; add to a couple of common ones
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
    except Exception:
        # Keymap setup is best-effort; failures shouldn't block add-on load
        pass


def unregister() -> None:
    unregister_keymap_entries()
