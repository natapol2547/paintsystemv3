"""End-to-end smoke test of the loop the playable demo relies on.

Drives the user-facing operators on the factory cube: set up a material,
add layers, paint pixels, save and reload, undo and redo. Nothing calls
the compiler by hand: the artifact must already match when an operator
returns, because that is the state Blender records in the undo step.

Undo and file load free every ID, so nothing here keeps a Python
reference to tree, node, image or material data across them; everything
is looked up again by name.
"""
import os
import sys
import tempfile
import traceback

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, close, finish, fmt, import_from, pixel_at,  # noqa: E402
                     register_addon, section)

register_addon()
core = import_from("compiler.core")
MATERIAL_GROUP_KEY = import_from("ops.node_tree_ops").MATERIAL_GROUP_KEY

RED = (1.0, 0.0, 0.0, 1.0)
BLUE = (0.0, 0.0, 1.0, 1.0)


def cube():
    return bpy.data.objects["Cube"]


def active_tree():
    mat = cube().active_material
    return mat.paint_system.tree if mat else None


def material_group(mat, tree):
    return next((n for n in mat.node_tree.nodes
                 if n.bl_idname == 'ShaderNodeGroup' and n.get(MATERIAL_GROUP_KEY) == tree.uuid), None)


def layer_names():
    tree = active_tree()
    return [item.node.name for item in tree.stack()] if tree else None


def check_artifact(label):
    """The compiled group exists, belongs to the tree, is up to date and is what the material uses."""
    tree = active_tree()
    art = tree.compiled if tree else None
    if not check(art is not None and art.bl_idname == 'ShaderNodeTree', f"{label}: artifact exists"):
        return
    check(art.get('ps_owner') == tree.uuid, f"{label}: artifact owned by the tree")
    owned = [ng.name for ng in bpy.data.node_groups if ng.get('ps_owner') == tree.uuid]
    check(owned == [art.name], f"{label}: exactly one artifact for the tree {owned}")
    check(core.artifact_fingerprint(tree) == core.build_ir(tree).fingerprint(),
          f"{label}: artifact matches the tree")
    group = material_group(cube().active_material, tree)
    check(group is not None and group.node_tree == art, f"{label}: material instances the artifact")
    blends = sorted(n.get('ps_identifier') for n in art.nodes
                    if (n.get('ps_identifier') or '').endswith(':blend'))
    expected = sorted(f"{item.node.uuid}:blend" for item in tree.stack())
    check(blends == expected, f"{label}: one blend instance per stacked layer")


