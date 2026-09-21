"""What the 3D view's Paint System panel draws for the active object.

The panel's draw runs against a layout that records every call, so the
check needs no window: it looks at which operators and properties the
panel asked for, not at pixels. `tests/test_ui_draw.py` draws the real
panel in a window.

Run:  blender -b --factory-startup --python tests/test_main_panel.py
"""
import os
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, guarded, import_from, register_addon, section  # noqa: E402

register_addon()
main_panels = import_from("panels.main_panels")
# The Brush and Color sections ask the active tool for the paint mode,
# which needs a 3D view. They are not what this test looks at.
main_panels.draw_paint_sections = lambda layout, context: None


class RecordingLayout:
    """Stands in for a UILayout: records each call and returns another recorder.

    Collapsible sub-panels come back closed, so their bodies are skipped.
    Attribute writes such as `row.scale_x` or an operator property are
    accepted and ignored.
    """

    def __init__(self, calls):
        object.__setattr__(self, "calls", calls)

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            child = RecordingLayout(self.calls)
            return (child, None) if name == "panel" else child
        return call

    def __setattr__(self, name, value):
        pass


def draw_main_panel():
    calls = []
    panel = main_panels.PAINTSYSTEM_PT_main_3dview
    panel.draw(SimpleNamespace(layout=RecordingLayout(calls)), bpy.context)
    return calls


def operators(calls):
    return [args[0] for name, args, _kwargs in calls if name == "operator"]


def labels(calls):
    return [kwargs.get("text") for name, _args, kwargs in calls if name == "label"]


def setup():
    cube = bpy.data.objects["Cube"]
    view_layer = bpy.context.view_layer
    view_layer.objects.active = cube
    cube.select_set(True)
    bpy.ops.paint_system.setup_material()
    empty = bpy.data.objects.new("Parented Empty", None)
    bpy.context.scene.collection.objects.link(empty)
    empty.parent = cube
    return cube, empty


cube, empty = setup()


def test_the_panel_follows_the_mesh():
    section("the material row shows the mesh Paint System works on")
    view_layer = bpy.context.view_layer
    material = cube.active_material

    view_layer.objects.active = cube
    calls = draw_main_panel()
    check(material is not None and material.paint_system.tree is not None,
          "the cube has a Paint System material")
    check("paint_system.setup_material" not in operators(calls) and material.name in labels(calls),
          "with the cube active the panel shows its material")

    view_layer.objects.active = empty
    calls = draw_main_panel()
    check(bpy.context.object == empty and "paint_system.toggle_paint_mode" in operators(calls),
          "with an empty parented to the cube active the paint row comes from the cube")
    check("paint_system.setup_material" not in operators(calls),
          f"and no Setup button for the empty (drew {operators(calls)})")
    check(material.name in labels(calls),
          f"but the cube's material and tree field (labels {labels(calls)})")


guarded(test_the_panel_follows_the_mesh)

finish("MAIN PANEL TEST")
