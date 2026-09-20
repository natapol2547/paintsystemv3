"""The action bar as a gizmo group in a real 3D view (PS-052).

The layout maths are checked headless in `tests/test_action_bar.py`.
What needs a window is the group itself: that Blender instances it while
a selection is live, that `draw_prepare` puts the buttons where the
layout says, that a button whose operator cannot run is hidden and the
row closes up, and that turning gizmos off in the view takes the bar with
it. That last one matters because a gizmo that is not drawn is still
picked, so a bar left behind would swallow clicks over empty space.

Run:  blender --factory-startup --python tests/test_action_bar_ui.py
"""
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, import_from, register_addon, section  # noqa: E402

if bpy.app.background:
    print("test_action_bar_ui.py needs a window; run without -b")
    sys.exit(2)

register_addon()
action_bar = import_from("panels.action_bar")
session = import_from("selection.session")
raster = import_from("selection.raster")

prepares = []
draw_errors = []
_draw_prepare = action_bar.PAINTSYSTEM_GGT_action_bar.draw_prepare


def _recording_draw_prepare(self, context):
    try:
        _draw_prepare(self, context)
        prepares.append({
            "total": len(self.buttons),
            "shown": [gizmo for _operator, gizmo in self.buttons if not gizmo.hide],
            "centers": [tuple(gizmo.matrix_basis.translation)[:2]
                        for _operator, gizmo in self.buttons if not gizmo.hide],
            "rect": tuple(self.backdrop.rect),
            "visible": action_bar.visible_rect(context.area, context.region),
        })
    except Exception:
        draw_errors.append(traceback.format_exc())
        raise


action_bar.PAINTSYSTEM_GGT_action_bar.draw_prepare = _recording_draw_prepare


def view3d():
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'),
               key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return window, area, region


def override():
    window, area, region = view3d()
    return bpy.context.temp_override(window=window, area=area, region=region)


def op(operator, **props):
    with override():
        return operator('EXEC_DEFAULT', True, **props)


def tree():
    return bpy.data.objects["Cube"].active_material.paint_system.tree


def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def polls():
    with override():
        return action_bar.PAINTSYSTEM_GGT_action_bar.poll(bpy.context)


def wait_for(condition, timeout=5.0):
    waited = 0.0
    while not condition() and waited < timeout:
        yield 0.05
        waited += 0.05


def fresh_prepare():
    """Yield until the bar has drawn again, and give back that layout."""
    prepares.clear()
    redraw()
    yield from wait_for(lambda: prepares)
    return


def steps():
    obj = bpy.data.objects["Cube"]
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    op(bpy.ops.paint_system.setup_material)
    op(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024')
    paint = tree().nodes.active.name
    op(bpy.ops.paint_system.add_layer, layer_type='SOLID_COLOR')
    solid = tree().nodes.active.name
    tree().active_layer_index = tree().nodes.find(paint)
    op(bpy.ops.object.mode_set, mode='TEXTURE_PAINT')
    yield from wait_for(lambda: session.current().paint_mode)

    section("the bar waits for a selection")
    check(not polls(), "no bar in texture paint mode with nothing selected")
    redraw()
    yield 0.4
    check(not prepares, "and nothing of it drew")

    section("a live selection brings it up")
    op(bpy.ops.paint_system.select_all, action='SELECT')
    yield from wait_for(lambda: session.current().selected)
    check(polls(), "the group polls true")
    yield from fresh_prepare()
    check(prepares and not draw_errors,
          f"draw_prepare ran without an exception ({len(prepares)} draws, {draw_errors[:1]})")
    if not prepares:
        return

    last = prepares[-1]
    expected = action_bar.bar_layout(last["visible"], len(last["shown"]),
                                     bpy.context.preferences.system.ui_scale)
    check(last["total"] and len(last["shown"]) == last["total"],
          f"every button shows on an image layer ({len(last['shown'])} of {last['total']})")
    check(all(abs(a[0] - b[0]) < 0.5 and abs(a[1] - b[1]) < 0.5
              for a, b in zip(last["centers"], expected["centers"])),
          f"the buttons sit where the layout puts them ({last['centers'][:1]})")
    check(last["rect"] == expected["rect"], f"and the backdrop around them ({last['rect']})")
    _window, area, region = view3d()
    x0, y0, x1, y1 = last["rect"]
    check(0 <= x0 and x1 <= region.width and 0 <= y0 and y1 <= region.height,
          f"inside the region ({region.width} x {region.height})")

    section("the sidebar moves the bar")
    space = area.spaces.active
    space.show_region_ui = True
    yield 0.3
    yield from fresh_prepare()
    with_sidebar = prepares[-1]["rect"]
    space.show_region_ui = False
    yield 0.3
    yield from fresh_prepare()
    without_sidebar = prepares[-1]["rect"]
    check(with_sidebar[0] < without_sidebar[0],
          f"opening the sidebar shifts the bar left ({with_sidebar[0]} then {without_sidebar[0]})")

    section("a button that cannot run is not shown")
    tree().active_layer_index = tree().nodes.find(solid)
    yield 0.3
    yield from fresh_prepare()
    solid_layout = prepares[-1]
    check(len(solid_layout["shown"]) == solid_layout["total"] - 3,
          f"the three pixel actions go, leaving {len(solid_layout['shown'])} buttons")
    narrower = action_bar.bar_layout(solid_layout["visible"], len(solid_layout["shown"]),
                                     bpy.context.preferences.system.ui_scale)
    check(solid_layout["rect"] == narrower["rect"], "and the row closes up around the rest")
    tree().active_layer_index = tree().nodes.find(paint)
    yield 0.3

    section("the view's gizmo switch takes the bar with it")
    space.show_gizmo = False
    check(not polls(), "no bar when the view hides its gizmos, so none of it can be clicked")
    space.show_gizmo = True
    check(polls(), "and it comes back with them")

    section("the preference turns it off")
    hidden = type("Preferences", (), {"show_action_bar": False})()
    real_preferences = action_bar.preferences
    action_bar.preferences = lambda context: hidden
    try:
        check(not polls(), "the switch in the Gizmos popover hides the bar")
    finally:
        action_bar.preferences = real_preferences

    screenshot = os.environ.get("SCREENSHOT")
    if screenshot:
        yield from fresh_prepare()
        window, _area, _region = view3d()
        with bpy.context.temp_override(window=window):
            bpy.ops.screen.screenshot(filepath=screenshot)
        yield 0.2
        check(os.path.exists(screenshot), f"saved {screenshot}")

    session.release()
    raster.release()


def driver_for(gen):
    def driver():
        try:
            delay = next(gen)
        except StopIteration:
            finish("ACTION BAR UI TEST")
        except Exception:
            traceback.print_exc()
            check(False, "exception in the steps")
            finish("ACTION BAR UI TEST")
        return 0.1 if delay is None else delay
    return driver


bpy.app.timers.register(driver_for(steps()), first_interval=1.0, persistent=True)