def paint_left_half(image, color):
    w, h = image.size
    px = np.zeros((h, w, 4), dtype=np.float32)
    px[:, :w // 2] = color
    image.pixels.foreach_set(px.ravel())
    image.update()


def check_bake(label, left, right):
    rgba = bake_group(active_tree().compiled)
    got_left, got_right = pixel_at(rgba, 0.25, 0.5), pixel_at(rgba, 0.75, 0.5)
    check(close(got_left, left), f"{label}: left half {fmt(got_left)} == {fmt(left)}")
    check(close(got_right, right), f"{label}: right half {fmt(got_right)} == {fmt(right)}")


def init_undo():
    # Background sessions start (and reload files) with undo disabled until
    # the first explicit push. That push writes two identical steps in a
    # row, the pattern where memfile undo reuses in-memory datablocks, so
    # it also covers what an idle click between two edits does in the UI.
    bpy.ops.ed.undo_push(message="Smoke test start")


def run(op, **props):
    """Call an operator like the UI does. The positional ``True`` asks for
    the undo push that script calls skip by default."""
    return op('EXEC_DEFAULT', True, **props) == {'FINISHED'}


def check_no_freed_images(label):
    art = active_tree().compiled
    names = [n.image.name for n in art.nodes if n.bl_idname == 'ShaderNodeTexImage' and n.image]
    check(all(name in bpy.data.images for name in names), f"{label}: artifact only uses live images {names}")


try:
    bpy.context.view_layer.objects.active = cube()
    init_undo()

    section("setup material, undo, redo")
    check(run(bpy.ops.paint_system.setup_material), "setup_material finished")
    check(active_tree() is not None, "material has a tree")
    check_artifact("after setup")
    bpy.ops.ed.undo()
    check(active_tree() is None, "undo removes the tree from the material")
    check(not list(core.ps_trees()), "undo removes the tree datablock")
    check(not [ng for ng in bpy.data.node_groups if ng.get('ps_owner')], "undo removes the artifact")
    bpy.ops.ed.redo()
    check(active_tree() is not None, "redo restores the tree")
    check_artifact("after redo setup")

    section("add layers through operators")
    check(run(bpy.ops.paint_system.add_layer, layer_type='SOLID'), "add solid layer")
    active_tree().nodes.active.fill_color = RED
    check(run(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024'), "add image layer")
    tree = active_tree()
    image_node = tree.nodes.active
    check(image_node.bl_idname == 'PaintSystemImageLayerNode', "new image layer is active")
    check([item.node.bl_idname for item in tree.stack()]
          == ['PaintSystemImageLayerNode', 'PaintSystemSolidColorLayerNode'], "image stacked above solid")
    image = image_node.image
    check(image is not None and image.get('ps_managed'), "image layer owns a managed image")
    check(bpy.context.scene.tool_settings.image_paint.canvas == image, "new image is the paint canvas")
    IMAGE_NAME = image.name
    check_artifact("after adding layers")

    section("painted pixels reach the compiled group")
    check_bake("unpainted", RED, RED)
    paint_left_half(image, BLUE)
    check(image.is_dirty, "painting marks the image dirty")
    check_bake("painted", BLUE, RED)
    del tree, image_node, image

    section("save and reload")
    path = os.path.join(tempfile.mkdtemp(prefix="ps_smoke_"), "smoke.blend")
    check(bpy.ops.wm.save_as_mainfile(filepath=path) == {'FINISHED'}, "saved")
    check(bpy.data.images[IMAGE_NAME].packed_file is not None, "save packs the painted image")
    check(bpy.ops.wm.open_mainfile(filepath=path) == {'FINISHED'}, "reopened")
    check(active_tree() is not None, "material still has its tree")
    check(bpy.data.images[IMAGE_NAME].packed_file is not None, "image still packed after reload")
    check_artifact("after reload")
    check_bake("after reload", BLUE, RED)

    section("undo and redo add layer")
    bpy.context.view_layer.objects.active = cube()
    init_undo()
    before = layer_names()
    check(run(bpy.ops.paint_system.add_layer, layer_type='SOLID'), "add another solid layer")
    check(len(layer_names()) == len(before) + 1, f"layer added {layer_names()}")
    names = layer_names()
    check(len(set(names)) == len(names) and all(active_tree().nodes.get(n) for n in names),
          "second solid layer gets a unique name nodes.get resolves")
    bpy.ops.ed.undo()
    check(layer_names() == before, f"undo restores the stack {layer_names()}")
    check_artifact("after undo add")
    check_bake("after undo add", BLUE, RED)
    bpy.ops.ed.redo()
    check(len(layer_names()) == len(before) + 1, f"redo re-adds the layer {layer_names()}")
    check_artifact("after redo add")
    bpy.ops.ed.undo()

    section("undo add image layer")
    bpy.ops.ed.undo_push(message="Smoke idle step")
    check(run(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024'), "add image layer")
    bpy.ops.ed.undo()
    check(layer_names() == before, f"undo removes the image layer {layer_names()}")
    check_no_freed_images("after undo add image")
    check_artifact("after undo add image")

    section("undo and redo remove layer")
    image_layer = next(item.node.name for item in active_tree().stack()
                       if item.node.bl_idname == 'PaintSystemImageLayerNode')
    check(run(bpy.ops.paint_system.set_active_layer, node_name=image_layer), "select image layer")
    check(run(bpy.ops.paint_system.remove_layer), "remove image layer")
    check(image_layer not in layer_names(), "image layer removed")
    check_artifact("after remove")
    check_bake("after remove", RED, RED)
    bpy.ops.ed.undo()
    check(layer_names() == before, f"undo restores the removed layer {layer_names()}")
    check_artifact("after undo remove")
    check_bake("after undo remove", BLUE, RED)
    bpy.ops.ed.redo()
    check(image_layer not in layer_names(), "redo removes the layer again")
    check_artifact("after redo remove")

    section("undo a node editor edit")
    # Links made in the node editor build on the next tick, after Blender
    # pushed the undo step (core.tree_updated). flush_now stands in for the
    # timer.
    restored = []

    def record_restored_artifact(*args):
        # Inserted ahead of the addon's handler, which recompiles.
        restored.append({n.get('ps_identifier') or '' for n in active_tree().compiled.nodes})

    before = layer_names()
    tree = active_tree()
    hand = tree.nodes.new('PaintSystemImageLayerNode')
    hand.image = bpy.data.images.new("Hand Image", 8, 8)
    HAND_NAME, HAND_UUID = hand.name, hand.uuid
    bpy.ops.ed.undo_push(message="Smoke add unlinked layer")
    output = tree.get_output_node()
    tree.links.new(output.inputs['Color'].links[0].from_socket, hand.inputs['Color'])
    tree.links.new(hand.outputs['Color'], output.inputs['Color'])
    bpy.ops.ed.undo_push(message="Smoke link by hand")
    core.flush_now()
    check(layer_names() == [HAND_NAME] + before, f"hand-linked layer tops the stack {layer_names()}")
    check_artifact("after the hand link builds")
    del tree, hand, output
    bpy.app.handlers.undo_post.insert(0, record_restored_artifact)
    try:
        bpy.ops.ed.undo()
    finally:
        bpy.app.handlers.undo_post.remove(record_restored_artifact)
    check(restored and not any(i.startswith(HAND_UUID) for i in restored[0]),
          "undo re-reads the artifact instead of keeping the build made after the step")
    check(layer_names() == before, f"undo unlinks the hand-linked layer {layer_names()}")
    check_artifact("after undo hand link")
    check_no_freed_images("after undo hand link")

except Exception:
    traceback.print_exc()
    check(False, "exception")

finish("SMOKE LOOP TEST")
