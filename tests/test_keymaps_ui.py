"""The add-on's keymap items in a real window (PS-093).

Two parts. The first reads the default keyconfig, which background
Blender leaves empty: no keymap Blender consults in the main region of
the 3D view in Texture Paint, or of the image editor in Paint mode, may
bind Ctrl+D, or the add-on's Ctrl+D item would take the combination
away from Blender or lose it to Blender. Like `test_icons.py` it fails
on a future Blender that starts using the combination, so the item is
reviewed again before it ships there.

The second presses Ctrl+D through Blender's own event handling with
simulated input. `--enable-event-simulate` also makes Blender ignore
real input while the test runs. `select_all`'s execute is wrapped to
record every call, so a check can tell that the item ran, with which
action, and that it did not raise. It also presses Alt+D, which clears
nothing, and Ctrl+D over the sidebar's Opacity field, where Blender's
User Interface keymap adds a driver and the selection is kept.

Not covered: annotation with D held through a drag. Only real input
records a held key, and a simulated D held through a drag draws
nothing. keymaps/__init__.py describes how the two interact.

Run:  blender --factory-startup --enable-event-simulate --python tests/test_keymaps_ui.py
"""
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import MODIFIER_KEYS, check, finish, import_from, register_addon, section, simulate  # noqa: E402

if bpy.app.background:
    print("test_keymaps_ui.py needs a window; run without -b")
    sys.exit(2)

register_addon()
tools = import_from("tools")
selection_ops = import_from("ops.selection_ops")
session = import_from("selection.session")
raster = import_from("selection.raster")

TEXTURE_PAINT_KEYMAPS = (
    "Screen Editing", "Grease Pencil", "Paint Face Mask (Weight, Vertex, Texture)", "Paint Curve", "Image Paint",
    "Object Non-modal", "Frames", "3D View Generic", "3D View", "Window", "Screen",
)
"""Keymaps consulted in the 3D view's main region in Texture Paint, besides the active tool's."""

IMAGE_PAINT_KEYMAPS = (
    "Screen Editing", "Frames", "Grease Pencil", "Paint Curve", "Image Paint", "Image Generic", "Image",
    "Window", "Screen",
)
"""Keymaps consulted in the image editor's main region in Paint mode, besides the active tool's."""

CTRL_D = {"ctrl": 1, "shift": 0, "alt": 0, "oskey": 0, "hyper": 0}
"""The modifier fields of a Ctrl+D press; 4.2 to 4.3 have no `hyper`."""

calls = []
select_all = selection_ops.PAINTSYSTEM_OT_select_all
_execute = select_all.execute


def _recording_execute(self, context):
    try:
        result = _execute(self, context)
    except Exception:
        calls.append((self.action, None, traceback.format_exc()))
        raise
    calls.append((self.action, set(result), None))
    return result


select_all.execute = _recording_execute


def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    return max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)


