"""Stack model tests: folders, the stack walk, link edits and PSContext (PS-010, PS-019, PS-030)."""
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, close, finish, fmt, import_from, pixel_at,  # noqa: E402
                     register_addon, section)

register_addon()
core = import_from("compiler.core")
stack_ops = import_from("nodetree.stack_ops")
parse_context = import_from("context").parse_context
button_layer = import_from("context").button_layer
link_tree_to_material = import_from("ops.node_tree_ops").link_tree_to_material

SOLID = 'PaintSystemSolidColorLayerNode'
FOLDER = 'PaintSystemFolderLayerNode'
RED = (1.0, 0.0, 0.0, 1.0)


def new_tree(name):
    tree = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    tree.initialize()
    return tree


def layout(tree):
    """(name, level, parent name, index in parent) per stack item."""
    return [(item.node.name, item.level, item.parent.node.name if item.parent else None,
             item.index_in_parent) for item in tree.stack()]


def solid(tree, name, color=RED, target=None):
    node = tree.insert_layer_node(SOLID, target=target)
    node.name = name
    node.fill_color = color
    return node


def folder(tree, name, target=None):
    node = tree.insert_layer_node(FOLDER, target=target)
    node.name = name
    return node


def check_one_link(tree, label):
    """Every layer in the stack feeds exactly one slot, through its Color output."""
    for item in tree.stack():
        node = item.node
        slots = [link.to_socket for link in stack_ops.stack_output(node).links
                 if stack_ops.is_slot(link.to_socket)]
        check(len(slots) == 1 and slots[0] == stack_ops.consumer_input(node),
              f"{label}: {node.name} feeds one slot")
        check([socket.name for socket in node.outputs] == ["Color"], f"{label}: {node.name} has one output")


def composite(tree):
    core.compile_tree(tree)
    rgba = bake_group(tree.compiled, color="Color", alpha="Color Alpha", size=4)
    return pixel_at(rgba, 0.5, 0.5, 4)


def check_pixel(label, got, want, tol=1e-3):
    check(close(got, want, tol), f"{label}: {fmt(got)} expected {fmt(want)}")


