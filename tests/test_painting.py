"""Texture painting follows the active layer (PS-060, PS-061).

Drives the operators on the factory cube and checks where painting goes:
the canvas, the image paint mode, the mesh's active UV map and the brush
alpha, as the active layer, its lock settings, the active object and the
active material change, and across paint mode, undo and file reload.
None of it may recompile the tree.

Clicking a node in the node editor and the message bus subscription for
material slots need a window loop and are covered by test_ui_draw.py.
"""
import os
import sys
import tempfile

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, guarded, import_from, register_addon, section  # noqa: E402

register_addon()
core = import_from("compiler.core")
paint_handlers = import_from("handlers.paint_handlers")


def cube():
    return bpy.data.objects["Cube"]


def tree_of(obj):
    mat = obj.active_material
    return mat.paint_system.tree if mat else None


def image_paint():
    return bpy.context.scene.tool_settings.image_paint


def run(op, **props):
    """Call an operator like the UI does, with its undo push.

    The window loop evaluates the depsgraph after every operator, which
    runs the depsgraph handlers; a script call does not, so it is done here.
    """
    result = op('EXEC_DEFAULT', True, **props)
    bpy.context.view_layer.update()
    return result == {'FINISHED'}


def select(tree, node):
    """Click a row in the layer list."""
    tree.active_layer_index = tree.nodes.find(node.name)


def activate(obj):
    """Click an object in the viewport: the depsgraph update is what the addon sees."""
    view_layer = bpy.context.view_layer
    view_layer.objects.active = obj
    view_layer.update()


def layer(tree, bl_idname):
    return next(item.node for item in tree.stack() if item.node.bl_idname == bl_idname)


