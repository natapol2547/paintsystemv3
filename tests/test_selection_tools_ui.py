"""The 3D view selection tools driven by simulated input in a real window (PS-093).

Blender queues the events this test simulates and the window loop handles
them, so drags go through the tool keymaps, the modal operators and the
preview exactly as a user's would. `--enable-event-simulate` also makes
Blender ignore real input while the test runs.

The preview is read back from a draw handler added after the drag starts,
so it runs after the preview's own handler, and the dashes are drawn in
pure red and green (`RED_GREEN_ANTS`) so nothing else in the overlay
layer counts. The dash phase each draw used is recorded by wrapping
`overlay.ant_style`, which only the preview calls. The preview's timer
events are counted by wrapping `preview.Preview.tick`, so the redraw
check waits on events and draws rather than a fixed time.

Two groups of checks wait on the view raster: a drag over the cube builds
a mask, and a drag over empty background counts as no selection.

Run:  blender --factory-startup --enable-event-simulate --python tests/test_selection_tools_ui.py
"""
import math
import os
import sys
import traceback
import types

import bpy
import gpu
import numpy as np
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, drag, finish, import_from, register_addon, section, simulate, since, skip  # noqa: E402

if bpy.app.background:
    print("test_selection_tools_ui.py needs a window; run without -b")
    sys.exit(2)

register_addon()
tools = import_from("tools")
preview = import_from("tools.preview")
workspace_tools = import_from("tools.workspace_tools")
main_panels = import_from("panels.main_panels")
session = import_from("selection.session")
raster = import_from("selection.raster")
stencil = import_from("selection.stencil")
overlay = import_from("selection.overlay")

RED_GREEN_ANTS = {
    "selection_ant_color_a": (1.0, 0.0, 0.0),
    "selection_ant_color_b": (0.0, 1.0, 0.0),
    "selection_wash_opacity": 0.0,
}
"""Overlay settings for the pixel checks: dash colours nothing else in the 3D view draws, and no wash."""

TOOL_IDS = ("paint_system.select_lasso", "paint_system.select_box", "paint_system.select_ellipse")
"""The group in toolbar order."""

styles = []
captures = {}
wanted = set()

_ant_style = overlay.ant_style


def _recording_ant_style(context):
    style = _ant_style(context)
    styles.append(style)
    return style


overlay.ant_style = _recording_ant_style

preview_ticks = []
_preview_tick = preview.Preview.tick


def _counting_preview_tick(self):
    preview_ticks.append(self.kind)
    return _preview_tick(self)


preview.Preview.tick = _counting_preview_tick


def _capture():
    """Read the region back when a step asked for this area."""
    area = bpy.context.area
    if area is None or area.as_pointer() not in wanted:
        return
    region = bpy.context.region
    width, height = region.width, region.height
    buffer = gpu.types.Buffer('UBYTE', width * height * 4)
    gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, 'UBYTE', data=buffer)
    pixels = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 4).astype(np.int32)
    captures[area.as_pointer()] = (pixels, styles[-1] if styles else None)
    wanted.discard(area.as_pointer())


capture_handles = []


def add_capture_handler():
    """Read back after every handler added so far, the preview's included."""
    capture_handles.append(bpy.types.SpaceView3D.draw_handler_add(_capture, (), 'WINDOW', 'POST_PIXEL'))


def remove_capture_handlers():
    while capture_handles:
        bpy.types.SpaceView3D.draw_handler_remove(capture_handles.pop(), 'WINDOW')


def dashes(pixels):
    """-1 where no dash is, 0 for a red dash (`ant_a`) and 1 for a green one (`ant_b`)."""
    high = pixels[..., :3] > 225
    low = pixels[..., :3] < 30
    opaque = pixels[..., 3] > 250
    out = np.full(pixels.shape[:-1], -1)
    out[high[..., 0] & low[..., 1] & low[..., 2] & opaque] = 0
    out[low[..., 0] & high[..., 1] & low[..., 2] & opaque] = 1
    return out


class LayoutRecorder:
    """Stands in for a `UILayout` and keeps the text of every label drawn into it."""

    def __init__(self):
        self.labels = []

    def panel(self, *args, **kwargs):
        return self, self

    def label(self, text="", **kwargs):
        self.labels.append(text)

    def __getattr__(self, name):
        return lambda *args, **kwargs: self


