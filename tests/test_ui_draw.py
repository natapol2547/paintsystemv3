"""Draw every Paint System panel in a real window and fail on exceptions.

Blender swallows exceptions raised inside ``draw()`` and only prints them,
so a panel that breaks after a Blender API change never fails a headless
test. This test runs in a windowed session (under Xvfb on CI), wraps the
draw callbacks of every class the addon registers, sets up a painted
material, forces a full redraw and reports any exception.

Run:  blender --factory-startup --gpu-backend opengl --python tests/test_ui_draw.py
"""
import functools
import inspect
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (  # noqa: E402
    PACKAGE, check, section, register_addon, finish,
)

if bpy.app.background:
    print("test_ui_draw.py needs a window; run without -b")
    sys.exit(2)

register_addon()

DRAW_METHODS = ("draw", "draw_header", "draw_header_preset", "draw_item", "filter_items", "poll")
errors = []
calls = {}


def _record(cls_name, name, func, args):
    calls[cls_name] = calls.get(cls_name, 0) + 1
    try:
        return func(*args)
    except Exception:
        errors.append((cls_name, name, traceback.format_exc()))
        raise


def _wrap(cls, name):
    """Replace ``cls.<name>`` with a recording wrapper of identical signature.

    Blender validates the argument count of draw/poll functions when a
    class is registered, so a ``*args`` wrapper would be rejected; the
    wrapper is generated with the original parameter list instead.
    """
    original = cls.__dict__.get(name)
    if original is None:
        return
    is_classmethod = isinstance(original, classmethod)
    func = original.__func__ if is_classmethod else original
    params = list(inspect.signature(func).parameters.values())
    names = [p.name for p in params]
    defaults = {f"_d_{p.name}": p.default for p in params if p.default is not p.empty}
    decl = ", ".join(f"{p.name}=_d_{p.name}" if p.default is not p.empty else p.name for p in params)
    src = f"def wrapped({decl}):\n    return _record(_cls_name, _name, _func, ({', '.join(names)},))\n"
    ns = {"_record": _record, "_cls_name": cls.__name__, "_name": name, "_func": func, **defaults}
    exec(src, ns)  # noqa: S102 - test-only code generation
    wrapped = functools.update_wrapper(ns["wrapped"], func)
    setattr(cls, name, classmethod(wrapped) if is_classmethod else wrapped)


def addon_ui_classes():
    found = []
    for base in (bpy.types.Panel, bpy.types.Menu, bpy.types.UIList, bpy.types.Header,
                 bpy.types.AddonPreferences):
        stack = list(base.__subclasses__())
        while stack:
            cls = stack.pop()
            stack.extend(cls.__subclasses__())
            if cls.__module__.split(".")[0] == PACKAGE and cls not in found:
                found.append(cls)
    return found


def popover_panels(classes):
    """Panels that are not in a sidebar and must be opened explicitly."""
    return [c for c in classes
            if issubclass(c, bpy.types.Panel)
            and getattr(c, "bl_region_type", "UI") not in {"UI", "TOOLS", "TOOL_PROPS"}
            and getattr(c, "bl_space_type", "") in {"VIEW_3D", "IMAGE_EDITOR", "NODE_EDITOR"}]


def find_area(window, space_type):
    for area in window.screen.areas:
        if area.type == space_type:
            return area
    return None


