"""The live selection in a real window: timers, subscriptions and undo (PS-091).

The headless test calls `session.sync()` itself because background Blender
runs no timers and no message bus. This one lets the window loop run them
and checks that the scheduled tick keeps `session.current()` in step after
a mode change, a scene switch, the selection operator, a layer switch and
undo, and that the Selection section draws.

Run:  blender --factory-startup --python tests/test_selection_session_ui.py
SCREENSHOT=<path.png> also saves a screenshot with the sidebar open.
"""
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, import_from, register_addon, section, since, skip  # noqa: E402

if bpy.app.background:
    print("test_selection_session_ui.py needs a window; run without -b")
    sys.exit(2)

register_addon()
session = import_from("selection.session")
raster = import_from("selection.raster")
main_panels = import_from("panels.main_panels")

draw_errors = []
section_draws = []
_draw_section = main_panels._draw_selection_section


def _wrapped_draw_section(layout, context, tree):
    try:
        _draw_section(layout, context, tree)
        section_draws.append(session.current().reason)
    except Exception:
        draw_errors.append(traceback.format_exc())
        raise


main_panels._draw_selection_section = _wrapped_draw_section


def view3d():
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return window, area, region


def override():
    window, area, region = view3d()
    return bpy.context.temp_override(window=window, area=area, region=region)


def tree():
    return bpy.data.objects["Cube"].active_material.paint_system.tree


def op(operator, **props):
    with override():
        return operator('EXEC_DEFAULT', True, **props)


def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def wait_for(condition, timeout=5.0):
    """Yield to the window loop until *condition()* holds or *timeout* seconds pass.

    Timer ticks can lag well behind a fixed delay while the window compiles
    shaders, so every check that depends on a tick waits for it this way.
    """
    waited = 0.0
    while not condition() and waited < timeout:
        yield 0.05
        waited += 0.05


def steps():
    obj = bpy.data.objects["Cube"]
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    op(bpy.ops.paint_system.setup_material)
    op(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024')
    big = tree().nodes.active.name
    op(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024')
    small = tree().nodes.active
    small.image = bpy.data.images.new("PS Session UI Small", 256, 256)
    small = small.name
    yield 0.3

    section("the tick follows mode changes through the message bus")
    op(bpy.ops.object.mode_set, mode='TEXTURE_PAINT')
    yield from wait_for(lambda: session.current().paint_mode)
    check(session.current().paint_mode, "entering texture paint mode syncs with paint_mode set")
    check(not session.current().selected, "no selection yet")

    section("the tick follows a scene switch through the message bus")
    window = bpy.context.window_manager.windows[0]
    home = window.scene
    other = bpy.data.scenes.new("PS Session UI Other")
    window.scene = other
    yield from wait_for(lambda: session.current().scene_uid == other.session_uid)
    check(session.current().scene_uid == other.session_uid, "switching scenes syncs a state for the new scene")
    window.scene = home
    yield from wait_for(lambda: session.current().scene_uid == home.session_uid)
    check(session.current().scene_uid == home.session_uid and session.current().paint_mode,
          "switching back syncs the first scene again")
    bpy.data.scenes.remove(other)

    section("the selection operator schedules a sync")
    tree().active_layer_index = tree().nodes.find(small)
    yield 0.2
    check(op(bpy.ops.paint_system.select_all, action='SELECT') == {'FINISHED'}, "select all runs in paint mode")
    yield from wait_for(lambda: session.current().selected)
    state = session.current()
    check(state.selected and state.size == (256, 256), f"the tick synced the selection at the layer size ({state.size})")
    check(state.active and raster.peek_mask(tree().selection, (256, 256)) is not None,
          f"and built the mask in the window's GPU context ({state.reason})")

    section("switching layers moves the selection to the new layer")
    tree().active_layer_index = tree().nodes.find(big)
    yield from wait_for(lambda: session.current().size == (1024, 1024))
    check(session.current().size == (1024, 1024) and session.current().active,
          f"the tick rebuilt at the other layer's size ({session.current().size})")

    section("a script edit without notify is caught up by the next notify")
    before_digest = session.current().digest
    tree().selection.add_op('BOX', points=[(0.2, 0.2), (0.6, 0.6)], feather=4.0)
    yield 0.5
    check(session.current().digest == before_digest, "nothing in Blender reports the edit on its own")
    session.notify()
    yield from wait_for(lambda: session.current().digest != before_digest)
    check(session.current().digest != before_digest and session.current().active, "notify syncs it")

    section("undo in texture paint mode")
    if since(5, 1):
        check(op(bpy.ops.paint_system.select_all, action='DESELECT') == {'FINISHED'}, "deselect runs")
        yield from wait_for(lambda: not session.current().selected)
        op(bpy.ops.paint_system.select_all, action='SELECT')
        yield from wait_for(lambda: session.current().selected)
        with override():
            bpy.ops.ed.undo()
        yield from wait_for(lambda: not session.current().selected)
        check(tree().selection.is_empty, "undo restores the ops in paint mode")
        check(not session.current().selected, "and undo_post resynced the session")
        with override():
            bpy.ops.ed.redo()
        yield from wait_for(lambda: session.current().active)
        check(not tree().selection.is_empty and session.current().active, "redo restores and resyncs")
    else:
        # Before 5.1 undo in texture paint mode steps through image undo
        # only, so the operator pushes no step there (ops.selection_ops).
        skip("before 5.1, undo in texture paint mode does not restore the ops")

    section("a target problem shows in the section")
    if tree().selection.is_empty:
        op(bpy.ops.paint_system.select_all, action='SELECT')
    tree().nodes[big].image = bpy.data.images.new("PS Session UI Tiled", 64, 64, tiled=True)
    session.notify()
    yield from wait_for(lambda: session.current().reason == session.UDIM)
    check(session.current().reason == session.UDIM and session.current().message,
          f"a UDIM layer is reported ({session.current().reason})")

    section("the Selection section draws")
    window, area, _ = view3d()
    area.spaces.active.show_region_ui = True
    redraw()
    yield 0.3
    sidebar = next(r for r in area.regions if r.type == 'UI')
    sidebar.active_panel_category = "Paint System"
    redraw()
    yield from wait_for(lambda: section_draws)
    check(section_draws and not draw_errors,
          f"the section drew without an exception ({len(section_draws)} draws, {draw_errors[:1]})")
    check(session.UDIM in section_draws, "including with the problem line")
    screenshot = os.environ.get("SCREENSHOT")
    if screenshot:
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
            finish("SELECTION SESSION UI TEST")
        except Exception:
            traceback.print_exc()
            check(False, "exception in the steps")
            finish("SELECTION SESSION UI TEST")
        return 0.1 if delay is None else delay
    return driver


bpy.app.timers.register(driver_for(steps()), first_interval=1.0, persistent=True)
