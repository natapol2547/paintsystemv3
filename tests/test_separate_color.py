"""The Separate Color node in the Paint System tree (PS-098 slice 4).

A link carries one RGBA value, and this node splits one into floats that
can start another channel's stack or feed a mask. It is not a layer, so
the stack model has to leave it where it is: a layer feeding it stays in
its own stack, and a stack starting from it keeps it at the bottom
through edits.
"""
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, close, finish, fmt, import_from,  # noqa: E402
                     pixel_at, register_addon, section)

register_addon()
core = import_from("compiler.core")
stack_ops = import_from("nodetree.stack_ops")
composite = import_from("filters.composite")
filters_core = import_from("filters.core")
layer_plan = import_from("filters.layer_plan")
node_categories = import_from("nodetree").node_categories

SOLID = 'PaintSystemSolidColorLayerNode'
FOLDER = 'PaintSystemFolderLayerNode'
FILTER = 'PaintSystemFilterLayerNode'
SEPARATE = 'PaintSystemSeparateColorNode'
SIZE = 4


def new_tree(name):
    tree = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    tree.initialize()
    return tree


def output_socket(tree, channel_name):
    return stack_ops.socket_named(tree.get_output_node().inputs, channel_name)


def solid(tree, channel_name, color, target=None):
    with core.suspend_compile(tree):
        node = tree.insert_layer_node(SOLID, channel_name, target=target)
        node.fill_color = color
    return node


def value(tree, channel_name):
    """The baked value of a channel without alpha, at the centre."""
    core.flush_now()
    rgba = bake_group(tree.compiled, color=channel_name, alpha=channel_name, size=SIZE)
    return pixel_at(rgba, 0.5, 0.5, SIZE)[0]


def near(a, b, tol=0.02):
    return abs(a - b) <= tol


def color(tree):
    core.flush_now()
    rgba = bake_group(tree.compiled, color="Color", alpha="Color Alpha", size=SIZE)
    return pixel_at(rgba, 0.5, 0.5, SIZE)


def names(tree, channel_name):
    return [item.node.name for item in tree.stack(channel_name)]


