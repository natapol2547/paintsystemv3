"""The playable demo from start to finish (M0 slice 7).

Walks the demo bar on the factory cube with the operators and properties
the panels use: Setup Paint System, add image layers, a solid layer and a
folder, reorder them, set blend mode, opacity and clipping, enter paint
mode, paint on the canvas it selects, then save, reopen, undo and redo,
and save and reopen once more. After every step the compiled group must
match the tree, and baked texels of it must match the stack composited by
hand with the PS-001 coverage rule.

Painting writes pixels into the canvas image: a brush stroke needs a 3D
view region, which background mode does not have. The brush and colour
sections are drawn by test_ui_draw.py; here texture paint must have the
brush they show.

File load and undo free every ID, so nothing is kept across them; layers
are looked up again by name.
"""
import os
import sys
import tempfile

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, close, finish, fmt, guarded, import_from,  # noqa: E402
                     multiply_blend, over, pixel_at, register_addon, section)

register_addon()
core = import_from("compiler.core")
find_material_group_node = import_from("context").find_material_group_node

TRANSPARENT = (0.0, 0.0, 0.0, 0.0)
PAPER = (1.0, 0.8, 0.6, 1.0)
RED = (1.0, 0.0, 0.0, 1.0)
GREEN = (0.0, 1.0, 0.0, 1.0)
BLUE = (0.0, 0.0, 1.0, 1.0)
LEFT, RIGHT = 0.25, 0.75
ORDER = ["Highlights", "Ink", "Shade", "Sketch", "Paper"]


def cube():
    return bpy.data.objects["Cube"]


def active_tree():
    mat = cube().active_material
    return mat.paint_system.tree if mat else None


def node(name):
    return active_tree().nodes[name]


def run(op, **props):
    """Call an operator like the UI does, with its undo push, and evaluate
    the depsgraph as the window loop does after it."""
    result = op('EXEC_DEFAULT', True, **props)
    bpy.context.view_layer.update()
    return result == {'FINISHED'}


def edit(message, name, **values):
    """Change layer properties from the panel: each change is an undo step."""
    layer = node(name)
    for prop, value in values.items():
        setattr(layer, prop, value)
    bpy.ops.ed.undo_push(message=message)


def select(name):
    """Click a row in the layer list."""
    tree = active_tree()
    tree.active_layer_index = tree.nodes.find(name)
    bpy.ops.ed.undo_push(message=f"Select {name}")


def add(layer_type, name, **props):
    before = {n.name for n in active_tree().nodes} if active_tree() else set()
    check(run(bpy.ops.paint_system.add_layer, layer_type=layer_type, **props), f"add {name}")
    added = active_tree().nodes.active
    check(added.name not in before, f"the new {name} layer is active")
    added.name = name
    bpy.ops.ed.undo_push(message=f"Rename {name}")


def image_paint():
    return bpy.context.scene.tool_settings.image_paint