def window_region(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def show_sidebar(area):
    space = area.spaces.active
    if hasattr(space, "show_region_ui"):
        space.show_region_ui = True


def retab_panels(area, classes):
    """Move our sidebar panels onto whichever tab is active in ``area``.

    ``Region.active_panel_category`` is read-only, so instead of switching
    the tab we re-register the addon's sidebar panels for this space under
    the tab that is already showing. That keeps the real ``poll``, header
    and ``draw`` code on the normal sidebar path. The region only reports
    its category after it has been laid out once, so this runs one timer
    tick after ``show_sidebar``.
    """
    region = next((r for r in area.regions if r.type == 'UI'), None)
    active = getattr(region, "active_panel_category", "") if region else ""
    if not active or active == 'UNSUPPORTED':
        print(f"  (no active tab reported for {area.type}; panels stay on their own tab)")
        return
    for cls in classes:
        if (issubclass(cls, bpy.types.Panel)
                and getattr(cls, "bl_space_type", "") == area.type
                and getattr(cls, "bl_region_type", "") == 'UI'
                and getattr(cls, "bl_category", "") != active
                and getattr(cls, "is_registered", False)):
            bpy.utils.unregister_class(cls)
            cls.bl_category = active
            bpy.utils.register_class(cls)


def tag_redraw(window):
    """Ask for a redraw; the window loop draws between timer ticks.

    ``wm.redraw_timer`` would force it synchronously but crashes when
    called from an app timer after area types change, so the test is a
    small state machine driven by ``bpy.app.timers`` instead.
    """
    for area in window.screen.areas:
        area.tag_redraw()


# PS_UI_PARTS limits the setup for bisecting crashes, e.g. "paint,node".
PARTS = set(os.environ.get("PS_UI_PARTS", "paint,node,image").split(","))


def setup_scene(window):
    """Cube with a Paint System material, in texture paint mode."""
    view3d = find_area(window, 'VIEW_3D')
    region = window_region(view3d)
    cube = bpy.data.objects['Cube']
    bpy.context.view_layer.objects.active = cube
    cube.select_set(True)
    with bpy.context.temp_override(window=window, area=view3d, region=region, object=cube):
        bpy.ops.paint_system.setup_material()
        bpy.ops.paint_system.add_layer(layer_type='IMAGE')
        bpy.ops.paint_system.add_layer(layer_type='SOLID_COLOR')
        # A layer clipped to the image layer, then a disabled folder with a
        # locked layer inside draw the clipped, nested, greyed and locked rows.
        tree = cube.active_material.paint_system.tree
        tree.nodes.active.is_clip = True
        bpy.ops.paint_system.add_layer(layer_type='FOLDER')
        folder = tree.nodes.active
        bpy.ops.paint_system.add_layer(layer_type='SOLID_COLOR')
        tree.nodes.active.lock_layer = True
        folder.enabled = False
        if "paint" in PARTS:
            bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    return cube


def setup_areas(window, tree):
    """3D view + node editor + image editor with sidebars open."""
    screen = window.screen
    view3d = find_area(window, 'VIEW_3D')
    show_sidebar(view3d)
    areas = [view3d]

    others = [a for a in screen.areas if a != view3d and a.type not in {'PROPERTIES', 'OUTLINER'}]
    if others and "node" in PARTS:
        node_area = others[0]
        node_area.type = 'NODE_EDITOR'
        space = node_area.spaces.active
        space.tree_type = 'PaintSystemNodeTree'
        space.node_tree = tree
        show_sidebar(node_area)
        areas.append(node_area)
    if len(others) > 1 and "image" in PARTS:
        img_area = others[1]
        img_area.type = 'IMAGE_EDITOR'
        show_sidebar(img_area)
        areas.append(img_area)
    return areas


def open_popover(window, view3d, cls):
    region = window_region(view3d)
    try:
        with bpy.context.temp_override(window=window, area=view3d, region=region):
            bpy.ops.wm.call_panel(name=cls.bl_idname, keep_open=False)
    except Exception:
        errors.append((cls.__name__, "call_panel", traceback.format_exc()))


def open_menu(window, view3d, cls):
    region = window_region(view3d)
    try:
        with bpy.context.temp_override(window=window, area=view3d, region=region):
            bpy.ops.wm.call_menu(name=cls.bl_idname)
    except Exception:
        errors.append((cls.__name__, "call_menu", traceback.format_exc()))


def open_move_popup(window, view3d, tree):
    """Invoke a layer move with several options on offer, which opens a menu of them."""
    region = window_region(view3d)
    tree.nodes.active = next(item.node for item in tree.stack()
                             if item.level == 0 and not item.node.is_folder)
    try:
        with bpy.context.temp_override(window=window, area=view3d, region=region):
            result = bpy.ops.paint_system.move_layer_up('INVOKE_DEFAULT')
        check(result == {'INTERFACE'}, f"a move with several options opens a menu {result}")
    except Exception:
        errors.append(("PAINTSYSTEM_OT_move_layer_up", "invoke", traceback.format_exc()))


class Steps:
    """Timer-driven test: each step returns the delay before the next."""

    def __init__(self):
        self.window = bpy.context.window_manager.windows[0]
        self.classes = addon_ui_classes()
        self.view3d = None
        self.tree = None
        self.areas = []
        self.popups = []
        self.state = 0

    def step_setup(self):
        section("wrap draw callbacks")
        if not os.environ.get("PS_UI_NOWRAP"):
            for cls in self.classes:
                for name in DRAW_METHODS:
                    _wrap(cls, name)
        check(len(self.classes) > 0, f"{len(self.classes)} UI classes wrapped: "
              + ", ".join(sorted(c.__name__ for c in self.classes)))
        section("scene")
        cube = setup_scene(self.window)
        tree = cube.active_material.paint_system.tree
        check(tree is not None, "material has a Paint System tree")
        self.tree = tree
        self.areas = setup_areas(self.window, tree)
        self.view3d = self.areas[0]
        self.popups = ([(open_popover, cls) for cls in popover_panels(self.classes)]
                       + [(open_menu, cls) for cls in self.classes if issubclass(cls, bpy.types.Menu)])
        tag_redraw(self.window)
        return 0.5

    def step_retab(self):
        section("sidebar tabs")
        for area in self.areas:
            retab_panels(area, self.classes)
        tag_redraw(self.window)
        return 1.0

    def step_popup(self):
        opener, cls = self.popups.pop()
        opener(self.window, self.view3d, cls)
        tag_redraw(self.window)
        return 0.5

    def step_move_popup(self):
        section("move popup")
        open_move_popup(self.window, self.view3d, self.tree)
        tag_redraw(self.window)
        return 0.5

    def step_results(self):
        section("results")
        drawn = sorted(name for name, n in calls.items() if n)
        check("PAINTSYSTEM_PT_main_3dview" in calls, "main 3D view panel was drawn")
        check("PAINTSYSTEM_PT_layers_3dview" in calls, "layers panel was drawn")
        check("PAINTSYSTEM_UL_layers" in calls, "layer list rows were drawn")
        check("PAINTSYSTEM_MT_add_layer" in calls, "add layer menu was drawn")
        check(len(drawn) > 0, f"callbacks reached: {', '.join(drawn)}")
        seen = set()
        for cls_name, method, tb in errors:
            key = (cls_name, method)
            if key in seen:
                continue
            seen.add(key)
            print(tb)
            check(False, f"{cls_name}.{method} raised")
        check(not errors, "no draw exceptions")
        return None

    def __call__(self):
        try:
            if self.state == 0:
                self.state = 1
                return self.step_setup()
            if self.state == 1:
                self.state = 2
                return self.step_retab()
            if self.popups:
                return self.step_popup()
            if self.state == 2:
                self.state = 3
                return self.step_move_popup()
            self.step_results()
        except Exception:
            traceback.print_exc()
            check(False, "exception in test driver")
        finish("UI DRAW TEST")
        return None


# Run after the window loop starts so the screen exists and can draw.
bpy.app.timers.register(Steps(), first_interval=0.5)
