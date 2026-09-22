"""Preview Channel: the active channel's values on the object (PS-061).

The operator gives the compiled node group a Preview output, links it to a
Material Output of its own in every material that uses the tree, and sets
a display that shows the values as they are stored. The checks look at the
materials, at baked pixels of the Preview output, at the scene's view
settings, and at a file saved while previewing.

Run:  blender -b --factory-startup --python tests/test_preview_channel.py
"""
import os
import sys
import tempfile

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, close, finish, fmt, guarded, import_from,  # noqa: E402
                     register_addon, section)

register_addon()
ops = bpy.ops.paint_system
core = import_from("compiler.core")
compile_tree = core.compile_tree
preview_module = import_from("props.preview")
find_material_group_node = import_from("context").find_material_group_node
link_tree_to_material = import_from("ops.node_tree_ops").link_tree_to_material
PREVIEW_TREE_KEY = import_from("ops.paint_ops").PREVIEW_TREE_KEY

SIZE = 8
# The checker behind a colour that is not opaque.
CHECKER = (0.2, 0.4)


def activate(obj):
    view_layer = bpy.context.view_layer
    view_layer.objects.active = obj
    obj.select_set(True)


def new_painted_object(name):
    """A mesh with a new material and Paint System tree of its own, made active."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.uv_layers.new(name="UVMap")
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    activate(obj)
    ops.setup_material()
    material = obj.active_material
    return obj, material, material.paint_system.tree


def material_outputs(material):
    return [node for node in material.node_tree.nodes if node.bl_idname == 'ShaderNodeOutputMaterial']


def preview_outputs(material, tree):
    return [node for node in material_outputs(material) if node.get(PREVIEW_TREE_KEY) == tree.uuid]


def active_output(material):
    return next((node for node in material_outputs(material) if node.is_active_output), None)


def toggle():
    return ops.preview_channel() == {'FINISHED'}


def set_display(view_transform, look, exposure, gamma):
    view_settings = bpy.context.scene.view_settings
    view_settings.view_transform = view_transform
    view_settings.look = look
    view_settings.exposure = exposure
    view_settings.gamma = gamma


def display():
    view_settings = bpy.context.scene.view_settings
    return (view_settings.view_transform, view_settings.look,
            round(view_settings.exposure, 4), round(view_settings.gamma, 4))


def preview_pixels(tree):
    return bake_group(tree.compiled, shader="Preview", size=SIZE)


def preview_source(tree):
    """The identifier of the compiled node whose colour the preview shows."""
    emission = next(node for node in tree.compiled.nodes
                    if node.get("ps_identifier") == f"{tree.get_output_node().uuid}:preview")
    links = emission.inputs['Color'].links
    return links[0].from_node.get("ps_identifier") if links else None


def over_checker(color, alpha):
    """The two colours a half-covered checker gives, one per checker square."""
    return [tuple(c * alpha + grey * (1.0 - alpha) for c in color) for grey in CHECKER]


def matches_checker(rgba, color, alpha):
    """Whether every pixel is *color* over one of the two squares, and both squares show."""
    wants = over_checker(color, alpha)
    hits = [next((i for i, want in enumerate(wants) if close(px[:3], want)), None) for px in rgba]
    return None not in hits and set(hits) == {0, 1}


obj, material, tree = new_painted_object("Preview Object")
original = active_output(material)


def test_preview():
    section("a preview shows the active channel through an output of its own")
    group = find_material_group_node(material, tree)
    rough = tree.create_channel("Rough", 'FLOAT')
    layer = tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Rough")
    layer.fill_color = (0.25, 0.25, 0.25, 1.0)
    activate(obj)
    check(tree.active_channel == rough and toggle() and tree.preview_channel, "the operator starts a preview")
    names = [s.name for s in group.outputs]
    check(names[-1] == "Preview", f"the compiled group has a Preview output last {names}")
    previews = preview_outputs(material, tree)
    check(len(previews) == 1 and active_output(material) == previews[0] and not original.is_active_output,
          "the material gets an output of its own for it, which is the active one")
    surface = previews[0].inputs['Surface'] if previews else None
    check(surface is not None and surface.is_linked and surface.links[0].from_socket.name == "Preview",
          "and the output shows the Preview output")
    rgba = preview_pixels(tree)
    check(all(close(px[:3], (0.25, 0.25, 0.25)) for px in rgba),
          f"a channel without alpha shows its values as light {fmt(rgba[0][:3])}")
    layer.fill_color = (0.8, 0.2, 0.1, 1.0)
    value = bake_group(tree.compiled, color="Rough", alpha="Rough", size=SIZE)[0]
    rgba = preview_pixels(tree)[0]
    check(close(rgba[:3], value[:3]) and close(rgba[:2], rgba[1:3]),
          f"a float channel shows the value the material gets, not the colour painted {fmt(rgba[:3])}, "
          f"want {fmt(value[:3])}")

    color = tree.channels["Color"]
    out = tree.get_output_node().uuid
    tree.active_channel_index = 0
    check(tree.active_channel == color and preview_source(tree) == f"{out}:preview:over",
          f"switching channels compiles the preview of the new one ({preview_source(tree)})")
    layer = tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Color")
    layer.fill_color = (1.0, 0.0, 0.0, 1.0)
    layer.opacity = 0.5
    check(matches_checker(preview_pixels(tree), (1.0, 0.0, 0.0), 0.5),
          "which shows over a checker where it is not opaque")
    output = next(s for s in tree.get_output_node().inputs if s.name == "Color")
    tree.links.remove(output.links[0])
    # A link edit compiles from a timer, which a headless script never runs.
    compile_tree(tree)
    check(matches_checker(preview_pixels(tree), (0.0, 0.0, 0.0), 0.0), "an empty channel shows only the checker")

    check(toggle() and not tree.preview_channel, "the operator ends the preview")
    check("Preview" not in [s.name for s in group.outputs] and not preview_outputs(material, tree),
          "the Preview output and the material's preview output are gone")
    check(active_output(material) == original, "the material's own output is active again")

    toggle()
    # Clearing the flag by hand leaves the output behind, as appending a
    # material saved while previewing does.
    tree.preview_channel = False
    check(toggle() and len(preview_outputs(material, tree)) == 1,
          "starting a preview replaces a preview output left behind")
    toggle()
    check(active_output(material) == original, "and ending it still activates the material's own output")


def test_display():
    section("a preview shows values as they are stored and gives the display back")
    activate(obj)
    tree.active_channel_index = list(tree.channels).index(tree.channels["Rough"])
    set_display('AgX', 'AgX - Punchy', 0.5, 1.2)
    saved = display()
    toggle()
    check(display() == ('Raw', 'None', 0.0, 1.0), f"a Non-Color channel shows under Raw, neutrally {display()}")
    tree.active_channel_index = 0
    check(display()[0] == 'Standard', f"a Color channel under Standard {display()}")
    rough = tree.channels["Rough"]
    space = rough.color_space
    rough.color_space = 'COLOR'
    rough.color_space = 'NONCOLOR'
    check(display()[0] == 'Standard', "the colour space of a channel not previewed changes nothing")
    rough.color_space = space
    color = tree.channels["Color"]
    color.color_space = 'NONCOLOR'
    check(display()[0] == 'Raw', "the view transform follows the colour space")
    color.color_space = 'COLOR'
    copy = tree.copy()
    core.mark_dirty()
    check(not copy.preview_channel, "a copy of the tree does not preview")
    # A tree that has the flag but no preview output, such as one whose
    # materials were deleted, does not hold the display either.
    copy.preview_channel = True
    toggle()
    check(display() == saved, f"ending the preview gives the display back {display()}, want {saved}")
    bpy.data.node_groups.remove(copy)

    toggle()
    view_settings = bpy.context.scene.view_settings
    view_settings.view_transform = 'False Color'
    view_settings.exposure = 0.3
    view_settings.gamma = 1.1
    tree.active_channel_index = 1
    check(display()[0] == 'False Color', "a view transform picked while previewing stays when the channel changes")
    toggle()
    check(display()[0] == 'False Color' and display()[2:] == (0.3, 1.1),
          f"and when the preview ends, as do a changed exposure and gamma {display()}")
    check(not bpy.context.scene.paint_system.preview_display.is_saved, "the saved display is cleared")
    set_display('AgX', 'None', 0.0, 1.0)


def test_materials():
    section("every material that uses the tree previews, and puts its own output back")
    set_display('AgX', 'AgX - Punchy', 0.5, 1.2)
    saved = display()
    second = bpy.data.materials.new("Preview Second")
    group = link_tree_to_material(second, tree)
    frame = second.node_tree.nodes.new('NodeFrame')
    frame.location = (3000.0, 2000.0)
    group.parent = frame
    first_output = active_output(second)
    other_output = second.node_tree.nodes.new('ShaderNodeOutputMaterial')
    other_output.is_active_output = True
    # A material that runs this tree and a tree of its own. Its own output
    # is not its first one, which Blender would activate by itself.
    other_obj, other_material, other_tree = new_painted_object("Preview Other")
    own_output = other_material.node_tree.nodes.new('ShaderNodeOutputMaterial')
    own_output.is_active_output = True
    link_tree_to_material(other_material, tree)
    other_material.paint_system.tree = other_tree
    # A material whose group node runs a node group without a Preview output.
    stale = bpy.data.materials.new("Preview Stale")
    link_tree_to_material(stale, tree).node_tree = bpy.data.node_groups.new("Preview Stale Group", 'ShaderNodeTree')

    activate(obj)
    check(toggle(), "the preview starts")
    previews = preview_outputs(second, tree)
    check(len(previews) == 1 and active_output(second) == previews[0],
          "a second material that uses the tree previews too")
    check(group.outputs["Preview"].is_linked, "through its own group node")
    check(all(output.parent == frame for output in previews), "whose frame the output joins")
    check(not preview_outputs(stale, tree) and active_output(stale) is not None,
          "a group node without a Preview output gets no preview output")
    activate(other_obj)
    toggle()
    check(active_output(other_material) == preview_outputs(other_material, other_tree)[0],
          "a material that runs two trees shows the one previewed last")
    other_scene = bpy.data.scenes.new("Preview Scene")
    other_scene.view_settings.view_transform = 'AgX'
    other_scene.view_settings.look = 'AgX - Punchy'
    other_scene.paint_system.preview_display.show(other_scene.view_settings, other_tree.active_channel)

    activate(obj)
    toggle()
    check(active_output(second) == other_output and not first_output.is_active_output,
          "ending it activates the output that was active, not the first")
    check(active_output(material) == original, "and the first material's own output")
    check(active_output(other_material) == preview_outputs(other_material, other_tree)[0],
          "a material that still previews the other tree keeps showing it")
    check(bpy.context.scene.paint_system.preview_display.is_saved,
          "the display stays while another tree previews")
    activate(other_obj)
    toggle()
    check(active_output(other_material) == own_output, "ending that one shows the material again")
    check(display() == saved and not other_tree.preview_channel,
          f"and gives the display back, as the last preview has ended {display()}, want {saved}")
    other_view = other_scene.view_settings
    check((other_view.view_transform, other_view.look) == ('AgX', 'AgX - Punchy'),
          "in every scene that showed one")
    bpy.data.scenes.remove(other_scene)
    bpy.data.materials.remove(stale)
    set_display('AgX', 'None', 0.0, 1.0)


def test_wrapped_tree():
    section("a group layer sees the same tree with or without its preview")
    parent = bpy.data.node_groups.new("Preview Parent", 'PaintSystemNodeTree')
    parent.initialize()
    wrapper = parent.nodes.new('PaintSystemGroupLayerNode')
    wrapper.node_tree = tree
    before = wrapper.hash_parts(None)
    own = core.artifact_fingerprint(tree)
    activate(obj)
    toggle()
    during = wrapper.hash_parts(None)
    tree.active_channel_index = 1 - tree.active_channel_index
    switched = wrapper.hash_parts(None)
    check(core.artifact_fingerprint(tree) != own, "the wrapped tree's own artifact has the preview")
    check(before == during == switched,
          "but the group layer's hash stays, so caches and filter layers above it stay valid")
    toggle()
    bpy.data.node_groups.remove(parent)


def test_missing_view():
    section("a colour management without the preview's view transform")
    real = preview_module.preview_view_transform
    preview_module.preview_view_transform = lambda channel: "Missing View"
    try:
        set_display('AgX', 'AgX - Punchy', 0.5, 1.2)
        saved = display()
        activate(obj)
        toggle()
        check(display() == ('AgX', 'None', 0.0, 1.0), f"keeps the view transform, and still shows neutrally {display()}")
        toggle()
        check(display() == saved, f"and gives the look back at the end {display()}")
    finally:
        preview_module.preview_view_transform = real
        set_display('AgX', 'None', 0.0, 1.0)


def test_poll():
    section("a preview needs the tree in the active material")
    nested = bpy.data.node_groups.new("Preview Nested", 'PaintSystemNodeTree')
    nested.initialize()
    empty = bpy.data.objects.new("Preview Empty", None)
    bpy.context.scene.collection.objects.link(empty)
    activate(empty)
    bpy.context.scene.paint_system.active_node_tree = nested
    check(not ops.preview_channel.poll(), "a tree no material runs cannot preview")
    nested.preview_channel = True
    check(ops.preview_channel.poll(), "but a preview that is on can always be ended")
    nested.preview_channel = False
    bpy.context.scene.paint_system.active_node_tree = None


def test_reserved_name():
    section("no channel can take the Preview output's name")
    extra = tree.create_channel("Preview", 'FLOAT')
    check(extra.name == "Preview 1", f"a new channel called Preview gets a number ({extra.name})")
    rough = tree.channels["Rough"]
    rough.name = "Preview"
    check(rough.name == "Preview 2", f"so does a renamed one ({rough.name})")
    rough.name = "Rough"
    tree.delete_channel(list(tree.channels).index(extra))


def test_saved_file():
    section("a file saved while previewing opens previewing")
    activate(obj)
    set_display('AgX', 'AgX - Punchy', 0.5, 1.2)
    toggle()
    path = os.path.join(tempfile.mkdtemp(prefix="ps_preview_"), "preview.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    check(bpy.ops.wm.open_mainfile(filepath=path) == {'FINISHED'}, "reopened")
    reopened_obj = bpy.data.objects["Preview Object"]
    reopened = reopened_obj.active_material
    reopened_tree = reopened.paint_system.tree
    check(reopened_tree.preview_channel and len(preview_outputs(reopened, reopened_tree)) == 1,
          "the tree still previews, through the material's preview output")
    check(display()[0] in ('Raw', 'Standard') and bpy.context.scene.paint_system.preview_display.is_saved,
          f"with the preview's display and the saved one {display()}")
    activate(reopened_obj)
    toggle()
    check(not preview_outputs(reopened, reopened_tree) and display() == ('AgX', 'AgX - Punchy', 0.5, 1.2),
          f"ending it there gives everything back {display()}")


guarded(test_preview)
guarded(test_display)
guarded(test_materials)
guarded(test_wrapped_tree)
guarded(test_missing_view)
guarded(test_poll)
guarded(test_reserved_name)
guarded(test_saved_file)

finish("PREVIEW CHANNEL TEST")