try:
    section("stack walk")
    tree = new_tree("Stack")
    a = solid(tree, "A")
    f = folder(tree, "F")
    b = solid(tree, "B", target=f)
    g = folder(tree, "G", target=b)
    solid(tree, "C", target=g)
    d = solid(tree, "D")
    check(layout(tree) == [
        ("D", 0, None, 0),
        ("F", 0, None, 1),
        ("G", 1, "F", 0),
        ("C", 2, "G", 0),
        ("B", 1, "F", 1),
        ("A", 0, None, 2),
    ], f"folders nested two deep walk top first with levels {layout(tree)}")
    check(tree.nodes.active == d, "the inserted layer becomes active")
    check(not tree.nodes["B"].inputs['Color'].is_linked, "bottom layer of a folder has nothing below it")
    check([n.name for n in stack_ops.descendants(f)] == ["G", "C", "B"], "descendants include nested content")
    check(tree.nodes["C"].location.y > g.location.y > a.location.y, "folder content is laid out above its folder")
    check([socket.name for socket in a.inputs] == ["Color", "Mask"], "a layer takes Color, then Mask")
    check([socket.name for socket in f.inputs] == ["Color", "Content Color", "Mask"],
          "a folder takes Color, then Content Color, with Mask last")
    check_one_link(tree, "after inserts")

    section("insert into folders")
    e = tree.nodes.new(SOLID)
    e.name = "E"
    with core.suspend_compile(tree):
        stack_ops.insert_into(tree, f, e, at_top=False)
    check([name for name, *_ in layout(tree)] == ["D", "F", "G", "C", "B", "E", "A"], "insert at the bottom of a folder")
    h = folder(tree, "H")
    solid(tree, "I", target=h)
    check(layout(tree)[:2] == [("H", 0, None, 0), ("I", 1, "H", 0)], "insert into an empty folder")
    check_one_link(tree, "after folder inserts")

    section("remove")
    tree.remove_layer_node(f)
    check([name for name, *_ in layout(tree)] == ["H", "I", "D", "A"], f"removing a folder removes its content {layout(tree)}")
    check(all(name not in tree.nodes for name in "FGCBE"), "folder content nodes are deleted")
    check(stack_ops.consumer_input(a).node == d, "the gap closes around the removed folder")
    tree.remove_layer_node(tree.nodes["I"])
    check(not h.inputs['Content Color'].is_linked, "removing the only child empties the folder")
    check_one_link(tree, "after removes")
    core.compile_tree(tree)
    check(core.artifact_fingerprint(tree) == core.build_ir(tree).fingerprint(), "compiles after the edits")

    section("folder compositing")
    flat = new_tree("Flat")
    solid(flat, "Base", RED)
    solid(flat, "Top", (0.0, 0.0, 1.0, 0.5))
    nested = new_tree("Nested")
    solid(nested, "Base", RED)
    box = folder(nested, "Box")
    inner = solid(nested, "Top", (0.0, 0.0, 1.0, 0.5), target=box)
    want = (0.5, 0.0, 0.5, 1.0)
    check_pixel("flat stack", composite(flat), want)
    check_pixel("MIX folder at opacity 1 equals the flat stack", composite(nested), want)
    box.opacity = 0.5
    check_pixel("folder opacity scales its content", composite(nested), (0.75, 0.0, 0.25, 1.0))
    box.opacity = 1.0
    box.enabled = False
    check_pixel("a disabled folder hides its content", composite(nested), RED)
    check(inner.enabled, "disabling a folder leaves its layers untouched")
    box.enabled = True
    empty = folder(nested, "Empty")
    check_pixel("an empty folder is transparent", composite(nested), want)
    nested.remove_layer_node(empty)

    section("group layer compositing")
    child = new_tree("Child")
    solid(child, "Top", (0.0, 0.0, 1.0, 0.5))
    parent = new_tree("Parent")
    base = solid(parent, "Base", RED)
    group = parent.nodes.new('PaintSystemGroupLayerNode')
    group.node_tree = child
    parent.links.new(base.outputs['Color'], group.inputs['Color'])
    parent.links.new(group.outputs['Color'], parent.get_output_node().inputs['Color'])
    check_pixel("a group layer composites its tree over the colour and alpha it takes in",
                composite(parent), want)

    section("hand-made links")
    output = nested.get_output_node()
    nested.links.new(inner.outputs['Color'], output.inputs['Color'])
    core.flush_now()
    check(core.artifact_fingerprint(nested) == core.build_ir(nested).fingerprint(),
          "the hand edit compiles on the next tick")
    check_pixel("one link carries the colour and the alpha", composite(nested), (0.0, 0.0, 1.0, 0.5))
    nested.links.remove(output.inputs['Color'].links[0])
    check_pixel("an unlinked channel is transparent", composite(nested), (0.0, 0.0, 0.0, 0.0))
    nested.links.new(box.outputs['Color'], output.inputs['Color'])
    check_pixel("relinking the folder restores the stack", composite(nested), want)

    section("mask links")
    # A layer can also feed another layer's Mask. That link is not part of
    # the stack, so edits must neither follow it nor remove it. It is made
    # before the stack link here, so it comes first in the output's links.
    masked = new_tree("Masked")
    top = solid(masked, "Top")
    bottom = masked.nodes.new(SOLID)
    bottom.name = "Bottom"
    masked.links.new(bottom.outputs['Color'], top.inputs['Mask'])
    with core.suspend_compile(masked):
        stack_ops.insert_below(masked, bottom, top)
    check(stack_ops.consumer_input(bottom) == top.inputs['Color'], "the stack skips a link into a mask")
    mid = solid(masked, "Mid", target=bottom)
    check([name for name, *_ in layout(masked)] == ["Top", "Mid", "Bottom"], "a layer inserts above the masking layer")
    check(masked.move_layer_node(mid, 'DOWN', 'SKIP'), "the layer above it moves down")
    check([name for name, *_ in layout(masked)] == ["Top", "Bottom", "Mid"], f"past it {layout(masked)}")
    check(top.inputs['Mask'].is_linked and top.inputs['Mask'].links[0].from_node == bottom,
          "the mask link survives the insert and the move")
    check(masked.move_layer_node(bottom, 'DOWN', 'SKIP'), "the layer feeding the mask moves down")
    check([name for name, *_ in layout(masked)] == ["Top", "Mid", "Bottom"], f"past Mid {layout(masked)}")
    check(top.inputs['Mask'].is_linked and top.inputs['Mask'].links[0].from_node == bottom,
          "moving the layer that feeds a mask keeps the mask link")
    check(masked.move_layer_node(bottom, 'UP', 'SKIP'), "and moves back up")

    def bottom_feeds():
        return sorted((link.to_node.name, link.to_socket.identifier)
                      for link in stack_ops.stack_output(bottom).links)

    fed = bottom_feeds()
    check(not masked.move_layer_node(bottom, 'UP', 'SKIP'),
          "a layer cannot move above the layer it masks, which would loop the mask link")
    check(not masked.move_layer_node(top, 'DOWN', 'SKIP'),
          "nor can the masked layer move below the layer that masks it")
    check([name for name, *_ in layout(masked)] == ["Top", "Bottom", "Mid"] and bottom_feeds() == fed,
          f"the refused moves leave the stack and the links as they were {layout(masked)}")
    check_one_link(masked, "with a mask link")
    core.compile_tree(masked)
    blend = next(n for n in masked.compiled.nodes if n.get("ps_identifier") == f"{top.uuid}:blend")
    check(blend.inputs['Mask'].is_linked, "the compiled mask reads the layer")

    reroute = masked.nodes.new('NodeReroute')
    masked.links.new(bottom.outputs['Color'], reroute.inputs[0])
    masked.links.new(reroute.outputs[0], top.inputs['Mask'])
    check(not stack_ops.is_slot(reroute.inputs[0]), "a reroute is never a stack slot")
    # Down and back up leaves the reroute link first on the output, so the
    # insert below has to skip it to find the stack.
    check(masked.move_layer_node(bottom, 'DOWN', 'SKIP') and masked.move_layer_node(bottom, 'UP', 'SKIP'),
          "the layer feeding the reroute moves down and back")
    check(reroute.inputs[0].is_linked and reroute.inputs[0].links[0].from_node == bottom,
          "a move keeps the reroute link")
    extra = solid(masked, "Extra", target=bottom)
    check([name for name, *_ in layout(masked)] == ["Top", "Extra", "Bottom", "Mid"],
          f"so an insert above the layer feeding it goes into the stack {layout(masked)}")
    check(reroute.inputs[0].links[0].from_node == bottom, "and the reroute still reads the same layer")
    masked.remove_layer_node(extra)
    check(reroute.inputs[0].is_linked, "a remove leaves it too")

    section("editing state stays out of the shader")
    base = nested.nodes["Base"]
    fingerprint = core.compile_tree(nested)
    ir = core.build_ir(nested)
    subtree = ir.ctx.subtree_hash(base)
    base.lock_layer = True
    base.lock_alpha = True
    box.is_expanded = False
    check(core.compile_tree(nested) == fingerprint, "lock flags and folder expansion keep the fingerprint")
    check(core.build_ir(nested).ctx.subtree_hash(base) == subtree, "lock flags keep the cache hash")

    section("PSContext")
    view_layer = bpy.context.view_layer
    cube = bpy.data.objects["Cube"]
    for obj in view_layer.objects:
        obj.select_set(obj == cube)
    view_layer.objects.active = cube
    cube.data.materials.clear()
    ps = parse_context(bpy.context)
    check(ps.ps_object == cube and cube.active_material is None, "cube without a material")
    check(ps.tree is None and ps.layer is None and ps.stack_item is None, "no material: no tree, layer or stack item")

    empty_obj = bpy.data.objects.new("Gradient Empty", None)
    bpy.context.scene.collection.objects.link(empty_obj)
    empty_obj.parent = cube
    view_layer.objects.active = empty_obj
    ps = parse_context(bpy.context)
    check(bpy.context.object == empty_obj and ps.ps_object == cube, "an empty resolves to its parent mesh")

    view_layer.objects.active = cube
    mat = bpy.data.materials.new("Stack Material")
    cube.data.materials.append(mat)
    link_tree_to_material(mat, nested)
    nested.nodes.active = inner
    ps = parse_context(bpy.context)
    check(ps.ps_object.active_material == mat and ps.tree == nested, "material and tree")
    check(ps.channel == nested.channels[0], "active channel")
    check(ps.layer == inner and ps.stack_item is not None and ps.stack_item.parent.node == box,
          "active layer and its stack item")

    check(button_layer(bpy.context, nested) == inner, "a layer button acts on the active layer")
    with bpy.context.temp_override(node=box):
        check(button_layer(bpy.context, nested) == box, "or on the node that drew the button")
    with bpy.context.temp_override(node=nested.get_input_node()):
        check(button_layer(bpy.context, nested) is None, "and on nothing when that is not a layer")
    check(bpy.ops.paint_system.bake_cache.poll(), "Bake Layer Cache polls on the active layer")
    nested.nodes.active = nested.get_input_node()
    check(button_layer(bpy.context, nested) is None and not bpy.ops.paint_system.bake_cache.poll(),
          "and not with a Group Input node active")
    nested.nodes.active = inner

    section("operators")
    nested.nodes.active = box
    check(bpy.ops.paint_system.add_layer(layer_type='SOLID_COLOR') == {'FINISHED'}, "add with a folder active")
    added = nested.nodes.active
    check(layout(nested)[1][0] == added.name and layout(nested)[1][2] == "Box", "adds at the top of the folder")
    check(bpy.ops.paint_system.add_layer(layer_type='FOLDER') == {'FINISHED'}, "add a folder with a layer active")
    sub = nested.nodes.active
    check(sub.is_folder and layout(nested)[1][0] == sub.name and layout(nested)[2][0] == added.name,
          f"adds directly above the active layer {layout(nested)}")
    nested.nodes.active = box
    check(bpy.ops.paint_system.remove_layer() == {'FINISHED'}, "remove the folder")
    check([name for name, *_ in layout(nested)] == ["Base"], f"folder and content removed {layout(nested)}")
    check(nested.nodes.active == base, "the row below becomes active")
    check(core.artifact_fingerprint(nested) == core.build_ir(nested).fingerprint(), "operators leave the artifact current")
except Exception:
    traceback.print_exc()
    check(False, "unexpected exception")

finish("STACK TEST")