try:
    section("the node")
    items = [item.nodetype for category in node_categories() for item in category.items(None)]
    check(SEPARATE in items, "the Add menu offers it")
    tree = new_tree("Separate")
    tree.create_channel("Rough", 'FLOAT')
    separate = tree.nodes.new(SEPARATE)
    check(separate.uuid and not stack_ops.is_layer(separate), "it has a uuid and is not a layer")
    check([socket.identifier for socket in separate.outputs] == ["Red", "Green", "Blue", "Alpha"]
          and all(socket.bl_idname == 'NodeSocketFloat' for socket in separate.outputs),
          "it gives Red, Green, Blue and Alpha floats")
    check(not stack_ops.is_slot(separate.inputs['Color']), "its input is not a stack slot")

    section("an output starts another channel")
    top = solid(tree, "Color", (0.2, 0.6, 0.4, 1.0))
    tree.links.new(top.outputs['Color'], separate.inputs['Color'])
    tree.links.new(separate.outputs['Green'], output_socket(tree, "Rough"))
    check(stack_ops.consumer_input(top) == output_socket(tree, "Color") and names(tree, "Color") == [top.name],
          "the layer feeding it stays in its own stack")
    got = value(tree, "Rough")
    check(near(got, 0.6), f"Rough is Color's green {got:.3f}")
    got = color(tree)
    check(close(got, (0.2, 0.6, 0.4, 1.0)), f"and Color is unchanged {fmt(got)}")

    section("stack edits keep it at the bottom")
    over = solid(tree, "Rough", (1.0, 1.0, 1.0, 0.5))
    check(names(tree, "Rough") == [over.name], f"a layer goes on top of it {names(tree, 'Rough')}")
    check(over.inputs['Color'].links[0].from_socket == separate.outputs['Green'],
          "and takes it as the stack below")
    got = value(tree, "Rough")
    check(near(got, 0.8), f"so half of white over the green {got:.3f}")
    under = solid(tree, "Rough", (0.0, 0.0, 0.0, 0.0), target=over)
    with core.suspend_compile(tree):
        tree.move_layer_node(under, 'DOWN', 'SKIP')
    check(names(tree, "Rough") == [over.name, under.name]
          and under.inputs['Color'].links[0].from_socket == separate.outputs['Green'],
          f"a layer moved to the bottom goes above it {names(tree, 'Rough')}")
    with core.suspend_compile(tree):
        tree.remove_layer_node(under)
        tree.remove_layer_node(over)
    check(output_socket(tree, "Rough").links[0].from_socket == separate.outputs['Green'],
          "removing the layers links it to the output again")
    check(top.outputs['Color'].links and separate.inputs['Color'].links[0].from_node == top,
          "and the layer feeding it is still linked")

    section("modes")
    before = core.subtree_hash(tree, separate)
    separate.mode = 'HSV'
    check([socket.name for socket in separate.outputs] == ["Hue", "Saturation", "Value", "Alpha"],
          f"HSV names the outputs {[socket.name for socket in separate.outputs]}")
    check([socket.identifier for socket in separate.outputs] == ["Red", "Green", "Blue", "Alpha"]
          and output_socket(tree, "Rough").links[0].from_socket == separate.outputs['Green'],
          "and keeps their identifiers and links")
    check(core.subtree_hash(tree, separate) != before, "the mode changes the hash of what reads it")
    got = value(tree, "Rough")
    check(near(got, 0.4 / 0.6), f"Rough is the HSV saturation {got:.3f}")
    separate.mode = 'HSL'
    check(separate.outputs['Blue'].name == "Lightness", "HSL names the third output Lightness")
    got = value(tree, "Rough")
    check(near(got, 0.5), f"Rough is the HSL saturation {got:.3f}")
    separate.mode = 'RGB'
    check([socket.name for socket in separate.outputs] == ["Red", "Green", "Blue", "Alpha"],
          "RGB names them back")
    check(core.subtree_hash(tree, separate) == before, "and restores the hash")

    section("alpha, and an unlinked input")
    top.fill_color = (0.2, 0.6, 0.4, 0.25)
    tree.links.new(separate.outputs['Alpha'], output_socket(tree, "Rough"))
    got = value(tree, "Rough")
    check(near(got, 0.25), f"Alpha gives the stack's alpha {got:.3f}")
    tree.links.new(separate.outputs['Green'], output_socket(tree, "Rough"))
    got = value(tree, "Rough")
    check(near(got, 0.6), f"and a float carries an alpha of 1, so Green is the colour's green {got:.3f}")
    tree.links.remove(separate.inputs['Color'].links[0])
    tree.links.new(separate.outputs['Red'], output_socket(tree, "Rough"))
    got = value(tree, "Rough")
    check(near(got, 0.8), f"unlinked, it separates its own colour {got:.3f}")
    separate.inputs['Color'].default_value = (0.3, 0.0, 0.0, 1.0)
    got = value(tree, "Rough")
    check(near(got, 0.3), f"and follows a change to it {got:.3f}")

    section("an output as a mask")
    masked = new_tree("Separate Mask")
    bottom = solid(masked, "Color", (0.5, 0.0, 0.0, 1.0))
    top = solid(masked, "Color", (0.0, 0.0, 1.0, 1.0))
    mask = masked.nodes.new(SEPARATE)
    masked.links.new(bottom.outputs['Color'], mask.inputs['Color'])
    masked.links.new(mask.outputs['Red'], top.inputs['Mask'])
    got = color(masked)
    check(close(got, (0.25, 0.0, 0.5, 1.0)), f"the red below masks the layer above by half {fmt(got)}")
    check(not masked.move_layer_node(top, 'DOWN', 'SKIP'),
          "the masked layer cannot move below the layer its mask reads")
    check(names(masked, "Color") == [top.name, bottom.name] and mask.inputs['Color'].links[0].from_node == bottom,
          "and the refused move leaves the stack and the links as they were")

    section("a loop through a stack the node starts")
    # The folder's content starts from the red of the layer below the
    # folder. Moving that layer up, above the folder or into it, would
    # make it read its own result.
    looped = new_tree("Separate Loop")
    below = solid(looped, "Color", (0.5, 0.0, 0.0, 1.0))
    with core.suspend_compile(looped):
        box = looped.insert_layer_node(FOLDER, "Color")
    inner = solid(looped, "Color", (0.0, 0.0, 1.0, 0.5), target=box)
    source = looped.nodes.new(SEPARATE)
    looped.links.new(below.outputs['Color'], source.inputs['Color'])
    looped.links.new(source.outputs['Red'], inner.inputs['Color'])
    before = names(looped, "Color")
    check(before == [box.name, inner.name, below.name], f"the folder holds the layer over the node {before}")
    offered = [option.action for option in stack_ops.movement_options(looped.stack("Color"), below, 'UP')]
    check(set(offered) == {'MOVE_ADJACENT', 'SKIP'}, f"moving the layer below up is offered {offered}")
    for action in offered:
        check(not looped.move_layer_node(below, 'UP', action), f"but {action} is refused")
    check(names(looped, "Color") == before and inner.inputs['Color'].links[0].from_node == source
          and source.inputs['Color'].links[0].from_node == below,
          "and the refused moves leave the stack and the links as they were")

    section("under a filter layer")
    tree.create_channel("Tint", 'COLOR')
    with core.suspend_compile(tree):
        tree.links.new(separate.outputs['Green'], output_socket(tree, "Tint"))
        node = tree.insert_layer_node(FILTER, "Tint")
    check(node.inputs['Color'].links[0].from_node == separate, "a filter layer takes the node as the stack below")
    try:
        layer_plan.resolve_input(bpy.context, tree, node)
        check(False, "a filter over it builds")
    except filters_core.Refused as error:
        check("Cycles bake" in str(error) and separate.name in str(error),
              f"a filter over it needs a Cycles bake, and the refusal names it: {error}")

    masked.links.remove(top.inputs['Mask'].links[0])
    with core.suspend_compile(masked):
        own = masked.insert_layer_node(FILTER, "Color")
        masked.links.new(mask.outputs['Red'], own.inputs['Mask'])
    plan = composite.plan_below(own)
    check(len(plan.layers) == 2, "a filter layer whose own mask comes from the node still plans")

except Exception:
    traceback.print_exc()
    check(False, "unexpected exception")

finish("SEPARATE COLOR TEST")