def test_active_layer():
    section("canvas follows the active layer")
    activate(cube())
    bpy.ops.ed.undo_push(message="Painting test start")
    check(run(bpy.ops.paint_system.setup_material), "setup_material finished")
    check(run(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024'), "add image layer")
    check(run(bpy.ops.paint_system.add_layer, layer_type='SOLID_COLOR'), "add solid layer")
    tree = tree_of(cube())
    image_node = layer(tree, 'PaintSystemImageLayerNode')
    solid = layer(tree, 'PaintSystemSolidColorLayerNode')
    fingerprint = core.artifact_fingerprint(tree)

    check(image_paint().canvas is None, "a new solid layer leaves nothing to paint on")
    select(tree, image_node)
    check(image_paint().canvas == image_node.image, "selecting the image layer paints on its image")
    check(image_paint().mode == 'IMAGE', "image paint mode is single image")
    select(tree, solid)
    check(image_paint().canvas is None, "selecting a solid layer clears the canvas")
    image_paint().mode = 'MATERIAL'
    select(tree, image_node)
    check(image_paint().mode == 'IMAGE' and image_paint().canvas == image_node.image,
          "material paint mode switches back to single image")

    section("UV map")
    uv_layers = cube().data.uv_layers
    uv_layers.new(name="Paint UV")
    check(uv_layers.active.name == "UVMap", "a new UV map does not become active")
    image_node.uv_map = "Paint UV"
    check(uv_layers.active.name == "Paint UV", "the layer's UV map becomes the active UV map")
    check(core.artifact_fingerprint(tree) != fingerprint, "changing the UV map recompiles")
    image_node.uv_map = ""
    check(uv_layers.active.name == "UVMap", "no UV map paints through the active render UV map")
    check(core.artifact_fingerprint(tree) == fingerprint, "clearing the UV map restores the artifact")

    section("lock")
    image_node.lock_layer = True
    check(image_paint().canvas is None, "locking the layer clears the canvas")
    image_node.lock_layer = False
    check(image_paint().canvas == image_node.image, "unlocking paints on it again")
    image_node.lock_alpha = True
    check(image_paint().canvas == image_node.image, "lock alpha keeps the canvas")
    image_node.lock_alpha = False
    check(core.artifact_fingerprint(tree) == fingerprint, "selection and locks do not recompile")

    section("paint mode")
    check(run(bpy.ops.paint_system.toggle_paint_mode), "toggle into paint mode")
    check(cube().mode == 'TEXTURE_PAINT', f"the cube is in texture paint mode ({cube().mode})")
    check(image_paint().canvas == tree_of(cube()).nodes.active.image, "paint mode paints on the active layer")
    brush = image_paint().brush
    check(brush is not None, f"texture paint has a brush ({brush})")
    if brush is not None:
        tree = tree_of(cube())
        image_node = tree.nodes.active
        image_node.lock_alpha = True
        check(not brush.use_alpha, "lock alpha stops the brush changing alpha")
        image_node.lock_alpha = False
        check(brush.use_alpha, "unlocking alpha lets the brush change it")
        image_node.lock_alpha = True
        select(tree, layer(tree, 'PaintSystemSolidColorLayerNode'))
        select(tree, image_node)
        check(not brush.use_alpha, "selecting a layer with locked alpha applies it")
        image_node.lock_alpha = False
    check(run(bpy.ops.paint_system.toggle_paint_mode), "toggle out of paint mode")
    check(cube().mode == 'OBJECT', f"the cube is back in object mode ({cube().mode})")
    check(core.artifact_fingerprint(tree_of(cube())) == fingerprint, "paint mode does not recompile")


def test_active_object_and_material():
    section("active object")
    check(bpy.ops.mesh.primitive_plane_add() == {'FINISHED'}, "add a plane")
    plane = bpy.context.view_layer.objects.active
    check(run(bpy.ops.paint_system.setup_material), "setup_material on the plane")
    check(run(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024'), "add image layer to the plane")
    plane_image = tree_of(plane).nodes.active.image
    check(image_paint().canvas == plane_image, "the plane's layer is the canvas")
    cube_image = tree_of(cube()).nodes.active.image
    check(cube_image is not None and cube_image != plane_image, "the cube's active layer has its own image")
    activate(cube())
    check(image_paint().canvas == cube_image, "selecting the cube paints on its active layer")
    activate(plane)
    check(image_paint().canvas == plane_image, "selecting the plane paints on its active layer")

    section("active material")
    activate(cube())
    obj = cube()
    obj.data.materials.append(None)
    obj.active_material_index = 1
    check(run(bpy.ops.paint_system.setup_material), "setup_material on the second slot")
    check(run(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024'), "add image layer")
    second_image = tree_of(obj).nodes.active.image
    check(image_paint().canvas == second_image and second_image != cube_image, "the second material's layer")
    obj.active_material_index = 0
    # A material slot change is no depsgraph update. It notifies the message
    # bus, which only a window loop dispatches; test_ui_draw.py covers the
    # subscription itself.
    paint_handlers.on_active_material_index()
    check(image_paint().canvas == cube_image, "switching the material slot paints on that material's layer")


def test_undo_and_reload():
    section("undo")
    activate(cube())
    obj = cube()
    obj.active_material_index = 0
    tree = tree_of(obj)
    image_name = layer(tree, 'PaintSystemImageLayerNode').image.name
    select(tree, layer(tree, 'PaintSystemImageLayerNode'))
    bpy.ops.ed.undo_push(message="Select image layer")
    select(tree, layer(tree, 'PaintSystemSolidColorLayerNode'))
    bpy.ops.ed.undo_push(message="Select solid layer")
    check(image_paint().canvas is None, "the solid layer is selected")
    bpy.ops.ed.undo()
    canvas = image_paint().canvas
    check(canvas is not None and canvas.name == image_name, f"undo restores the canvas with the selection ({canvas})")
    bpy.ops.ed.redo()
    check(image_paint().canvas is None, "redo restores the solid layer's empty canvas")

    section("reload")
    tree = tree_of(cube())
    select(tree, layer(tree, 'PaintSystemImageLayerNode'))
    image_paint().canvas = None
    path = os.path.join(tempfile.mkdtemp(prefix="ps_painting_"), "painting.blend")
    check(bpy.ops.wm.save_as_mainfile(filepath=path) == {'FINISHED'}, "saved")
    check(bpy.ops.wm.open_mainfile(filepath=path) == {'FINISHED'}, "reopened")
    canvas = image_paint().canvas
    check(canvas is not None and canvas.name == image_name, f"loading the file paints on the active layer ({canvas})")


guarded(test_active_layer)
guarded(test_active_object_and_material)
guarded(test_undo_and_reload)
finish("PAINTING TEST")
