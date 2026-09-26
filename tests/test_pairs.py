"""Socket pairs: one layer in several stacks, one pair of sockets per stack (PS-098).

A link dropped on a layer's virtual input becomes a new pair. Each pair
blends its own stack with the layer's shared settings, and keeps its own
cache or filter result. Removing a pair keeps the other pairs' links.
"""
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, close, finish, fmt, import_from, over,  # noqa: E402
                     pixel_at, register_addon, section)

register_addon()
bake = import_from("compiler.bake")
core = import_from("compiler.core")
derived = import_from("filters.derived")
layer_job = import_from("filters.layer_job")
layer_ops = import_from("ops.layer_ops")
stack_ops = import_from("nodetree.stack_ops")

SOLID = 'PaintSystemSolidColorLayerNode'
FOLDER = 'PaintSystemFolderLayerNode'
FILTER = 'PaintSystemFilterLayerNode'
RED = (1.0, 0.0, 0.0, 1.0)
GREEN = (0.0, 1.0, 0.0, 1.0)
BLUE = (0.0, 0.0, 1.0, 1.0)
SIZE = 4


def new_tree(name):
    """A tree with two colour channels, Color and Second."""
    tree = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    tree.initialize()
    tree.create_channel("Second", 'COLOR')
    return tree


def add(tree, bl_idname, name, channel, color=None, target=None):
    node = tree.insert_layer_node(bl_idname, channel_name=channel, target=target)
    node.name = name
    if color is not None:
        node.fill_color = color
    return node


def link_into_virtual(tree, socket, node):
    """Drop a link from *socket* on *node*'s virtual input, as a drag in the node editor does."""
    tree.links.new(socket, stack_ops.virtual_input(node))


def top_of(tree, channel):
    """The output feeding *channel*'s socket on the Group Output."""
    return stack_ops.feeding_link(stack_ops.channel_input(tree, channel)).from_socket


def rows(tree, channel):
    return [(item.node.name, item.pair, item.level) for item in tree.stack(channel)]


def identifiers(sockets):
    return [socket.identifier for socket in sockets]


def names(sockets):
    return [socket.name for socket in sockets]


def compiled_ids(tree):
    return {node.get("ps_identifier") for node in tree.compiled.nodes}


def channel_pixel(tree, channel):
    core.flush_now()
    rgba = bake_group(tree.compiled, color=channel, alpha=f"{channel} Alpha", size=SIZE)
    return pixel_at(rgba, 0.5, 0.5, SIZE)


