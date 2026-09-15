# SPDX-License-Identifier: GPL-3.0-or-later

import logging

import bpy

log = logging.getLogger(__name__)

# (KeyMap, KeyMapItem) pairs this addon created in the addon keyconfig.
# Only these are removed on unregister; user and default keymaps are
# never touched.
addon_keymaps = []


def add_keymap_entry(
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
            except (AttributeError, TypeError):
                log.warning("keymap %s: cannot set %s on %s", name, prop, idname)
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


def unregister_keymap_entries():
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            log.debug("keymap item already gone", exc_info=True)
    addon_keymaps.clear()