class ButtonRecorder:
    """Stands in for a `UILayout` and keeps every operator button drawn into it, in order.

    Each entry is (kind, text, menu, properties), where kind is "operator"
    or "menu_hold" and properties receives what the caller sets, such as
    the tool id `wm.tool_set_by_id` takes.
    """

    def __init__(self):
        self.buttons = []

    def _button(self, kind, operator, text="", menu=None, **kwargs):
        properties = types.SimpleNamespace()
        self.buttons.append((kind, text, menu, properties))
        return properties

    def operator(self, operator, **kwargs):
        return self._button("operator", operator, **kwargs)

    def operator_menu_hold(self, operator, **kwargs):
        return self._button("menu_hold", operator, **kwargs)

    def tool_buttons(self):
        """(kind, text, menu, tool id) of each button that sets one of this add-on's tools."""
        return [(kind, text, menu, properties.name) for kind, text, menu, properties in self.buttons
                if getattr(properties, "name", "").startswith("paint_system.")]

    def __getattr__(self, name):
        return lambda *args, **kwargs: self


class ButtonContext:
    """`bpy.context` as a menu opened from a toolbar button sees it: `button_operator` names the button's tool."""

    def __init__(self, tool_id):
        self.button_operator = types.SimpleNamespace(name=tool_id)

    def __getattr__(self, name):
        return getattr(bpy.context, name)


def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    return max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)


