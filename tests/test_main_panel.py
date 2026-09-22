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
icon_kwargs = import_from("common").icon_kwargs
find_material_group_node = import_from("context").find_material_group_node
# The Brush and Color sections ask the active tool for the paint mode,
# which needs a 3D view. They are not what this test looks at.
main_panels.draw_paint_sections = lambda layout, context: None


class RecordingLayout:
    """Stands in for a UILayout: records each call and returns another recorder.

    Collapsible sub-panels come back closed, so their bodies are skipped,
    unless *open_panels* is set. Attribute writes such as `row.scale_x`
    or an operator property are accepted and ignored.
    """

    def __init__(self, calls, open_panels=False):
        object.__setattr__(self, "calls", calls)
        object.__setattr__(self, "open_panels", open_panels)

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            child = RecordingLayout(self.calls, self.open_panels)
            if name != "panel":
                return child
            return child, (RecordingLayout(self.calls, True) if self.open_panels else None)
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


def test_preview_button():
    section("the paint row has a Preview Channel button")
    tree = cube.active_material.paint_system.tree

    def paint_row():
        calls = []
        main_panels._draw_paint_mode_row(RecordingLayout(calls), bpy.context, tree)
        return calls

    calls = paint_row()
    check(operators(calls) == ["paint_system.toggle_paint_mode", "paint_system.preview_channel", "wm.save_mainfile"],
          f"it sits between Toggle Paint Mode and Save ({operators(calls)})")
    button = next(kwargs for name, args, kwargs in calls
                  if name == "operator" and args[0] == "paint_system.preview_channel")
    check(button.get("text") == "" and button.get("depress") is False, "icon only, and raised")
    color_icon = icon_kwargs('color_socket')
    check(all(button.get(key) == value for key, value in color_icon.items()),
          f"with the active channel's socket icon ({button}, want {color_icon})")
    tree.preview_channel = True
    button = next(kwargs for name, args, kwargs in paint_row()
                  if name == "operator" and args[0] == "paint_system.preview_channel")
    check(button.get("depress") is True, "depressed while the tree previews")
    tree.preview_channel = False


def panels(calls):
    return {args[0]: kwargs for name, args, kwargs in calls if name == "panel"}


def test_sections_collapse():
    section("the channel list and the Selection section can be collapsed")
    bpy.context.view_layer.objects.active = cube
    tree = cube.active_material.paint_system.tree
    calls = draw_main_panel()
    check(panels(calls).get("paint_system_channels") == {"default_closed": False},
          f"the channel list is in a section that starts open (panels {panels(calls)})")
    channel = tree.active_channel
    check(channel is not None and channel.name in labels(calls),
          f"while it is closed its header names the active channel (labels {labels(calls)})")
    check(not any(name == "template_list" for name, _args, _kwargs in calls),
          "and the list itself is not drawn")

    calls = []
    main_panels._draw_selection_section(RecordingLayout(calls), bpy.context, tree)
    check(panels(calls).get("paint_system_selection") == {"default_closed": True},
          f"the Selection section starts closed (panels {panels(calls)})")


def drawn_props(calls):
    """(owner, property name, text) of each property drawn."""
    return [(args[0], args[1], kwargs.get("text")) for name, args, kwargs in calls if name == "prop"]


def test_channel_settings():
    section("the channel list and Channel Settings edit the channel and its material input")
    bpy.context.view_layer.objects.active = cube
    material = cube.active_material
    tree = material.paint_system.tree
    group = find_material_group_node(material, tree)
    color = tree.channels["Color"]
    tree.active_channel_index = 0

    def row(channel):
        calls = []
        main_panels.PAINTSYSTEM_UL_channels.draw_item(
            None, bpy.context, RecordingLayout(calls), tree, channel, 0, None, "", 0)
        return drawn_props(calls)

    def settings():
        calls = []
        main_panels._draw_channel_settings(RecordingLayout(calls, open_panels=True), bpy.context, tree)
        return drawn_props(calls), panels(calls)

    check((group.inputs["Color"], "default_value", "") in row(color),
          "a channel's row edits the material's unlinked input, the value its stack starts from")
    props, sections = settings()
    check(sections.get("paint_system_channel_settings") == {"default_closed": True},
          f"Channel Settings is a section that starts closed ({sections})")
    check([(owner, prop) for owner, prop, _text in props][:3]
          == [(color, "type"), (color, "color_space"), (color, "use_alpha")],
          f"it shows the type, colour space and Use Alpha first ({props})")
    check((group.inputs["Color Alpha"], "default_value", "Base Alpha") in props,
          "and the material's alpha input as Base Alpha")
    check(not any(prop == "use_range" for _owner, prop, _text in props), "no range for a colour channel")

    rgb = material.node_tree.nodes.new('ShaderNodeRGB')
    material.node_tree.links.new(rgb.outputs[0], group.inputs["Color"])
    material.node_tree.links.new(rgb.outputs[0], group.inputs["Color Alpha"])
    check(not any(prop == "default_value" for _owner, prop, _text in row(color)),
          "a linked input shows no value in the row")
    check(not any(prop == "default_value" for _owner, prop, _text in settings()[0]),
          "nor as Base Alpha")
    material.node_tree.nodes.remove(rgb)

    rough = tree.create_channel("Rough", 'FLOAT')
    props = settings()[0]
    check([prop for owner, prop, _text in props if owner == rough][-3:] == ["use_range", "range_min", "range_max"],
          f"a float channel adds Limit Range, Min and Max ({props})")
    check(not any(text == "Base Alpha" for _owner, _prop, text in props), "and no Base Alpha without alpha")
    check((group.inputs["Rough"], "default_value", "") in row(rough), "its row edits its material input too")
    # This channel's input has the name Rough's alpha input would have.
    decoy = tree.create_channel("Rough Alpha", 'FLOAT')
    tree.active_channel_index = list(tree.channels).index(rough)
    check("Rough Alpha" in group.inputs and not any(text == "Base Alpha" for _owner, _prop, text in settings()[0]),
          "not even when another channel's input has the alpha's name")
    tree.delete_channel(list(tree.channels).index(decoy))
    tree.delete_active_channel()

    normal = tree.create_channel("Normal", 'VECTOR')
    check(not any(prop == "default_value" for _owner, prop, _text in row(normal)),
          "a vector channel's row shows no value, since three fields do not fit")
    tree.delete_active_channel()

    nested = bpy.data.node_groups.new("Nested Only", 'PaintSystemNodeTree')
    nested.initialize()
    calls = []
    main_panels.PAINTSYSTEM_UL_channels.draw_item(
        None, bpy.context, RecordingLayout(calls), nested, nested.channels[0], 0, None, "", 0)
    check(not any(prop == "default_value" for _owner, prop, _text in drawn_props(calls)),
          "a tree that is not the material's own has no input to show")

    calls = []
    main_panels._draw_channels_section(RecordingLayout(calls, open_panels=True), bpy.context, tree)
    check("paint_system_channel_settings" in panels(calls), "the open channel list draws Channel Settings below it")


guarded(test_the_panel_follows_the_mesh)
guarded(test_preview_button)
guarded(test_sections_collapse)
guarded(test_channel_settings)

finish("MAIN PANEL TEST")
