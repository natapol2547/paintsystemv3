# SPDX-License-Identifier: GPL-3.0-or-later

import logging

import bpy

log = logging.getLogger(__name__)

# (KeyMap, KeyMapItem) pairs this addon created in the addon keyconfig.
# Only these are removed on unregister. User and default keymaps are
# never touched.
addon_keymaps = []


def add_keymap_entry(
    kc: bpy.types.KeyConfig,
    name: str,
    space_type: str,
    idname: str,
    key: str,
    ctrl: bool = False,
    properties: dict | None = None,
):
    """Bind a key press to *idname* in keymap *name* and record the item for unregister."""
    km = kc.keymaps.new(name=name, space_type=space_type)
    kmi = km.keymap_items.new(idname, type=key, value='PRESS', ctrl=ctrl)
    if properties:
        for prop, prop_value in properties.items():
            try:
                setattr(kmi.properties, prop, prop_value)
            except (AttributeError, TypeError):
                log.warning("keymap %s: cannot set %s on %s", name, prop, idname)
    addon_keymaps.append((km, kmi))


def register() -> None:
    # Items go into the addon keyconfig only. Nothing is removed from the
    # default or user keyconfigs. An addon item is matched before the
    # default items of its keymap, and an event stops at the first keymap
    # that handles it. So an addon item takes its key combination away
    # from every keymap Blender checks after it in the same region.
    # Background Blender reports an empty default keyconfig and cannot
    # show such clashes, so tests/test_keymaps_ui.py checks the default
    # keyconfig of the running build in a window.
    kc = getattr(getattr(bpy.context, 'window_manager', None),
                 'keyconfigs', None)
    kc = getattr(kc, 'addon', None)
    if not kc:
        return

    # No mouse button is bound in Image Paint. Blender's Image Paint keymap
    # gives RIGHTMOUSE, with and without every modifier, to
    # paint.grab_clone and brush.stencil_control. An addon item would be
    # matched first and take them away, and the extension platform does
    # not allow shadowing default entries. The colour sampler (I) and
    # erase toggle (E) come with their operators in PS-036.

    # Image Paint: Ctrl+D clears the selection. It is the Deselect
    # shortcut in Photoshop, Clip Studio Paint and Affinity. Alt+D is not
    # used, because pressing Alt before D for a straight-line annotation
    # would clear the selection, and before 5.1 without an undo step.
    #
    # Blender checks the Image Paint keymap in the main region of the 3D
    # view in Texture Paint mode, and of the image editor in Paint mode.
    # No keymap checked in those regions binds Ctrl+D, before or after
    # Image Paint, so this item hides no other binding. Without the item,
    # Ctrl+D does nothing there. Those keymaps are Screen Editing, the
    # active tool's keymap, Grease Pencil, Paint Face Mask (Weight,
    # Vertex, Texture), Paint Curve, Object Non-modal, Frames, 3D View
    # Generic, 3D View, Image Generic, Image, Window and Screen.
    #
    # This was checked on 4.2.23, 4.3.2, 4.4.3, 4.5.13, 5.0.1, 5.1.2,
    # 5.2.1, 5.3 alpha and Bforartists 5.2.0. It was checked in every
    # variant of the Blender, Blender 27X, Industry Compatible and
    # Bforartists presets from the preset data. It was also checked with
    # real key presses for the Blender, Industry Compatible and
    # Bforartists presets, with both select mouse settings.
    #
    # Ctrl+D elsewhere:
    # - User Interface binds it to anim.driver_button_add in the Blender,
    #   Blender 27X and Bforartists presets. Blender adds that keymap to
    #   button regions such as the sidebar and headers, not to these main
    #   regions. A key event goes only to the region under the cursor.
    #   Over a property button in the sidebar, Ctrl+D adds a driver and
    #   this item is not checked. While a text field is being edited, the
    #   field takes the key.
    # - Industry Compatible binds it in Object Mode, Mesh, Sculpt and
    #   other mode keymaps. Their polls fail in Texture Paint and in the
    #   image editor's Paint mode. It binds nothing to Ctrl+D in the
    #   keymaps listed above.
    #
    # Known side effects, accepted:
    # - select_all returns CANCELLED when there is nothing to clear, and a
    #   cancelled operator still uses up the event. So while a Paint
    #   System material is active, a Ctrl+D that a user or another add-on
    #   binds in a keymap checked after Image Paint does not run there.
    # - Annotating with D held still works. Ctrl then D clears once, and
    #   D can stay held for a stroke. D then Ctrl clears nothing, because
    #   the D presses that follow are key repeats and the item ignores
    #   them.
    # - The item also runs in the image editor in Paint mode. It runs with
    #   the brush tool on every version, even with the object in Object
    #   Mode, and before 5.1 also with the annotate tool. It also runs in
    #   View mode while the object is in Texture Paint.
    # - With region overlap, Ctrl+D over empty sidebar space reaches the
    #   region below and clears the selection. Over a panel it does not.
    # - macOS presets add a Cmd copy of each Ctrl item, but not to the
    #   add-on keyconfig, so this item stays Ctrl+D on macOS.
    add_keymap_entry(
        kc,
        name='Image Paint',
        space_type='EMPTY',
        idname='paint_system.select_all',
        key='D',
        ctrl=True,
        properties={'action': 'DESELECT'},
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
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            log.debug("keymap item already gone", exc_info=True)
    addon_keymaps.clear()