def main_region(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def override():
    area = view3d()
    return bpy.context.temp_override(window=window(), area=area, region=main_region(area))


def cube():
    return bpy.data.objects["Cube"]


def tree():
    return cube().active_material.paint_system.tree


def layer():
    return tree().nodes.active


def ops():
    return [(op.kind, op.mode, op.space) for op in tree().selection.ops]


def active_tool(workspace=None):
    workspace = workspace or window().workspace
    ref = workspace.tools.from_space_view3d_mode('PAINT_TEXTURE', create=False)
    return ref.idname if ref is not None else None


def set_tool(idname):
    with override():
        bpy.ops.wm.tool_set_by_id(name=idname)


def tool_properties():
    ref = window().workspace.tools.from_space_view3d_mode('PAINT_TEXTURE', create=False)
    return ref.operator_properties("paint_system.select_box")


def last_operator():
    operators = bpy.context.window_manager.operators
    return operators[-1].bl_idname if len(operators) else None


def image_sum():
    image = layer().image
    buffer = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(buffer)
    return float(buffer.sum())


def to_window(point):
    region = main_region(view3d())
    return (region.x + point[0], region.y + point[1])


def wait_for(condition, timeout=5.0):
    """Yield to the window loop until *condition()* holds or *timeout* seconds pass."""
    waited = 0.0
    while not condition() and waited < timeout:
        yield 0.05
        waited += 0.05


def settle():
    """Yield until the session is idle and the views have drawn what it synced."""
    yield from wait_for(lambda: not bpy.app.timers.is_registered(session._tick))
    for area in window().screen.areas:
        area.tag_redraw()
    yield 0.3


def region_drag(start, end, **modifiers):
    """A drag between region pixels, and time for the window loop to finish handling it."""
    yield from drag(window(), to_window(start), to_window(end), **modifiers)
    yield 0.1


def region_event(type, value, point, **modifiers):
    simulate(window(), type, value, *to_window(point), **modifiers)


def press_and_move(start, end, steps=8):
    """Press the left button at *start* and move to *end*, leaving the button held."""
    region_event('MOUSEMOVE', 'NOTHING', start)
    yield
    region_event('LEFTMOUSE', 'PRESS', start)
    yield
    for i in range(1, steps + 1):
        t = i / steps
        region_event('MOUSEMOVE', 'NOTHING', (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t))
        yield
    yield 0.1


def capture(area, tag=True):
    """Yield until *area* has drawn once more; its pixels and the ant style of that draw, or None.

    Without *tag* the step waits for a draw something else asks for.
    """
    key = area.as_pointer()
    captures.pop(key, None)
    wanted.add(key)
    if tag:
        area.tag_redraw()
    yield from wait_for(lambda: key in captures)
    wanted.discard(key)
    return captures.get(key)


def clear_selection():
    tree().selection.clear()
    session.notify()
    yield from settle()


def steps():
    obj = cube()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    with override():
        bpy.ops.paint_system.setup_material('EXEC_DEFAULT')
        bpy.ops.paint_system.add_layer('EXEC_DEFAULT', layer_type='IMAGE', resolution='1024')
    space = view3d().spaces.active
    space.show_region_toolbar = True
    space.show_region_tool_header = True
    with override():
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    yield from wait_for(lambda: session.current().paint_mode)
    default_style = {name: overlay.DEFAULTS[name] for name in RED_GREEN_ANTS}
    overlay.DEFAULTS.update(RED_GREEN_ANTS)
    try:
        yield from run_sections()
    finally:
        overlay.DEFAULTS.update(default_style)


def run_sections():
    area = view3d()
    region = main_region(area)
    width, height = region.width, region.height
    centre = (width // 2, height // 2)
    # Empty background left of the cube, clear of the toolbar and the asset shelf.
    rv3d = area.spaces.active.region_3d
    cube_left = min(location_3d_to_region_2d(region, rv3d, cube().matrix_world @ Vector(corner)).x
                    for corner in cube().bound_box)
    empty_low = (int(cube_left) - 260, centre[1] - 40)
    empty_high = (empty_low[0] + 200, empty_low[1] + 80)
    toolbar_right = max((r.x + r.width - region.x for r in area.regions if r.type == 'TOOLS'), default=0)
    check(empty_low[0] > toolbar_right + 20, f"the view has room left of the cube ({empty_low}, toolbar to {toolbar_right})")
    # Simulated mouse input reaches no keymap until the window has handled a key event.
    region_event('ESC', 'PRESS', centre)
    yield
    region_event('ESC', 'RELEASE', centre)
    yield 0.2
    add_capture_handler()
    yield from settle()
    baseline = yield from capture(area)
    baseline_dashes = int((dashes(baseline[0]) >= 0).sum()) if baseline is not None else -1
    remove_capture_handlers()

    section("the tools are one toolbar group with their own keymaps")
    from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
    toolbar = ToolSelectPanelHelper._tool_class_from_space_type('VIEW_3D')
    with override():
        listing = []
        for item in toolbar.tools_from_context(bpy.context, mode='PAINT_TEXTURE'):
            # A tool is a named tuple; a group is a plain tuple of them.
            if hasattr(item, "idname"):
                listing.append(item.idname)
            elif isinstance(item, tuple):
                listing.append(tuple(getattr(tool, "idname", None) for tool in item if tool is not None))
            else:
                listing.append(item)
    group = listing.index(TOOL_IDS) if TOOL_IDS in listing else -1
    check(group >= 1 and listing[group - 1] is None, f"the three tools form one group behind a separator ({listing})")
    mask = next((i for i, item in enumerate(listing)
                 if item == 'builtin_brush.mask' or (isinstance(item, tuple) and 'builtin_brush.mask' in item)), None)
    if mask is not None:
        check(group == mask + 2, f"the group follows the Mask tool ({group}, Mask at {mask})")
    else:
        check(group == len(listing) - 1, f"without a builtin_brush.mask tool the group is last ({group} of {len(listing)})")

    # Before any tool of the group is active: once one has been drawn
    # active, Blender shows that one for the rest of the session.
    lasso_first = [("Lasso Selection", "paint_system.select_lasso"), ("Rectangle Selection", "paint_system.select_box"),
                   ("Ellipse Selection", "paint_system.select_ellipse")]
    from bl_ui.space_toolsystem_common import WM_MT_toolsystem_submenu
    recorder = ButtonRecorder()
    with override():
        brush_active = active_tool() == tools.default_brush_tool()
        toolbar.draw_cls(recorder, bpy.context, detect_layout=False)
    check(brush_active and recorder.tool_buttons()
          == [("menu_hold", "Lasso Selection", "WM_MT_toolsystem_submenu", "paint_system.select_lasso")],
          f"with the brush active the toolbar shows the group as one Lasso Selection button "
          f"(brush active: {brush_active}; {recorder.tool_buttons()})")
    popups = {}
    for _label, tool_id in lasso_first:
        menu = types.SimpleNamespace(layout=ButtonRecorder(),
                                     _tool_group_from_button=WM_MT_toolsystem_submenu._tool_group_from_button)
        with override():
            WM_MT_toolsystem_submenu.draw(menu, ButtonContext(tool_id))
        popups[tool_id] = [(text, name) for _kind, text, _menu, name in menu.layout.tool_buttons()]
    check(all(popup == lasso_first for popup in popups.values()),
          f"the group's popup lists Lasso, Rectangle and Ellipse Selection in that order ({popups})")

    keyconfigs = bpy.context.window_manager.keyconfigs
    for tool in workspace_tools.TOOLS:
        keymaps = [km for km in keyconfigs.addon.keymaps
                   if "Paint Texture" in km.name and km.name.endswith(", " + tool.bl_label)]
        items = [(item.idname, item.value, item.shift, item.ctrl, item.alt) for km in keymaps for item in km.keymap_items]
        check(len(keymaps) == 1 and len(items) == 5, f"{tool.bl_label} has one keymap of five items ({items})")

    # The toolbar popup (Shift+Space) builds its keymap here, and the
    # toolbar's tooltips name the same keys. A tool gets the key shortcut
    # of its keymap's first operator, if one has any, and otherwise the
    # next free number key in toolbar order.
    from bl_keymap_utils import keymap_from_toolbar
    with override():
        popup = keymap_from_toolbar.generate(bpy.context, 'VIEW_3D')
    numbers = ('ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE', 'ZERO')
    free = [(key, held) for held in ((), ("shift",), ("ctrl",), ("alt",)) for key in numbers]
    keys = {tool_id: [] for tool_id in TOOL_IDS}
    for item in popup.keymap_items:
        if item.idname == "wm.tool_set_by_id" and item.properties.name in keys and not item.type.startswith("NUMPAD"):
            held = tuple(name for name in ("shift", "ctrl", "alt", "oskey", "hyper") if getattr(item, name, 0) == 1)
            keys[item.properties.name].append((item.type, held))
    positions = [free.index(found[0]) if len(found) == 1 and found[0] in free else None for found in keys.values()]
    check(None not in positions and positions == list(range(positions[0], positions[0] + 3)),
          f"the toolbar popup gives Lasso, Rectangle and Ellipse Selection consecutive number keys ({keys})")

    def default_items():
        return sum(len(km.keymap_items) for km in keyconfigs.default.keymaps)

    registered = default_items()
    tools.unregister()
    unregistered = default_items()
    tools.register()
    check(registered == unregistered == default_items(),
          f"registering adds nothing to the default keyconfig ({unregistered} items, {registered} registered)")

    section("drags with modifiers pick the mode; a click deselects")
    set_tool("paint_system.select_box")
    yield from wait_for(lambda: active_tool() == "paint_system.select_box")
    check(active_tool() == "paint_system.select_box", f"the Rectangle Selection tool is active ({active_tool()})")
    low, high = (centre[0] - 80, centre[1] - 60), (centre[0] + 80, centre[1] + 60)
    # Shift held from the start picks the mode only: the box stays 160 by 120.
    corners = [(low[0] + 0.5, low[1] + 0.5), (high[0] + 0.5, high[1] + 0.5)]
    expected = []
    for modifiers, mode in (({}, 'REPLACE'), ({"shift": True}, 'ADD'), ({"ctrl": True}, 'SUBTRACT'),
                            ({"shift": True, "ctrl": True}, 'INTERSECT')):
        yield from region_drag(low, high, **modifiers)
        expected.append(('BOX', mode, 'VIEW'))
        points = tree().selection.ops[-1].get_points() if len(tree().selection.ops) else []
        check(ops() == expected and last_operator() == "PAINT_SYSTEM_OT_select_box" and points == corners,
              f"a drag with {sorted(modifiers) or 'no modifier'} appends a {mode} box with the dragged corners "
              f"({ops()}, {last_operator()}, {points})")
    yield from region_drag(centre, centre)
    check(ops() == [] and last_operator() == "PAINT_SYSTEM_OT_select_all",
          f"a click clears the selection through select_all ({ops()}, {last_operator()})")

    for cancel in ('ESC', 'RIGHTMOUSE'):
        yield from press_and_move(low, high)
        region_event(cancel, 'PRESS', high)
        yield
        region_event(cancel, 'RELEASE', high)
        yield
        region_event('LEFTMOUSE', 'RELEASE', high)
        yield 0.2
        check(ops() == [], f"{cancel} cancels a drag without an op ({ops()})")

    start = (centre[0] - 60, centre[1] - 30)
    for key, name in (('LEFT_SHIFT', "shift"), ('LEFT_ALT', "alt")):
        yield from press_and_move(start, (start[0] + 120, start[1] + 40))
        region_event(key, 'PRESS', (start[0] + 120, start[1] + 40), **{name: True})
        yield
        end = (start[0] + 120, start[1] + 41)
        region_event('MOUSEMOVE', 'NOTHING', end, **{name: True})
        yield
        region_event('LEFTMOUSE', 'RELEASE', end, **{name: True})
        yield
        region_event(key, 'RELEASE', end)
        yield 0.2
        points = tree().selection.ops[-1].get_points() if len(tree().selection.ops) else []
        origin = (start[0] + 0.5, start[1] + 0.5)
        if name == "shift":
            want = [origin, (origin[0] + 120, origin[1] + 120)]
            label = "Shift pressed during a drag makes a square"
        else:
            want = [(origin[0] - 120, origin[1] - 41), (origin[0] + 120, origin[1] + 41)]
            label = "Alt pressed during a drag draws from the centre"
        check(ops() == [('BOX', 'REPLACE', 'VIEW')] and points == want, f"{label} ({ops()}, {points}, want {want})")

    set_tool("paint_system.select_ellipse")
    yield from wait_for(lambda: active_tool() == "paint_system.select_ellipse")
    yield from region_drag(low, high, shift=True)
    check(ops() == [('BOX', 'REPLACE', 'VIEW'), ('ELLIPSE', 'ADD', 'VIEW')],
          f"a Shift drag with Ellipse Selection appends an ADD ellipse ({ops()})")
    set_tool("paint_system.select_lasso")
    yield from wait_for(lambda: active_tool() == "paint_system.select_lasso")
    path = [(centre[0] + 80 * math.sin(2 * math.pi * i / 40), centre[1] - 25 + 50 * math.cos(2 * math.pi * i / 40))
            for i in range(41)]
    region_event('MOUSEMOVE', 'NOTHING', path[0])
    yield
    region_event('LEFT_CTRL', 'PRESS', path[0], ctrl=True)
    yield
    region_event('LEFTMOUSE', 'PRESS', path[0], ctrl=True)
    yield
    for point in path[1:]:
        region_event('MOUSEMOVE', 'NOTHING', point, ctrl=True)
        yield
    region_event('LEFTMOUSE', 'RELEASE', path[-1], ctrl=True)
    yield
    region_event('LEFT_CTRL', 'RELEASE', path[-1])
    yield 0.2
    lasso = tree().selection.ops[-1].get_points() if len(tree().selection.ops) else []
    check(ops()[-1:] == [('LASSO', 'SUBTRACT', 'VIEW')] and len(ops()) == 3 and len(lasso) >= 20,
          f"a Ctrl drag with Lasso Selection appends a SUBTRACT lasso ({ops()}, {len(lasso)} points)")

    section("face selection masking keeps its faces")
    set_tool("paint_system.select_box")
    yield from wait_for(lambda: active_tool() == "paint_system.select_box")
    mesh = cube().data
    saved_faces = [polygon.select for polygon in mesh.polygons]
    mesh.use_paint_mask = True
    for polygon in mesh.polygons:
        polygon.select = polygon.index % 2 == 0
    faces = [polygon.select for polygon in mesh.polygons]
    yield 0.2
    try:
        for modifiers, mode in (({}, 'REPLACE'), ({"shift": True}, 'ADD'), ({"ctrl": True}, 'SUBTRACT'),
                                ({"shift": True, "ctrl": True}, 'INTERSECT')):
            yield from region_drag(low, high, **modifiers)
            check(ops()[-1:] == [('BOX', mode, 'VIEW')] and [p.select for p in mesh.polygons] == faces,
                  f"a {mode} drag leaves the face selection alone ({ops()[-1:]})")
        yield from region_drag(centre, centre)
        check(ops() == [] and [p.select for p in mesh.polygons] == faces,
              f"a click clears only the selection ({ops()})")
    finally:
        mesh.use_paint_mask = False
        for polygon, select in zip(mesh.polygons, saved_faces):
            polygon.select = select

    section("a drag over the cube builds a mask (needs the view raster)")
    yield from clear_selection()
    yield from region_drag((centre[0] - 60, centre[1] - 60), (centre[0] + 60, centre[1] + 60))
    yield from settle()
    state = session.current()
    check(ops() == [('BOX', 'REPLACE', 'VIEW')] and state.reason == '',
          f"the session builds the drag's selection ({ops()}, reason {state.reason!r}: {state.message})")
    mask = raster.peek_mask(tree().selection, state.size, state.tile)
    peak = float(mask.read().max()) if mask is not None else None
    check(peak is not None and peak > 0.5, f"the mask has selected texels (maximum {peak})")

    section("the preview marches the overlay's ants")
    yield from clear_selection()
    styles.clear()
    yield from press_and_move(empty_low, empty_high)
    add_capture_handler()
    try:
        check(len(styles) > 0, f"the preview draws during a drag ({len(styles)} draws)")
        frame = yield from capture(area)
        check(frame is not None and frame[1] is not None, "the preview was read back")
        if frame is not None and frame[1] is not None:
            classes = dashes(frame[0])
            row_y = empty_low[1]
            columns = np.arange(empty_low[0] + 6, empty_high[0] - 6)
            window_rows = classes[row_y - 4:row_y + 5, columns]
            thickness = (window_rows >= 0).sum(axis=0)
            want_width = preview.line_width(bpy.context)
            check(thickness.min() == thickness.max() == want_width,
                  f"the bottom edge is {want_width} px thick (min {thickness.min()}, max {thickness.max()})")
            row = window_rows[int(np.argmax((window_rows >= 0).sum(axis=1)))]
            ant_a, ant_b = frame[1]
            dash = ant_b[3]
            # The outline runs clockwise, so the bottom edge runs towards -x.
            along = -(columns + 0.5)
            formula = (np.mod(along - ant_a[3], 2.0 * dash) >= dash).astype(int)
            drawn = row >= 0
            agree = float((row[drawn] == formula[drawn]).mean()) if drawn.any() else 0.0
            check(drawn.mean() > 0.9 and agree > 0.95,
                  f"the dashes follow ANT_GLSL at the draw's phase ({agree:.3f} agree, {drawn.mean():.2f} covered)")
        # Nothing tags the region from here: the preview's timer has to.
        draws, ticked = len(styles), len(preview_ticks)
        yield from wait_for(lambda: len(preview_ticks) - ticked >= 2 and len(styles) - draws >= 2)
        check(len(preview_ticks) - ticked >= 2 and len(styles) - draws >= 2,
              f"the preview redraws with the mouse still ({len(styles) - draws} draws over "
              f"{len(preview_ticks) - ticked} timer events)")
        # The dash pattern repeats every 2 * DASH_PIXELS / DASH_SPEED seconds,
        # four timer intervals, so one frame drawn that much later looks the
        # same. Any of the next three untagged draws must differ.
        rows = slice(empty_low[1] - 4, empty_low[1] + 5)
        first = yield from capture(area, tag=False)
        phases = []
        moved = False
        for _ in range(3):
            later = yield from capture(area, tag=False)
            if first is None or later is None or first[1] is None or later[1] is None:
                break
            phases.append(round(later[1][0][3], 2))
            if later[1][0][3] != first[1][0][3] and not np.array_equal(dashes(first[0])[rows], dashes(later[0])[rows]):
                moved = True
                break
        if first is None or first[1] is None or not phases:
            check(False, "two frames were drawn without a tag")
        else:
            check(moved, f"the dashes move between untagged draws (phase {first[1][0][3]:.2f} then {phases})")
    finally:
        remove_capture_handlers()
    region_event('ESC', 'PRESS', empty_high)
    yield
    region_event('ESC', 'RELEASE', empty_high)
    yield
    region_event('LEFTMOUSE', 'RELEASE', empty_high)
    yield 0.2
    draws = len(styles)
    area.tag_redraw()
    yield 0.3
    check(ops() == [] and len(styles) == draws, f"a cancelled drag leaves no op and no preview ({ops()})")

    section("a drag over empty background is no selection (needs the view raster)")
    yield from clear_selection()
    yield from region_drag(empty_low, empty_high)
    yield from settle()
    state = session.current()
    check(ops() == [('BOX', 'REPLACE', 'VIEW')], f"the op stays on the tree ({ops()})")
    check(state.reason == '', f"an empty mask is not a problem (reason {state.reason!r}: {state.message})")
    check(state.empty and not state.active and session.label(state) == session.NOTHING_SELECTED,
          f"the session calls it nothing selected (empty={state.empty}, active={state.active})")
    check(not stencil.is_applied(bpy.context.scene), "the stencil is restored")
    add_capture_handler()
    try:
        frame = yield from capture(area)
    finally:
        remove_capture_handlers()
    count = int((dashes(frame[0]) >= 0).sum()) if frame is not None else -1
    check(frame is not None and abs(count - baseline_dashes) <= 20,
          f"no ants draw ({count} dash pixels, {baseline_dashes} without a selection)")
    recorder = LayoutRecorder()
    with override():
        main_panels._draw_selection_section(recorder, bpy.context, tree())
    check("Nothing selected" in recorder.labels, f"the panel says Nothing selected ({recorder.labels})")
    set_tool(tools.default_brush_tool())
    yield from wait_for(lambda: active_tool() == tools.default_brush_tool())
    painted = image_sum()
    yield from region_drag((centre[0] - 80, centre[1] - 20), (centre[0] + 80, centre[1] - 10))
    yield 0.3
    check(image_sum() != painted, "a stroke paints")
    yield from clear_selection()
    set_tool("paint_system.select_box")
    yield from wait_for(lambda: active_tool() == "paint_system.select_box")

    section("undo and Adjust Last Operation")
    if not since(5, 1):
        skip("a drag cannot be undone in Texture Paint before Blender 5.1 (accepted limitation)")
        skip("Adjust Last Operation needs that undo step, so it is not checked before Blender 5.1")
    else:
        yield from region_drag(centre, centre)
        yield from region_drag(low, high)
        yield from region_drag((low[0] + 20, low[1] + 20), (high[0] + 20, high[1] + 20), shift=True)
        check(ops() == [('BOX', 'REPLACE', 'VIEW'), ('BOX', 'ADD', 'VIEW')], f"two drags ({ops()})")
        operators = bpy.context.window_manager.operators
        operators[-1].properties.mode = 'SUBTRACT'
        with override():
            result = bpy.ops.ed.undo_redo('EXEC_DEFAULT', True)
        yield 0.2
        check(ops() == [('BOX', 'REPLACE', 'VIEW'), ('BOX', 'SUBTRACT', 'VIEW')],
              f"redoing the last drag as SUBTRACT replaces its op ({result}, {ops()})")
        # A region in the override segfaults undo on 4.2.
        with bpy.context.temp_override(window=window(), area=area):
            bpy.ops.ed.undo()
        yield 0.2
        check(ops() == [('BOX', 'REPLACE', 'VIEW')], f"undo removes the last drag's op ({ops()})")
        with bpy.context.temp_override(window=window(), area=area):
            bpy.ops.ed.redo()
        yield 0.2
        check(ops() == [('BOX', 'REPLACE', 'VIEW'), ('BOX', 'SUBTRACT', 'VIEW')], f"redo brings it back ({ops()})")
    yield from clear_selection()

    section("the tool header's settings reach the op")
    header_errors = []
    header_draws = []

    def header_hook(self, context):
        ref = context.workspace.tools.from_space_view3d_mode('PAINT_TEXTURE', create=False)
        if ref is None or ref.idname not in TOOL_IDS:
            return
        try:
            workspace_tools.draw_settings(context, self.layout.row(), ref)
            header_draws.append(ref.idname)
        except Exception:
            header_errors.append(traceback.format_exc())

    bpy.types.VIEW3D_HT_tool_header.append(header_hook)
    try:
        area.tag_redraw()
        yield from wait_for(lambda: header_draws or header_errors)
        check(header_draws and not header_errors, f"draw_settings draws in the tool header ({header_errors})")
    finally:
        bpy.types.VIEW3D_HT_tool_header.remove(header_hook)
    props = tool_properties()
    props.feather = 7.0
    props.through = True
    props.antialias = False

    def last_settings():
        op = tree().selection.ops[-1] if len(tree().selection.ops) else None
        return None if op is None else (round(op.feather, 3), op.through, op.antialias)

    try:
        yield from region_drag(low, high)
        check(last_settings() == (7.0, True, False), f"feather 7, Through and no anti-alias reach the op ({last_settings()})")
        props = tool_properties()
        props.feather = 2.0
        props.through = False
        yield from region_drag(low, high, shift=True)
        check(last_settings() == (2.0, False, False), f"a second header change reaches the op ({last_settings()})")
        if since(5, 1):
            operators = bpy.context.window_manager.operators
            operators[-1].properties.feather = 11.0
            with override():
                bpy.ops.ed.undo_redo('EXEC_DEFAULT', True)
            yield 0.2
            check(last_settings() == (11.0, False, False), f"Adjust Last Operation changes the op ({last_settings()})")
            check(round(tool_properties().feather, 3) == 2.0,
                  f"the header keeps its own feather ({tool_properties().feather})")
            yield from region_drag(low, high, shift=True)
            check(last_settings() == (2.0, False, False), f"the next drag uses the header's feather ({last_settings()})")
        else:
            skip("Adjust Last Operation needs that undo step, so it is not checked before Blender 5.1")
    finally:
        props = tool_properties()
        props.feather = 0.0
        props.through = False
        props.antialias = True
    yield from clear_selection()

    section("unregistering hands Texture Paint back to the brush")
    brush = tools.default_brush_tool()

    def paints(point):
        """Whether a stroke through *point*, in region pixels, changes the layer's image."""
        before = image_sum()
        yield from region_drag((point[0] - 80, point[1]), (point[0] + 80, point[1] + 10))
        yield 0.3
        return image_sum() != before

    tools.unregister()
    try:
        check(active_tool() == brush, f"in Texture Paint the tool is the brush again ({active_tool()})")
        painted = yield from paints((centre[0], centre[1] - 30))
        check(painted, "a drag paints")
    finally:
        tools.register()

    set_tool("paint_system.select_box")
    yield from wait_for(lambda: active_tool() == "paint_system.select_box")
    with override():
        bpy.ops.object.mode_set(mode='OBJECT')
    tools.unregister()
    try:
        with override():
            bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
        yield from wait_for(lambda: session.current().paint_mode)
        check(active_tool() == brush, f"unregistered in Object Mode, the brush is back in Texture Paint ({active_tool()})")
        painted = yield from paints(centre)
        check(painted, "a drag paints")
    finally:
        tools.register()

    # The tool is set in the Texture Paint workspace, which is then hidden
    # behind a workspace in Object Mode; showing it again enters Texture Paint.
    home = window().workspace
    texture_paint = bpy.data.workspaces["Texture Paint"]
    with override():
        bpy.ops.object.mode_set(mode='OBJECT')
    window().workspace = texture_paint
    yield from wait_for(lambda: window().workspace == texture_paint and bpy.context.mode == 'PAINT_TEXTURE')
    yield 0.3
    set_tool("paint_system.select_box")
    yield from wait_for(lambda: active_tool() == "paint_system.select_box")
    window().workspace = home
    yield from wait_for(lambda: window().workspace == home)
    yield 0.3
    if bpy.context.mode != 'OBJECT':
        with override():
            bpy.ops.object.mode_set(mode='OBJECT')
    tools.unregister()
    try:
        check(active_tool(texture_paint) == brush,
              f"a workspace no window shows gets the brush ({active_tool(texture_paint)})")
        window().workspace = texture_paint
        yield from wait_for(lambda: window().workspace == texture_paint and bpy.context.mode == 'PAINT_TEXTURE')
        yield 0.3
        check(bpy.context.mode == 'PAINT_TEXTURE' and active_tool() == brush,
              f"shown again, it is in Texture Paint with the brush ({bpy.context.mode}, {active_tool()})")
        region = main_region(view3d())
        painted = yield from paints((region.width // 2, region.height // 2))
        check(painted, "a drag paints")
    finally:
        tools.register()

    # A second main window shows the Texture Paint workspace with the tool,
    # and the add-on is disabled from a Preferences window opened from the
    # first one, which shows another workspace.
    window().workspace = home
    yield from wait_for(lambda: window().workspace == home)
    yield 0.3
    with override():
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
        bpy.ops.wm.window_new_main()
    yield 1.0
    windows = bpy.context.window_manager.windows
    second = windows[len(windows) - 1]
    second.workspace = texture_paint
    yield from wait_for(lambda: second.workspace == texture_paint)
    yield 0.5
    second_area = max((a for a in second.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    second_region = main_region(second_area)
    with bpy.context.temp_override(window=second, area=second_area, region=second_region):
        bpy.ops.wm.tool_set_by_id(name="paint_system.select_box")
    yield from wait_for(lambda: active_tool(texture_paint) == "paint_system.select_box")
    with override():
        bpy.ops.screen.userpref_show('INVOKE_DEFAULT')
    yield from wait_for(lambda: any(w.screen.is_temporary for w in bpy.context.window_manager.windows))
    yield 0.5
    preferences = next(w for w in bpy.context.window_manager.windows if w.screen.is_temporary)
    with bpy.context.temp_override(window=preferences):
        tools.unregister()
    try:
        with bpy.context.temp_override(window=preferences):
            bpy.ops.wm.window_close()
        # Unregistering has already set the brush and the close is immediate,
        # so neither can be waited on; let the window loop handle the close
        # before input is simulated.
        yield 0.5
        check(active_tool(texture_paint) == brush
              and not any(w.screen.is_temporary for w in bpy.context.window_manager.windows),
              f"disabled from Preferences, the second window gets the brush ({active_tool(texture_paint)})")
        middle = (second_region.x + second_region.width // 2, second_region.y + second_region.height // 2)
        # Simulated mouse input reaches no keymap until the window has handled a key event.
        simulate(second, 'ESC', 'PRESS', *middle)
        yield
        simulate(second, 'ESC', 'RELEASE', *middle)
        yield 0.2
        # Below the earlier strokes, whose pixels already have the brush colour.
        before = image_sum()
        yield from drag(second, (middle[0] - 80, middle[1] + 50), (middle[0] + 80, middle[1] + 60))
        yield from wait_for(lambda: image_sum() != before)
        check(image_sum() != before, "a drag in the second window paints")
        with bpy.context.temp_override(window=second):
            bpy.ops.wm.window_close()
        yield 0.5
    finally:
        tools.register()


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
    remove_capture_handlers()
    overlay.ant_style = _ant_style
    preview.Preview.tick = _preview_tick
    overlay.unregister()
    preview.release()
    session.release()
    raster.release()
    finish("SELECTION TOOLS UI TEST")


bpy.app.timers.register(driver_for(steps()), first_interval=1.0, persistent=True)
