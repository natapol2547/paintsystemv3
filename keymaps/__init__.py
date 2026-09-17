import bpy

from .common import add_keymap_entry, unregister_keymap_entries


def register() -> None:
    # Entries go into the addon keyconfig only; nothing is removed from
    # the default or user keyconfigs. An addon item is matched before the
    # default items of its keymap, and a handled event stops at the first
    # keymap that handles it, so an item takes a combination away from
    # every keymap Blender consults after it in the same region.
    # Background Blender reports an empty default keyconfig and cannot
    # answer this; tests/test_keymaps_ui.py checks the default keyconfig
    # of the running build in a window.
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

    # Image Paint: Ctrl+D clears the selection (PS-093), the Deselect
    # shortcut of Photoshop, Clip Studio Paint and Affinity. Alt+D is not
    # used: pressing Alt before D for a straight-line annotation would
    # clear the selection, before 5.1 without an undo step.
    #
    # Image Paint is consulted in the main region of the 3D view in
    # Texture Paint and of the image editor in Paint mode. Checked on
    # 4.2.23, 4.3.2, 4.4.3, 4.5.13, 5.0.1, 5.1.2, 5.2.1, 5.3 alpha and
    # Bforartists 5.2.0, in every variant of the Blender, Blender 27X,
    # Industry Compatible and Bforartists presets from the preset data,
    # and with real key presses for the Blender, Industry Compatible and
    # Bforartists presets and both select mouse settings: no keymap
    # consulted in those regions binds Ctrl+D, before or after Image
    # Paint, so the item shadows nothing. With the item removed, a Ctrl+D
    # press there is handled by nothing. Those keymaps are Screen
    # Editing, the active tool's keymap, Grease Pencil, Paint Face Mask
    # (Weight, Vertex, Texture), Paint Curve, Object Non-modal, Frames,
    # 3D View Generic, 3D View, Image Generic, Image, Window and Screen.
    #
    # Ctrl+D elsewhere:
    # - User Interface binds it to anim.driver_button_add in the Blender,
    #   Blender 27X and Bforartists presets. Blender adds that keymap to
    #   button regions such as the sidebar and headers, not to these main
    #   regions, and a key event goes only to the region under the
    #   cursor. Over a property button in the sidebar Ctrl+D adds a
    #   driver and this item is not consulted; while a text field is
    #   edited the field takes the key.
    # - Industry Compatible binds it in Object Mode, Mesh, Sculpt and
    #   other mode keymaps whose polls fail in Texture Paint and in the
    #   image editor's Paint mode, and it binds nothing to it in the
    #   keymaps listed above.
    #
    # Known interactions, accepted:
    # - select_all returns CANCELLED when there is nothing to clear, and a
    #   cancelled operator still consumes the event. While a Paint System
    #   material is active, a Ctrl+D that a user or another add-on binds
    #   in a keymap consulted after Image Paint does not run there.
    # - Annotation with D held is unaffected. Ctrl then D clears once and
    #   D can stay held for a stroke; D then Ctrl clears nothing, since
    #   the D presses that follow are repeats and the item ignores them.
    # - The item also runs in the image editor in Paint mode: with the
    #   brush tool on every version, even with the object in Object Mode,
    #   and before 5.1 also with the annotate tool, and in View mode while
    #   the object is in Texture Paint.
    # - With region overlap, Ctrl+D over empty sidebar space reaches the
    #   region below and clears the selection; over a panel it does not.
    # - macOS presets add a Cmd copy of each Ctrl item, but not to the
    #   add-on keyconfig, so this item stays Ctrl+D there.
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
    unregister_keymap_entries()
