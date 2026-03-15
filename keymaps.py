# SPDX-License-Identifier: GPL-2.0-or-later

import bpy

addon_keymaps = []

ENABLE_RMB_OVERRIDE_IN_TEXPAINT = True


def _add_keymap_entry(
    kc: bpy.types.KeyConfig,
    name: str,
    space_type: str,
    idname: str,
    key: str,
    value: str = 'PRESS',
    shift: bool = False,
    ctrl: bool = False,
    alt: bool = False,
    repeat: bool = False,
    properties: dict | None = None,
):
    km = kc.keymaps.new(name=name, space_type=space_type)
    kmi = km.keymap_items.new(
        idname, type=key, value=value, shift=shift, ctrl=ctrl, alt=alt)
    if repeat:
        kmi.repeat = repeat
    if properties:
        for prop, prop_value in properties.items():
            try:
                setattr(kmi.properties, prop, prop_value)
            except Exception:
                pass
    addon_keymaps.append((km, kmi))


def find_keymap(keymap_name):
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        for km in kc.keymaps:
            if km:
                kmi = km.keymap_items.get(keymap_name)
                if kmi:
                    return kmi
    return None


def find_keymap_by_name(keymap_name) -> list[bpy.types.KeyMapItem]:
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.user
    if kc:
        for km in kc.keymaps:
            if km:
                for kmi in km.keymap_items:
                    if kmi.name == keymap_name:
                        return kmi
    return None


def register() -> None:
    try:
        kc = getattr(getattr(bpy.context, 'window_manager', None),
                     'keyconfigs', None)
        kc = getattr(kc, 'addon', None)
        if not kc:
            return

        km_name = 'Image Paint'
        space = 'EMPTY'
        # Plain RMB override in Texture Paint tool context (preferred)
        if ENABLE_RMB_OVERRIDE_IN_TEXPAINT:
            # Tool-specific keymap names vary slightly across versions; add to a couple of common ones
            _add_keymap_entry(
                kc,
                name=km_name,
                space_type=space,
                idname='wm.call_panel',
                key='RIGHTMOUSE',
                value='PRESS',
                properties={'name': 'MAT_PT_TexPaintRMBMenu'},
            )

        # Color Sampler ('I') and Toggle Erase Alpha ('E')
        _add_keymap_entry(
            kc,
            name=km_name,
            space_type=space,
            idname='paint_system.color_sample',
            key='I',
        )
        _add_keymap_entry(
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
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    addon_keymaps.clear()
