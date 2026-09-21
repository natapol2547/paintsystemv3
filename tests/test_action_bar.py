"""Where the floating action bar sits and when it shows (PS-052).

The layout is two pure functions over region rectangles, so they are
checked here against made-up regions rather than a real window: every
combination of tool bar, sidebar, headers and asset shelf would take a
windowed run each. `tests/test_action_bar_ui.py` covers the gizmo group
itself, which needs a window.
"""
import os
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, guarded, import_from, register_addon, section  # noqa: E402

register_addon()
action_bar = import_from("panels.action_bar")
session = import_from("selection.session")

WIDTH, HEIGHT = 1000, 600


def region(kind, x, y, width, height, alignment='NONE'):
    return SimpleNamespace(type=kind, x=x, y=y, width=width, height=height,
                           alignment=alignment)


def area(*others):
    """A 3D view whose WINDOW region fills it, plus *others* drawn over it."""
    window = region('WINDOW', 0, 0, WIDTH, HEIGHT)
    return SimpleNamespace(regions=[window, *others]), window


def cube():
    return bpy.data.objects["Cube"]


def tree():
    return cube().active_material.paint_system.tree


def run(op, **props):
    result = op('EXEC_DEFAULT', True, **props)
    bpy.context.view_layer.update()
    return result


def setup():
    obj = cube()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    run(bpy.ops.paint_system.setup_material)
    run(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024')


setup()


def test_the_bar_avoids_the_other_regions():
    section("the bar stays inside the part of the view nothing covers")
    plain, window = area()
    check(action_bar.visible_rect(plain, window) == (0, 0, WIDTH, HEIGHT),
          "a view with no other region open is visible edge to edge")

    covered, window = area(
        region('TOOLS', 0, 0, 60, HEIGHT, 'LEFT'),
        region('UI', WIDTH - 200, 0, 200, HEIGHT, 'RIGHT'),
        region('TOOL_HEADER', 0, HEIGHT - 26, WIDTH, 26, 'TOP'),
        region('ASSET_SHELF', 0, 0, WIDTH, 120, 'BOTTOM'),
    )
    check(action_bar.visible_rect(covered, window) == (60, 120, WIDTH - 200, HEIGHT - 26),
          "the tool bar, the sidebar, a header and the asset shelf each trim their own side")

    hidden, window = area(
        region('TOOLS', 0, 0, 1, HEIGHT, 'LEFT'),
        region('UI', WIDTH - 1, 0, 1, HEIGHT, 'RIGHT'),
    )
    check(action_bar.visible_rect(hidden, window) == (0, 0, WIDTH, HEIGHT),
          "a closed region reports a width of 1 and takes nothing")

    flipped, window = area(region('TOOLS', WIDTH - 60, 0, 60, HEIGHT, 'RIGHT'))
    check(action_bar.visible_rect(flipped, window) == (0, 0, WIDTH - 60, HEIGHT),
          "a tool bar flipped to the right trims the right side")

    beside, window = area(region('UI', WIDTH, 0, 200, HEIGHT, 'RIGHT'))
    check(action_bar.visible_rect(beside, window) == (0, 0, WIDTH, HEIGHT),
          "with Region Overlap off the sidebar sits beside the view and trims nothing")

    floating, window = area(region('HUD', 20, HEIGHT - 120, 200, 100, 'NONE'))
    check(action_bar.visible_rect(floating, window) == (0, 0, WIDTH, HEIGHT),
          "Adjust Last Operation floats over the view and is not avoided")


def test_the_row_is_centred_at_the_bottom():
    section("the row of buttons")
    size, gap, pad = action_bar.BUTTON_SIZE, action_bar.BUTTON_GAP, action_bar.PADDING
    layout = action_bar.bar_layout((0, 0, WIDTH, HEIGHT), 5, 1.0)
    x0, y0, x1, y1 = layout["rect"]
    width = 5 * size + 4 * gap + 2 * pad
    check(x1 - x0 == width and y1 - y0 == size + 2 * pad,
          f"the backdrop holds five buttons and the padding ({x1 - x0} x {y1 - y0})")
    check(abs((x0 + x1) / 2 - WIDTH / 2) <= 0.5, "centred across the view")
    check(y0 == round(action_bar.MARGIN), f"a margin above the bottom edge ({y0})")
    centers = layout["centers"]
    check(len(centers) == 5 and all(abs(y - (y0 + pad + size / 2)) < 1e-6 for _x, y in centers),
          "every button sits on one line")
    check(all(abs(b - a - (size + gap)) < 1e-6
              for a, b in zip([x for x, _y in centers], [x for x, _y in centers][1:])),
          "one button pitch apart")

    inset = action_bar.bar_layout((60, 120, WIDTH - 200, HEIGHT - 26), 5, 1.0)
    check(abs(sum(inset["rect"][0::2]) / 2 - (60 + WIDTH - 200) / 2) <= 0.5,
          "centred across what the other regions leave, not across the whole view")
    check(inset["rect"][1] == round(120 + action_bar.MARGIN),
          "above the asset shelf, not behind it")

    scaled = action_bar.bar_layout((0, 0, WIDTH, HEIGHT), 5, 2.0)
    check(scaled["rect"][2] - scaled["rect"][0] == 2 * width
          and scaled["rect"][3] - scaled["rect"][1] == 2 * (size + 2 * pad),
          "a UI scale of 2 doubles the bar")

    narrow = action_bar.bar_layout((0, 0, 40, HEIGHT), 5, 1.0)
    check(narrow["rect"][0] == round(action_bar.MARGIN),
          "a view narrower than the bar keeps the first buttons reachable")


def test_the_bar_needs_a_live_selection():
    section("when the bar shows")
    t = tree()
    t.selection.clear()
    session.sync(force=True)
    check(not action_bar.show_bar(bpy.context), "not in object mode")

    run(bpy.ops.object.mode_set, mode='TEXTURE_PAINT')
    session.sync(force=True)
    check(bpy.context.mode == 'PAINT_TEXTURE' and not action_bar.show_bar(bpy.context),
          "not in texture paint mode without a selection")

    t.selection.add_op('ALL')
    session.sync(force=True)
    check(action_bar.show_bar(bpy.context), "with a live selection on the active tree")

    # A mask that is unavailable or covers nothing still counts: the bar
    # holds Deselect and Invert Selection, which work either way, and the
    # pixel actions say for themselves why they will not run.
    state = session.current()
    check(state.selected, f"even where the mask has a problem ({state.reason or 'none'})")

    hidden = SimpleNamespace(show_action_bar=False)
    real_preferences = action_bar.preferences
    action_bar.preferences = lambda context: hidden
    try:
        check(not action_bar.show_bar(bpy.context), "the preference turns it off")
    finally:
        action_bar.preferences = real_preferences

    run(bpy.ops.object.mode_set, mode='OBJECT')
    t.selection.clear()
    session.sync(force=True)


def test_the_menu_draws():
    section("the More menu")
    menu = bpy.types.PAINTSYSTEM_MT_action_bar
    check(menu.bl_label == "Selection Actions", "the menu is registered")
    # With no add-on preferences entry, which is how the tests import the
    # package, the menu still draws its actions and leaves out the switch.
    drawn = []
    layout = SimpleNamespace(
        operator=lambda *args, **kwargs: drawn.append(args[0]) or SimpleNamespace(action=''),
        separator=lambda **kwargs: None,
        prop=lambda *args, **kwargs: drawn.append(args[1]),
        menu=lambda idname, **kwargs: drawn.append(idname),
    )
    menu.draw(SimpleNamespace(layout=layout), bpy.context)
    # Blur and Sharpen live here rather than on the bar because both open
    # a dialog, and a gizmo that opens one is not the one-click thing the
    # bar is for.
    check(drawn == ["paint_system.select_all",
                    "PAINTSYSTEM_MT_invert_channels",
                    "paint_system.blur_pixels",
                    "paint_system.sharpen_pixels"], f"it drew {drawn}")


for test in (test_the_bar_avoids_the_other_regions,
             test_the_row_is_centred_at_the_bottom,
             test_the_bar_needs_a_live_selection,
             test_the_menu_draws):
    guarded(test)

session.release()

finish("ACTION BAR TEST")