def paint_half(image, color, left):
    width, height = image.size
    pixels = np.zeros((height, width, 4), dtype=np.float32)
    if left:
        pixels[:, :width // 2] = color
    else:
        pixels[:, width // 2:] = color
    image.pixels.foreach_set(pixels.ravel())
    image.update()


def layer_names():
    return [item.node.name for item in active_tree().stack()]


def expected(clip_shade=True):
    """Texels at LEFT and RIGHT of the finished demo stack, top first:
    Highlights (RED on the right, opacity 0.5), folder Ink (MULTIPLY,
    opacity 0.8) holding Shade (GREEN at opacity 0.5, clipped) over Sketch
    (BLUE on the left), and Paper at the bottom."""
    texels = []
    for sketch, highlights in ((BLUE, TRANSPARENT), (TRANSPARENT, RED)):
        if clip_shade:
            content = over(TRANSPARENT, over(sketch, GREEN, 0.5, clip=True))
        else:
            content = over(over(TRANSPARENT, sketch), GREEN, 0.5)
        ink = over(PAPER, content, 0.8, blend=multiply_blend)
        texels.append(over(ink, highlights, 0.5))
    return tuple(texels)


def check_composite(label, want):
    tree = active_tree()
    check(core.artifact_fingerprint(tree) == core.build_ir(tree).fingerprint(),
          f"{label}: the compiled group matches the tree")
    group = find_material_group_node(cube().active_material, tree)
    check(group is not None and group.node_tree == tree.compiled, f"{label}: the material uses the compiled group")
    rgba = bake_group(tree.compiled)
    for u, side, texel in ((LEFT, "left", want[0]), (RIGHT, "right", want[1])):
        got = pixel_at(rgba, u, 0.5)
        check(close(got, texel), f"{label}: {side} {fmt(got)} expected {fmt(texel)}")


def save_and_reopen(label, path=None):
    if path is None:
        check(bpy.ops.wm.save_mainfile() == {'FINISHED'}, f"{label}: saved")
        path = bpy.data.filepath
    else:
        check(bpy.ops.wm.save_as_mainfile(filepath=path) == {'FINISHED'}, f"{label}: saved")
    check(bpy.ops.wm.open_mainfile(filepath=path) == {'FINISHED'}, f"{label}: reopened")
    check(active_tree() is not None, f"{label}: the cube's material still has its tree")
    # Background sessions reopen with undo off until the first push.
    bpy.ops.ed.undo_push(message="Reopened")


def test_demo():
    section("setup")
    bpy.context.view_layer.objects.active = cube()
    bpy.ops.ed.undo_push(message="Demo start")
    check(run(bpy.ops.paint_system.setup_material), "Setup Paint System")
    base_color = next(n for n in cube().active_material.node_tree.nodes
                      if n.bl_idname == 'ShaderNodeBsdfPrincipled').inputs['Base Color']
    check(base_color.is_linked and base_color.links[0].from_node.node_tree == active_tree().compiled,
          "the compiled group feeds Base Color")

    section("layers and folder")
    add('SOLID_COLOR', "Paper")
    edit("Paper color", "Paper", fill_color=PAPER)
    add('IMAGE', "Highlights", resolution='1024')
    add('FOLDER', "Ink")
    add('IMAGE', "Sketch", resolution='1024')
    add('SOLID_COLOR', "Shade")
    edit("Shade color", "Shade", fill_color=GREEN)
    check(layer_names() == ["Ink", "Shade", "Sketch", "Highlights", "Paper"],
          f"a layer is added above the active one, and into an active folder {layer_names()}")
    parents = {item.node.name: item.parent.node.name if item.parent else None for item in active_tree().stack()}
    check(parents == {"Ink": None, "Shade": "Ink", "Sketch": "Ink", "Highlights": None, "Paper": None},
          f"Sketch and Shade are inside the folder {parents}")

    section("reorder")
    select("Highlights")
    check(run(bpy.ops.paint_system.move_layer_up, action='SKIP'), "move Highlights up past the folder")
    check(layer_names() == ORDER, f"Highlights is on top {layer_names()}")

    section("blend mode, opacity and clipping")
    edit("Ink blend", "Ink", blend_mode='MULTIPLY', opacity=0.8)
    edit("Shade opacity and clip", "Shade", opacity=0.5, is_clip=True)
    edit("Highlights opacity", "Highlights", opacity=0.5)
    check_composite("unpainted", (PAPER, PAPER))

    section("paint mode")
    select("Sketch")
    check(run(bpy.ops.paint_system.toggle_paint_mode), "Toggle Paint Mode")
    check(cube().mode == 'TEXTURE_PAINT', "the cube is in texture paint mode")
    check(image_paint().canvas == node("Sketch").image, "the canvas is the active layer's image")
    check(image_paint().brush is not None, "texture paint has a brush for the Brush and Color sections")
    paint_half(image_paint().canvas, BLUE, left=True)
    select("Highlights")
    check(image_paint().canvas == node("Highlights").image, "selecting another layer moves the canvas")
    paint_half(image_paint().canvas, RED, left=False)
    check(run(bpy.ops.paint_system.toggle_paint_mode), "Toggle Paint Mode again")
    check(cube().mode == 'OBJECT', "the cube is back in object mode")
    check_composite("painted", expected())

    section("save and reopen")
    path = os.path.join(tempfile.mkdtemp(prefix="ps_demo_"), "demo.blend")
    save_and_reopen("first save", path)
    check(layer_names() == ORDER, f"the stack order survives {layer_names()}")
    check(image_paint().canvas == node("Highlights").image, "the canvas is the active layer's image")
    check_composite("reopened", expected())

    section("undo and redo")
    edit("Unclip Shade", "Shade", is_clip=False)
    check_composite("unclipped", expected(clip_shade=False))
    bpy.ops.ed.undo()
    check(node("Shade").is_clip, "undo clips Shade again")
    check_composite("undo unclip", expected())
    bpy.ops.ed.redo()
    check_composite("redo unclip", expected(clip_shade=False))
    bpy.ops.ed.undo()

    select("Paper")
    check(run(bpy.ops.paint_system.remove_layer), "remove Paper")
    check("Paper" not in layer_names(), f"Paper is gone {layer_names()}")
    bpy.ops.ed.undo()
    check(layer_names() == ORDER, f"undo restores Paper {layer_names()}")
    check_composite("undo remove", expected())

    section("save again")
    save_and_reopen("second save")
    check(layer_names() == ORDER, f"the stack order survives {layer_names()}")
    check_composite("reopened again", expected())


guarded(test_demo)
finish("DEMO FLOW TEST")