def main_region(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def override(area=None):
    area = area or view3d()
    return bpy.context.temp_override(window=window(), area=area, region=main_region(area))


def cube():
    return bpy.data.objects["Cube"]


def tree():
    return cube().active_material.paint_system.tree


def op_count():
    return len(tree().selection.ops)


def active_tool():
    ref = window().workspace.tools.from_space_view3d_mode('PAINT_TEXTURE', create=False)
    return ref.idname if ref is not None else None


def wait_for(condition, timeout=5.0):
    """Yield to the window loop until *condition()* holds or *timeout* seconds pass."""
    waited = 0.0
    while not condition() and waited < timeout:
        yield 0.05
        waited += 0.05


def settle():
    yield from wait_for(lambda: not bpy.app.timers.is_registered(session._tick))
    yield 0.1


def select_everything():
    selection = tree().selection
    selection.clear()
    selection.add_op('ALL')
    session.notify()
    yield from settle()


def centre(area):
    region = main_region(area)
    return (region.x + region.width // 2, region.y + region.height // 2)


def press_d(modifier, point):
    """Press and release D with *modifier* (``"ctrl"`` or ``"alt"``) held at window pixel *point*, the modifier first."""
    key = MODIFIER_KEYS[modifier]
    held = {modifier: True}
    simulate(window(), 'MOUSEMOVE', 'NOTHING', *point)
    yield
    simulate(window(), key, 'PRESS', *point, **held)
    yield
    simulate(window(), 'D', 'PRESS', *point, **held)
    yield
    simulate(window(), 'D', 'RELEASE', *point, **held)
    yield
    simulate(window(), key, 'RELEASE', *point)
    yield 0.2


def binds_ctrl_d(kmi):
    """Whether *kmi* reacts to a Ctrl+D press, or would be hidden by an item that handles one.

    Every value but NOTHING counts: a click, drag or release item on the
    same key is either hidden by the handled press or fires with it.
    Modifier fields of -1 and `any` match any state of that modifier.
    """
    if not kmi.active or kmi.map_type != 'KEYBOARD' or kmi.type != 'D' or kmi.value == 'NOTHING':
        return False
    if kmi.key_modifier != 'NONE':
        return False
    if kmi.any:
        return True
    return all(getattr(kmi, name, wanted) in (wanted, -1) for name, wanted in CTRL_D.items())


def tool_keymap_names():
    """Keymap names of every tool the two paint toolbars can show, Blender's face mask select tools included."""
    from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
    names = set()
    mesh = cube().data
    saved = mesh.use_paint_mask
    try:
        for use_paint_mask in (False, True):
            mesh.use_paint_mask = use_paint_mask
            with override():
                for space_type, mode in (('VIEW_3D', 'PAINT_TEXTURE'), ('IMAGE_EDITOR', 'PAINT')):
                    toolbar = ToolSelectPanelHelper._tool_class_from_space_type(space_type)
                    for item in toolbar.tools_from_context(bpy.context, mode=mode):
                        # A tool is a named tuple; a group is a plain tuple of them.
                        group = (item,) if hasattr(item, "idname") else (item or ())
                        for tool in group:
                            keymap = getattr(tool, "keymap", None)
                            if keymap and isinstance(keymap[0], str):
                                names.update((keymap[0], keymap[0] + " (fallback)"))
    finally:
        mesh.use_paint_mask = saved
    return names


def ctrl_d_items(keyconfig, names):
    return [f"{km.name}: {kmi.idname} {kmi.value}" for km in keyconfig.keymaps if km.name in names
            for kmi in km.keymap_items if binds_ctrl_d(kmi)]


def check_default_keyconfigs():
    section("no keymap consulted in Texture Paint or image editor Paint mode binds Ctrl+D")
    keyconfigs = bpy.context.window_manager.keyconfigs
    default = keyconfigs.default
    fixed = sorted(set(TEXTURE_PAINT_KEYMAPS) | set(IMAGE_PAINT_KEYMAPS))
    missing = [name for name in fixed if default.keymaps.get(name) is None]
    check(not missing, f"the default keyconfig has every keymap the check reads (missing {missing})")
    tool_names = tool_keymap_names()
    present = sorted(name for name in tool_names if default.keymaps.get(name) is not None)
    check("Generic Tool: Annotate" in present,
          f"the toolbars give the tool keymaps, the annotate tool's included ({present})")
    names = set(fixed) | tool_names
    # The active keyconfig is the preset in use; a keymap it lacks comes
    # from the default one. They differ in Bforartists.
    configs = [keyconfigs.active] + ([default] if default.name != keyconfigs.active.name else [])
    for keyconfig in configs:
        prefs = keyconfig.preferences
        if prefs is not None and hasattr(prefs, "select_mouse"):
            saved = prefs.select_mouse
            try:
                for mouse in ('LEFT', 'RIGHT'):
                    # Setting it rebuilds the keyconfig from the preset data.
                    prefs.select_mouse = mouse
                    hits = ctrl_d_items(keyconfig, names)
                    check(not hits, f"{keyconfig.name} keyconfig with {mouse.lower()} click select: "
                                    f"no Ctrl+D item in {len(names)} keymaps ({hits})")
            finally:
                prefs.select_mouse = saved
        else:
            hits = ctrl_d_items(keyconfig, names)
            check(not hits, f"{keyconfig.name} keyconfig with its factory settings, as it has no select mouse "
                            f"setting: no Ctrl+D item in {len(names)} keymaps ({hits})")


def steps():
    if "Cube" not in bpy.data.objects:
        # Bforartists' factory startup has no cube.
        with override():
            bpy.ops.mesh.primitive_cube_add()
        bpy.context.view_layer.objects.active.name = "Cube"
    obj = cube()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    with override():
        bpy.ops.paint_system.setup_material('EXEC_DEFAULT')
        bpy.ops.paint_system.add_layer('EXEC_DEFAULT', layer_type='IMAGE', resolution='1024')
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    yield from wait_for(lambda: session.current().paint_mode)
    yield from run_sections()


def hovered_property(area, sidebar):
    with bpy.context.temp_override(window=window(), area=area, region=sidebar):
        return getattr(bpy.context, "property", None)


def find_opacity_field(area, sidebar):
    """Move the pointer down the sidebar until it hovers the layer's Opacity field; return that point or None.

    Python cannot read a button's rectangle, so the field is found the way
    a user finds it: Blender activates the button under the pointer, and
    `context.property` names the active button's property. 4.3.2 reports
    the path without the node, so only its end is compared.
    """
    for fraction in (0.8, 0.55):
        x = sidebar.x + int(sidebar.width * fraction)
        for y in range(sidebar.y + sidebar.height - 30, sidebar.y + 20, -9):
            simulate(window(), 'MOUSEMOVE', 'NOTHING', x, y)
            yield
            hovered = hovered_property(area, sidebar)
            if hovered and hovered[1].endswith("opacity"):
                return (x, y)
    return None


def opacity_drivers():
    animation = tree().animation_data
    return [d.data_path for d in animation.drivers if d.data_path.endswith("opacity")] if animation else []


def run_sections():
    check_default_keyconfigs()

    area = view3d()
    # Simulated input reaches no keymap until the window has handled a key
    # event, and the splash screen takes the first one.
    simulate(window(), 'ESC', 'PRESS', *centre(area))
    yield
    simulate(window(), 'ESC', 'RELEASE', *centre(area))
    yield 0.2

    section("Ctrl+D over the 3D view in Texture Paint clears the selection")
    brush = tools.default_brush_tool()
    check(active_tool() == brush, f"the brush is the active tool ({active_tool()})")
    for tool, label in ((brush, "the brush"), ("paint_system.select_lasso", "Lasso Selection")):
        if active_tool() != tool:
            with override():
                bpy.ops.wm.tool_set_by_id(name=tool)
            yield from wait_for(lambda: active_tool() == tool)
        yield from select_everything()
        before = op_count()
        calls.clear()
        yield from press_d("ctrl", centre(area))
        yield from wait_for(lambda: calls and op_count() == 0, timeout=2.0)
        check(active_tool() == tool and before == 1 and op_count() == 0
              and calls == [('DESELECT', {'FINISHED'}, None)],
              f"with {label} active, one press clears the selection through select_all "
              f"({before} ops, then {op_count()}; {calls})")

    section("Ctrl+D with nothing selected")
    tree().selection.clear()
    session.notify()
    yield from settle()
    calls.clear()
    yield from press_d("ctrl", centre(area))
    yield from wait_for(lambda: calls, timeout=2.0)
    check(op_count() == 0 and calls == [('DESELECT', {'CANCELLED'}, None)],
          f"select_all runs once, is cancelled and raises nothing ({op_count()} ops; {calls})")

    section("Alt+D does not clear the selection")
    yield from select_everything()
    calls.clear()
    yield from press_d("alt", centre(area))
    yield 0.3
    check(op_count() == 1 and calls == [], f"the selection is kept and select_all does not run ({op_count()} ops; {calls})")

    section("Ctrl+D over the sidebar's Opacity field adds a driver and keeps the selection")
    space = area.spaces.active
    space.show_region_ui = True
    area.tag_redraw()
    yield 0.3
    sidebar = next(r for r in area.regions if r.type == 'UI')
    sidebar.active_panel_category = "Paint System"
    area.tag_redraw()
    yield 0.5
    try:
        point = yield from find_opacity_field(area, sidebar)
        check(point is not None, f"the pointer finds the Opacity field in the sidebar ({point})")
        if point is not None:
            yield from select_everything()
            calls.clear()
            yield from press_d("ctrl", point)
            yield from wait_for(opacity_drivers, timeout=2.0)
            yield 0.2
            check(opacity_drivers(), f"User Interface's anim.driver_button_add adds a driver ({opacity_drivers()})")
            check(op_count() == 1 and calls == [],
                  f"the selection is kept and select_all does not run ({op_count()} ops; {calls})")
            # Close the driver popover.
            simulate(window(), 'ESC', 'PRESS', *point)
            yield
            simulate(window(), 'ESC', 'RELEASE', *point)
            yield 0.2
    finally:
        animation = tree().animation_data
        if animation:
            for fcurve in [d for d in animation.drivers if d.data_path.endswith("opacity")]:
                animation.drivers.remove(fcurve)
        space.show_region_ui = False
    simulate(window(), 'MOUSEMOVE', 'NOTHING', *centre(area))
    yield 0.3

    section("Ctrl+D over the image editor in Paint mode clears the selection")
    with override():
        bpy.ops.wm.tool_set_by_id(name=brush)
    yield from wait_for(lambda: active_tool() == brush)
    area.ui_type = 'IMAGE_EDITOR'
    yield from wait_for(lambda: area.type == 'IMAGE_EDITOR')
    space = area.spaces.active
    space.image = tree().nodes.active.image
    space.mode = 'PAINT'
    yield 0.5
    try:
        yield from select_everything()
        before = op_count()
        calls.clear()
        yield from press_d("ctrl", centre(area))
        yield from wait_for(lambda: calls and op_count() == 0, timeout=2.0)
        check(before == 1 and op_count() == 0 and calls == [('DESELECT', {'FINISHED'}, None)],
              f"one press clears the selection through select_all ({before} ops, then {op_count()}; {calls})")
    finally:
        area.ui_type = 'VIEW_3D'


def driver_for(gen):
    def driver():
        try:
            delay = next(gen)
        except StopIteration:
            end()
        except Exception:
            traceback.print_exc()
            check(False, "exception in the steps")
            end()
        return 0.02 if delay is None else delay
    return driver


def end():
    select_all.execute = _execute
    session.release()
    raster.release()
    finish("KEYMAPS UI TEST")


bpy.app.timers.register(driver_for(steps()), first_interval=1.0, persistent=True)