def painted_texels(image):
    """The (x, y) of every texel of *image* a bake has painted, from the bottom-left."""
    alpha = list(image.pixels)[3::4]
    width = image.size[0]
    return [(index % width, index // width) for index, value in enumerate(alpha) if value > 0.5]


def share_top(tree, node, channel):
    """Put a new pair of *node* on top of *channel*, through its virtual input."""
    link_into_virtual(tree, top_of(tree, channel), node)
    tree.links.new(node.outputs[-1], stack_ops.channel_input(tree, channel))


try:
    section("a new layer has one pair and a virtual input")
    tree = new_tree("Pairs")
    under = add(tree, SOLID, "Under", "Color", RED)
    ground = add(tree, SOLID, "Ground", "Second", GREEN)
    shared = add(tree, SOLID, "Shared", "Color", BLUE)
    shared.opacity = 0.5
    check(identifiers(shared.inputs) == ["Color", "__extend__", "Mask"]
          and names(shared.outputs) == ["Color"] and len(shared.pairs) == 1,
          f"inputs {identifiers(shared.inputs)}, outputs {names(shared.outputs)}")
    check(not hasattr(stack_ops.virtual_input(shared), 'default_value'),
          "the virtual input has no value, so nothing may read one from it")
    check(not stack_ops.is_slot(stack_ops.virtual_input(shared))
          and stack_ops.pair_of_input(stack_ops.virtual_input(shared)) is None,
          "and it is neither a slot nor a pair input")

    section("linking into the virtual input adds a pair")
    link_into_virtual(tree, top_of(tree, "Second"), shared)
    check(names(shared.inputs) == ["Color", "Color 2", "", "Mask"],
          f"the new pair input sits above the virtual input, with Mask last {names(shared.inputs)}")
    check(names(shared.outputs) == ["Color", "Color 2"] and len(shared.pairs) == 2,
          "with its own output and its own state")
    landed = stack_ops.feeding_link(stack_ops.below_input(shared, 1))
    check(landed is not None and landed.from_node == ground,
          "the link lands on the new pair's input")
    check(not stack_ops.virtual_input(shared).is_linked, "and leaves the virtual input free")
    shared.update()
    check(len(shared.outputs) == 2 and len(shared.inputs) == 4, "updating again adds nothing")

    tree.links.new(shared.outputs[1], stack_ops.channel_input(tree, "Second"))
    check(rows(tree, "Color") == [("Shared", 0, 0), ("Under", 0, 0)]
          and rows(tree, "Second") == [("Shared", 1, 0), ("Ground", 0, 0)],
          f"the layer sits in both stacks, one pair each {rows(tree, 'Color')} {rows(tree, 'Second')}")
    check(stack_ops.channel_of(tree, shared, 1).name == "Second"
          and tree.pair_in_stack(shared, "Second") == 1 and tree.pair_in_stack(shared, "Color") == 0,
          "each pair knows its channel")

    section("each pair blends its own stack with the shared settings")
    got = channel_pixel(tree, "Color")
    want = over(RED, BLUE, 0.5)
    check(close(got, want), f"Color is the layer over Under {fmt(got)}, want {fmt(want)}")
    got = channel_pixel(tree, "Second")
    want = over(GREEN, BLUE, 0.5)
    check(close(got, want), f"Second is the layer over Ground {fmt(got)}, want {fmt(want)}")
    ids = compiled_ids(tree)
    check(f"{shared.uuid}:blend" in ids and f"{shared.uuid}:blend@Color 2" in ids,
          "the first pair compiles as before, the second under its own name")
    shared.opacity = 1.0
    check(close(channel_pixel(tree, "Second"), BLUE) and close(channel_pixel(tree, "Color"), BLUE),
          "a setting changes every pair")

    section("each pair has its own cache")
    cache = bpy.data.images.new("Pair Cache", 8, 8)
    cache.pixels.foreach_set([1.0] * (8 * 8 * 4))
    # The hash first, as a bake writes it. Only the image marks the tree.
    shared.pairs[0].cache_hash = core.build_ir(tree).ctx.subtree_hash(shared, 0)
    shared.cache_enabled = True
    shared.pairs[0].cache_image = cache
    check(close(channel_pixel(tree, "Color"), (1.0, 1.0, 1.0, 1.0)),
          "the first pair shows its cache")
    check(close(channel_pixel(tree, "Second"), BLUE), "the second, with none, still blends its stack")
    check(shared.pairs[1].cache_stale is False and f"{shared.uuid}:cache@Color 2" not in compiled_ids(tree),
          "and has no cache node")
    cube = bpy.data.objects["Cube"]
    uv_layers = cube.data.uv_layers
    first_uv = uv_layers.active.name
    # Longer than the 63 bytes the bake operator's uv_layer string holds.
    second_uv = "Second UV map, with a name longer than any operator string holds"
    uv_layers.new(name=second_uv)
    # Squeeze the second UV map into the bottom-left quarter, so a bake
    # through any other map paints outside it.
    for corner in uv_layers[second_uv].data:
        corner.uv = corner.uv * 0.5
    uv_layers.active = uv_layers[first_uv]
    uv_layers[second_uv].active_render = True
    for pair, uv_map in ((0, ""), (1, second_uv)):
        baked = bake.bake_node_cache(bpy.context, tree, shared, cube, pair=pair, width=16, height=16,
                                     margin=0, uv_map=uv_map)
        painted = painted_texels(baked)
        outside = [(x, y) for x, y in painted if x >= 8 or y >= 8]
        check(painted and not outside,
              f"pair {pair} bakes through the UV map its cache is read with, "
              f"{'the named one' if uv_map else 'the active render one'}, not the active one "
              f"{len(painted)} {outside}")
    check(uv_layers.active.name == first_uv, "and the active UV map is the user's again")
    uv_layers[first_uv].active_render = True
    channel_pixel(tree, "Color")
    check(shared.cache_uv_map == second_uv and shared.pairs[1].cache_hash != ""
          and shared.pairs[0].cache_hash == "" and shared.pairs[0].cache_stale,
          "baking a pair with another UV map sends the other pairs back to baking, "
          "since one UV map reads every pair's cache")
    uv_layers.remove(uv_layers[second_uv])
    shared.cache_enabled = False
    bpy.data.images.remove(shared.pairs[1].cache_image)
    shared.pairs[0].cache_image = None
    bpy.data.images.remove(cache)

    section("unlinking a pair's input keeps the pair")
    tree.links.remove(stack_ops.feeding_link(stack_ops.below_input(shared, 1)))
    check(len(shared.outputs) == 2 and shared.outputs[1].is_linked,
          "the pair and its output stay")
    check(rows(tree, "Second") == [("Shared", 1, 0)], "and the stack below it is empty")
    tree.links.new(ground.outputs[0], stack_ops.below_input(shared, 1))

    section("a third pair")
    link_into_virtual(tree, under.outputs[0], shared)
    check(names(shared.inputs) == ["Color", "Color 2", "Color 3", "", "Mask"]
          and len(shared.pairs) == 3, f"goes after the others {names(shared.inputs)}")
    tree.links.remove(stack_ops.feeding_link(stack_ops.below_input(shared, 2)))

    section("removing a pair keeps the other pairs' links")
    second = shared.outputs[1].identifier
    tree.remove_layer_node(shared, "Color")
    check(rows(tree, "Color") == [("Under", 0, 0)], "the gap in its stack is closed")
    check(rows(tree, "Second") == [("Shared", 0, 0), ("Ground", 0, 0)],
          f"the other stack keeps the layer {rows(tree, 'Second')}")
    check(names(shared.inputs) == ["Color", "Color 2", "", "Mask"]
          and names(shared.outputs) == ["Color", "Color 2"] and len(shared.pairs) == 2,
          f"the pairs after it are named again {names(shared.inputs)}")
    check(shared.outputs[0].identifier == second,
          "and keep their identifiers, so their compiled nodes are kept")
    check(close(channel_pixel(tree, "Second"), BLUE), "the other stack still shows the layer")
    ids = compiled_ids(tree)
    check(f"{shared.uuid}:blend@{second}" in ids and f"{shared.uuid}:blend" not in ids,
          "under the name it compiled with before")
    check(close(channel_pixel(tree, "Color"), RED), "and the stack it left shows what was below")

    stack_ops.remove(tree, shared, 1)
    check(len(shared.outputs) == 1 and rows(tree, "Second")[0] == ("Shared", 0, 0),
          "removing a pair in no stack changes no stack")
    stack_ops.remove(tree, shared, 0)
    check(tree.nodes.get("Shared") is None and rows(tree, "Second") == [("Ground", 0, 0)],
          "the layer goes with its last pair")

    section("a folder's pairs share its content")
    tree = new_tree("Folder Pairs")
    add(tree, SOLID, "Ground", "Second", GREEN)
    box = add(tree, FOLDER, "Box", "Color")
    inside = add(tree, SOLID, "Inside", "Color", BLUE, target=box)
    inside.opacity = 0.5
    share_top(tree, box, "Second")
    check(identifiers(box.inputs) == ["Color", "Color 2", "__extend__", "Content Color", "Mask"],
          f"its pair inputs come first, then the virtual input and the shared inputs {identifiers(box.inputs)}")
    check(rows(tree, "Second") == [("Box", 1, 0), ("Inside", 0, 1), ("Ground", 0, 0)],
          f"the content shows in both stacks {rows(tree, 'Second')}")
    got = channel_pixel(tree, "Second")
    want = over(GREEN, BLUE, 0.5)
    check(close(got, want), f"and is blended over each of them {fmt(got)}")
    plan = stack_ops.removal(box, 0)
    check([(node.name, pair) for node, pair in plan.positions] == [("Box", 0)] and not plan.deleted,
          "removing one of two pairs leaves the content")
    tree.remove_layer_node(box, "Color")
    check(tree.nodes.get("Inside") is not None
          and rows(tree, "Second") == [("Box", 0, 0), ("Inside", 0, 1), ("Ground", 0, 0)],
          "and it does")
    check(identifiers(box.inputs)[0] == "Color 2" and stack_ops.below_input(box, 0) == box.inputs[0],
          "the pair left is the first input")

    section("a layer is never put in one channel twice")
    tree = new_tree("Repeats")
    add(tree, SOLID, "Ground", "Second", GREEN)
    box = add(tree, FOLDER, "Box", "Color")
    add(tree, SOLID, "Inside", "Color", RED, target=box)
    share_top(tree, box, "Second")
    mover = add(tree, SOLID, "Mover", "Color", BLUE)
    share_top(tree, mover, "Second")
    before = (rows(tree, "Color"), rows(tree, "Second"))
    check(stack_ops.repeats(tree) == 0, "no channel repeats a layer yet")
    result = tree.move_layer_node(mover, 'DOWN', 'MOVE_INTO_TOP', "Color")
    check(result == 'REPEAT' and (rows(tree, "Color"), rows(tree, "Second")) == before,
          "moving a layer into a folder that also sits in its other channel is refused")

    section("two channels can stack the same layers in opposite orders")
    tree = new_tree("Orders")
    add(tree, SOLID, "Under", "Color", GREEN)
    add(tree, SOLID, "Ground", "Second", GREEN)
    lower = add(tree, SOLID, "Lower", "Color", RED)
    upper = add(tree, SOLID, "Upper", "Color", BLUE)
    lower.opacity = upper.opacity = 0.5
    share_top(tree, lower, "Second")
    share_top(tree, upper, "Second")
    result = tree.move_layer_node(lower, 'UP', 'SKIP', "Second")
    check(result == 'MOVED' and rows(tree, "Second") == [("Lower", 1, 0), ("Upper", 1, 0), ("Ground", 0, 0)],
          f"Blender sees a cycle between the two nodes, but no pair is made from itself {result}")
    got, want = channel_pixel(tree, "Color"), over(over(GREEN, RED, 0.5), BLUE, 0.5)
    check(close(got, want), f"each channel blends its own order {fmt(got)}")
    got, want = channel_pixel(tree, "Second"), over(over(GREEN, BLUE, 0.5), RED, 0.5)
    check(close(got, want), f"in both of them {fmt(got)}")
    masked = add(tree, SOLID, "Masked", "Color")
    tree.links.new(upper.outputs[0], masked.inputs["Mask"])
    before = rows(tree, "Color")
    result = tree.move_layer_node(upper, 'UP', 'SKIP', "Color")
    check(result == 'LOOP' and rows(tree, "Color") == before,
          f"a move that makes a pair read its own output is still refused {result}")

    section("a filter layer keeps a result per pair")
    tree = new_tree("Filter Pairs")
    add(tree, SOLID, "Under", "Color", RED)
    add(tree, SOLID, "Ground", "Second", GREEN)
    node = tree.insert_layer_node(FILTER, channel_name="Color")
    share_top(tree, node, "Second")
    result = bpy.data.images.new("Pair Result", 8, 8)
    result.pixels.foreach_set(list(BLUE) * (8 * 8))
    result[derived.BUILD_KEY] = "built"
    result[derived.UV_MAP_KEY] = ""
    node.pairs[1].derived_image = result
    check(node.amount(0) == 0.0 and node.amount(1) == 1.0, "each pair is built on its own")
    check(close(channel_pixel(tree, "Color"), RED), "the unbuilt pair passes its stack through")
    check(close(channel_pixel(tree, "Second"), BLUE), "the built one shows its result")
    ids = compiled_ids(tree)
    check(f"{node.uuid}:result@Color 2" in ids and f"{node.uuid}:result" not in ids,
          "which only the built pair compiles")

    section("the pair list's buttons")
    bpy.context.scene.paint_system.active_node_tree = tree
    tree.nodes.active = node
    node.active_pair_index = 1
    key = stack_ops.pair_key(node, 1)
    layer_job._builds[key] = 2
    layer_job._restarts[key] = 1
    check(bpy.ops.paint_system.remove_pair() == {'FINISHED'}, "Remove Pair runs")
    check(stack_ops.pair_count(node) == 1 and rows(tree, "Second") == [("Ground", 0, 0)]
          and rows(tree, "Color")[0] == (node.name, 0, 0),
          "the selected pair leaves its stack, and the other stays")
    check("Pair Result" not in bpy.data.images, "its filtered image goes with it")
    check(key not in layer_job._builds and key not in layer_job._restarts,
          "and so do its refresh counts, so a new pair cannot inherit them")
    check(node.active_pair_index == 0, "the pair left is selected")
    check(not bpy.ops.paint_system.remove_pair.poll(), "the last pair cannot be removed")
    check(bpy.ops.paint_system.add_pair() == {'FINISHED'} and stack_ops.pair_count(node) == 2
          and node.active_pair_index == 1 and not node.outputs[1].is_linked,
          "Add Pair adds a pair in no stack, and selects it")

    section("Remove Layer keeps a layer only while another pair feeds something")
    tree.active_channel_index = [channel.name for channel in tree.channels].index("Color")
    name = node.name
    node.pairs[1].derived_image = bpy.data.images.new("Unlinked Result", 8, 8)
    plan = stack_ops.removal(node, 0)
    check(plan.positions == [(node, 0)] and plan.deleted == {name},
          "a pair in no stack does not keep the layer")
    check(len(layer_ops._filter_results(plan)) == 1, "the dialog counts that pair's filtered image")
    check(bpy.ops.paint_system.remove_layer('EXEC_DEFAULT') == {'FINISHED'} and name not in tree.nodes,
          "so Remove Layer deletes it")
    check("Unlinked Result" not in bpy.data.images, "with the filtered image of the pair in no stack")
    box = add(tree, FOLDER, "Box", "Color")
    keeper = add(tree, SOLID, "Keeper", "Color", BLUE, target=box)
    share_top(tree, keeper, "Second")
    add(tree, SOLID, "Inside", "Color", RED, target=box)
    tree.nodes.active = box
    plan = stack_ops.removal(box, 0)
    check([(node.name, pair) for node, pair in plan.positions] == [("Box", 0), ("Inside", 0), ("Keeper", 0)]
          and plan.deleted == {"Box", "Inside"},
          f"a folder takes its content, but not a layer another stack reads {plan}")
    check(bpy.ops.paint_system.remove_layer('EXEC_DEFAULT') == {'FINISHED'}
          and "Box" not in tree.nodes and "Inside" not in tree.nodes
          and rows(tree, "Color") == [("Under", 0, 0)]
          and rows(tree, "Second") == [("Keeper", 0, 0), ("Ground", 0, 0)],
          f"and does so {rows(tree, 'Color')} {rows(tree, 'Second')}")
    tree.nodes.active = keeper
    check(not bpy.ops.paint_system.remove_layer.poll(),
          "Remove Layer refuses a layer that is not in the active channel")
    for order in (("Masker", "Masked"), ("Masked", "Masker")):
        box = add(tree, FOLDER, "Box", "Color")
        # Each layer added into the folder lands on top of it.
        made = {name: add(tree, SOLID, name, "Color", target=box) for name in order}
        made["Masker"].add_pair()
        tree.links.new(made["Masker"].outputs[1], made["Masked"].inputs["Mask"])
        plan = stack_ops.removal(box, 0)
        tree.remove_layer_node(box, "Color")
        check(plan.deleted == {"Box", "Masked", "Masker"}
              and not {"Box", "Masked", "Masker"} & set(tree.nodes.keys()),
              f"a pair that only feeds a layer going too does not keep its layer, with {order[-1]} on top "
              f"{plan.deleted}")
    box = add(tree, FOLDER, "Box", "Color")
    inside = add(tree, SOLID, "Inside", "Color", target=box)
    inside.add_pair()
    tree.links.new(inside.outputs[1], box.inputs["Mask"])
    plan = stack_ops.removal(box, 0)
    tree.remove_layer_node(box, "Color")
    check(plan.deleted == {"Box", "Inside"} and not {"Box", "Inside"} & set(tree.nodes.keys()),
          f"a layer that only masks its own folder goes with the folder {plan.deleted}")
    box = add(tree, FOLDER, "Box", "Color")
    inner = add(tree, FOLDER, "Inner", "Color", target=box)
    add(tree, SOLID, "Deep", "Color", target=inner)
    twice = add(tree, SOLID, "Twice", "Color", target=box)
    # Wire Twice on top of Inner's content too, by hand, as the operators never would.
    link_into_virtual(tree, stack_ops.feeding_link(stack_ops.content_input(inner)).from_socket, twice)
    tree.links.new(twice.outputs[1], stack_ops.content_input(inner))
    plan = stack_ops.removal(box, 0)
    tree.remove_layer_node(box, "Color")
    check(plan.deleted == {"Box", "Inner", "Deep", "Twice"} and not plan.deleted & set(tree.nodes.keys()),
          f"a layer wired in twice leaves no gap that would strand the layers under it {plan.deleted}")
    box = add(tree, FOLDER, "Box", "Color")
    low = add(tree, SOLID, "Low", "Color", target=box)
    high = add(tree, SOLID, "High", "Color", target=box)
    share_top(tree, high, "Second")
    share_top(tree, low, "Second")
    plan = stack_ops.removal(box, 0)
    tree.remove_layer_node(box, "Color")
    check(plan.deleted == {"Box"} and rows(tree, "Color") == [("Under", 0, 0)]
          and rows(tree, "Second") == [("Low", 0, 0), ("High", 0, 0), ("Keeper", 0, 0), ("Ground", 0, 0)],
          f"layers in another stack stay, each kept by the one above it {plan.deleted} "
          f"{rows(tree, 'Second')}")
    beneath = add(tree, SOLID, "Beneath", "Color")
    box = add(tree, FOLDER, "Box", "Color")
    inside = add(tree, SOLID, "Inside", "Color", target=box)
    # Wire Beneath's second pair on top of the folder's content by hand.
    link_into_virtual(tree, inside.outputs[0], beneath)
    tree.links.new(beneath.outputs[1], stack_ops.content_input(box))
    tree.nodes.active = box
    plan = stack_ops.removal(box, 0)
    check(bpy.ops.paint_system.remove_layer('EXEC_DEFAULT') == {'FINISHED'} and plan.deleted == {"Box", "Inside"}
          and rows(tree, "Color") == [("Beneath", 0, 0), ("Under", 0, 0)],
          f"a layer right under a removed folder stays, since closing the gap hands it the folder's place "
          f"{plan.deleted} {rows(tree, 'Color')}")
    loop = add(tree, FOLDER, "Loop", "Color")
    loop.add_pair()
    tree.links.new(loop.outputs[1], stack_ops.content_input(loop))
    outer = add(tree, FOLDER, "Outer", "Color")
    other = add(tree, FOLDER, "Other", "Second")
    outer.add_pair()
    tree.links.new(outer.outputs[1], stack_ops.content_input(other))
    other.add_pair()
    tree.links.new(other.outputs[1], stack_ops.content_input(outer))
    plans = [stack_ops.removal(outer, 0), stack_ops.removal(loop, 0)]
    tree.remove_layer_node(outer, "Color")
    tree.remove_layer_node(loop, "Color")
    check([plan.deleted for plan in plans] == [set(), {"Loop"}] and "Loop" not in tree.nodes
          and rows(tree, "Color") == [("Beneath", 0, 0), ("Under", 0, 0)]
          and stack_ops.pair_count(outer) == 1 and stack_ops.pair_count(other) == 2,
          f"a folder wired into its own content, directly or through another folder, is removed without "
          f"looping {[plan.deleted for plan in plans]}")
    hide = add(tree, FOLDER, "Hide", "Color")
    hidden = add(tree, FOLDER, "Hidden", "Color", target=hide)
    muted = add(tree, SOLID, "Muted", "Color", target=hidden)
    muter = add(tree, SOLID, "Muter", "Color", target=hide)
    link_into_virtual(tree, muted.outputs[0], muter)
    tree.links.new(muter.outputs[1], stack_ops.content_input(hidden)).is_muted = True
    plan = stack_ops.removal(hide, 0)
    tree.remove_layer_node(hide, "Color")
    check(plan.deleted == {"Hide", "Hidden", "Muter"} and not plan.deleted & set(tree.nodes.keys())
          and stack_ops.pair_count(muted) == 1,
          f"a layer behind a muted link keeps its pair, since the removal never reaches it {plan.deleted}")
    bpy.context.scene.paint_system.active_node_tree = None
    tree = new_tree("Muted Below")
    add(tree, SOLID, "Under", "Color", RED)
    beneath = add(tree, SOLID, "Beneath", "Color")
    box = add(tree, FOLDER, "Box", "Color")
    inside = add(tree, SOLID, "Inside", "Color", target=box)
    link_into_virtual(tree, inside.outputs[0], beneath)
    tree.links.new(beneath.outputs[1], stack_ops.content_input(box))
    stack_ops.feeding_link(stack_ops.below_input(box)).is_muted = True
    plan = stack_ops.removal(box, 0)
    tree.remove_layer_node(box, "Color")
    check(plan.deleted == {"Box", "Inside", "Beneath"} and not plan.deleted & set(tree.nodes.keys()),
          f"but a muted link under the folder does not keep that layer, since closing the gap drops it "
          f"{plan.deleted}")

    section("removing a pair of a tree Blender evaluates")
    # A muted node passes its unlinked pair outputs through one pair
    # input. Removing that input once left internal links pointing at
    # freed memory, which crashed the next depsgraph update on 4.5 and
    # later.
    for bl_idname, extra, masked in ((SOLID, 1, False), (SOLID, 2, True), (FOLDER, 2, False)):
        tree = new_tree("Evaluated")
        under = add(tree, SOLID, "Under", "Color", RED)
        node = add(tree, bl_idname, "Solo", "Color")
        for _ in range(extra):
            node.add_pair()
        if masked:
            tree.links.new(under.outputs[0], node.inputs["Mask"])
        bpy.context.scene.paint_system.active_node_tree = tree
        tree.nodes.active = node
        bpy.context.view_layer.update()
        node.active_pair_index = 0
        bpy.ops.paint_system.remove_pair()
        bpy.context.view_layer.update()
        inputs = {socket.as_pointer() for socket in node.inputs}
        check(stack_ops.pair_count(node) == extra
              and all(link.from_socket.as_pointer() in inputs for link in node.internal_links),
              f"{node.ps_type} with {extra + 1} pairs{', masked' if masked else ''}: the first "
              f"pair goes, and every internal link starts at an input the node still has")
    bpy.context.scene.paint_system.active_node_tree = None

except Exception:
    traceback.print_exc()
    check(False, "unexpected exception")

finish("PAIRS TEST")
