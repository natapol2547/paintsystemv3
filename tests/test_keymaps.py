"""The add-on's items in the add-on keyconfig (PS-093).

Background Blender still has an add-on keyconfig, so the items can be
read here field by field. It reports an empty default keyconfig, so
whether an item shadows a default one, and whether it fires, is checked
windowed by `test_keymaps_ui.py`.

Run:  blender -b --factory-startup --python tests/test_keymaps.py
"""
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, guarded, import_from, register_addon, section  # noqa: E402

addon = register_addon()
keymaps = import_from("keymaps")


def addon_keyconfig():
    return bpy.context.window_manager.keyconfigs.addon


def clear_items():
    """Every select_all item in an Image Paint keymap of the add-on keyconfig, with its keymap."""
    return [(km, kmi) for km in addon_keyconfig().keymaps if km.name == "Image Paint"
            for kmi in km.keymap_items if kmi.idname == "paint_system.select_all"]


def our_items():
    """Every item of an operator of this add-on in any keymap of the add-on keyconfig."""
    return [(km.name, kmi.idname) for km in addon_keyconfig().keymaps
            for kmi in km.keymap_items if kmi.idname.startswith("paint_system.")]


def test_ctrl_d():
    section("Ctrl+D clears the selection in Image Paint")
    check(addon_keyconfig() is not None, "background Blender has an add-on keyconfig")
    items = clear_items()
    check(len(items) == 1, f"exactly one select_all item in Image Paint ({len(items)})")
    if not items:
        return
    km, kmi = items[0]
    check((km.space_type, km.region_type, km.is_modal) == ('EMPTY', 'WINDOW', False),
          f"the keymap is Image Paint's own: any space, main region ({km.space_type}, {km.region_type})")
    check((kmi.map_type, kmi.type, kmi.value) == ('KEYBOARD', 'D', 'PRESS'),
          f"a D key press ({kmi.map_type}, {kmi.type}, {kmi.value})")
    # The modifier fields are ints where -1 means any, so compare values.
    modifiers = {name: getattr(kmi, name) for name in ("ctrl", "shift", "alt", "oskey", "hyper") if hasattr(kmi, name)}
    check(modifiers.pop("ctrl") == 1 and all(value == 0 for value in modifiers.values()) and not kmi.any,
          f"Ctrl and no other modifier ({kmi.ctrl=}, {modifiers}, {kmi.any=})")
    check(kmi.key_modifier == 'NONE' and not kmi.repeat and kmi.active,
          f"no key held with it, no key repeat, active ({kmi.key_modifier}, {kmi.repeat=}, {kmi.active=})")
    check(kmi.properties.action == 'DESELECT', f"the action is DESELECT ({kmi.properties.action})")
    check((km, kmi) in keymaps.addon_keymaps, "the item is recorded for unregister")


def test_reregister():
    section("disabling and enabling the add-on")
    before = sorted(our_items())
    addon.unregister()
    try:
        check(our_items() == [], f"unregistering leaves no item of this add-on ({our_items()})")
        check(keymaps.addon_keymaps == [], f"nothing is left to remove ({len(keymaps.addon_keymaps)})")
    finally:
        addon.register()
    check(len(clear_items()) == 1, f"registered again, still exactly one Ctrl+D item ({len(clear_items())})")
    check(sorted(our_items()) == before,
          f"registered again, the same items as before ({len(our_items())} items, {len(before)} before)")


guarded(test_ctrl_d)
guarded(test_reregister)
finish("KEYMAPS TEST")
