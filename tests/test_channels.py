"""Channels of a Paint System tree: add, rename, move, remove, and their options (PS-005).

Each check goes through the operators or the channel properties, the way
the channel list in the sidebar uses them, and then looks at the channel
order, the active channel and the Group Input and Output sockets that
follow the channels. The options are checked on the compiled node group,
on a material that uses it, and on baked pixels.

Run:  blender -b --factory-startup --python tests/test_channels.py
"""
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, close, finish, fmt, guarded, import_from, over,  # noqa: E402
                     pixel_at, register_addon, section)

register_addon()
ops = bpy.ops.paint_system
compile_tree = import_from("compiler.core").compile_tree
build_ir = import_from("compiler.core").build_ir
link_tree_to_material = import_from("ops.node_tree_ops").link_tree_to_material


def new_tree(name):
    """A new tree the operators act on, through the scene's tree field."""
    tree = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    tree.initialize()
    bpy.context.scene.paint_system.active_node_tree = tree
    return tree


def names(tree):
    return [channel.name for channel in tree.channels]


def sockets_follow(tree):
    """Whether the Group Input outputs and Group Output inputs match the channels, in order.

    Each channel is one RGBA socket; the compiled node group splits it into
    the channel and its alpha.
    """
    want = names(tree)
    return ([s.name for s in tree.get_input_node().outputs] == want
            and [s.name for s in tree.get_output_node().inputs] == want)


def passes_through(tree):
    """Whether every Group Output input is linked from the Group Input output of its name.

    True for a tree with no layers, where each channel is its input.
    """
    input_node = tree.get_input_node()
    return all(socket.is_linked and socket.links[0].from_node == input_node
               and socket.links[0].from_socket.name == socket.name
               for socket in tree.get_output_node().inputs)


def fed_by(tree, socket_name):
    """The node linked into the Group Output input called *socket_name*, or None.

    Found by name: ``inputs[socket_name]`` matches identifiers first, and a
    socket renamed in place keeps its old name as its identifier.
    """
    socket = next(s for s in tree.get_output_node().inputs if s.name == socket_name)
    return socket.links[0].from_node if socket.is_linked else None


def test_add():
    section("adding a channel")
    tree = new_tree("Channels Add")
    check(names(tree) == ["Color"] and tree.active_channel_index == 0, f"a new tree has one channel {names(tree)}")

    check(ops.add_channel('EXEC_DEFAULT', name="Rough", type='FLOAT') == {'FINISHED'}, "Add Channel runs")
    check(names(tree) == ["Color", "Rough"] and tree.active_channel_index == 1,
          f"the new channel goes below the active one and becomes active {names(tree)}")
    check(tree.channels[1].type == 'FLOAT' and tree.channels[1].uuid, "it has its type and a uuid")
    check(sockets_follow(tree), "the group sockets follow the new channel")
    check(fed_by(tree, "Rough") == tree.get_input_node(), "the new channel passes its input straight through")
    check(all(s.bl_idname == 'NodeSocketColor' for s in tree.get_output_node().inputs),
          "a float channel's socket is RGBA like every other")

    tree.active_channel_index = 0
    ops.add_channel('EXEC_DEFAULT', name="Mask")
    check(names(tree) == ["Color", "Mask", "Rough"] and tree.active_channel_index == 1,
          f"with the first channel active the new one goes second {names(tree)}")
    check(sockets_follow(tree) and passes_through(tree),
          "every channel, the new one and those below it, still passes its input through")

    ops.add_channel('EXEC_DEFAULT', name="Color")
    check(names(tree) == ["Color", "Mask", "Color 1", "Rough"],
          f"a name another channel has gets a number {names(tree)}")
    check(sockets_follow(tree), "the group sockets follow")

    tree.active_channel_index = 0
    created = tree.create_channel("Height", 'FLOAT')
    check(created.name == "Height" and created.type == 'FLOAT' and tree.active_channel == created,
          f"create_channel returns the channel it added ({created.name})")
    check(passes_through(tree), "and the channels below keep their links")


def test_rename():
    section("renaming a channel")
    tree = new_tree("Channels Rename")
    ops.add_channel('EXEC_DEFAULT', name="Rough")
    tree.channels[1].name = "Roughness"
    check(names(tree) == ["Color", "Roughness"] and sockets_follow(tree),
          f"a rename renames the sockets {names(tree)}")
    tree.channels[1].name = "Color"
    check(names(tree) == ["Color", "Color 1"], f"a rename to another channel's name gets a number {names(tree)}")
    check(sockets_follow(tree), "the group sockets follow")

    # The renamed sockets keep "Color" as their identifier, so a new
    # channel called "Color" has sockets with other identifiers.
    tree = new_tree("Channels Rename Reuse")
    layer = tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Color")
    tree.channels[0].name = "Paint"
    ops.add_channel('EXEC_DEFAULT', name="Color")
    check(names(tree) == ["Paint", "Color"] and sockets_follow(tree),
          f"a new channel can take a renamed channel's old name {names(tree)}")
    check(fed_by(tree, "Paint") == layer and fed_by(tree, "Color") == tree.get_input_node(),
          "the renamed channel keeps its layer and the new one passes its input through")
    check([item.node for item in tree.stack("Paint")] == [layer] and tree.stack("Color") == [],
          "each channel's stack reads from its own socket")
    second = tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Color")
    check(fed_by(tree, "Color") == second and fed_by(tree, "Paint") == layer,
          "a layer added to the new channel leaves the renamed one alone")

    tree = new_tree("Channels Rename Material")
    nt, bsdf, group = material_for(tree)
    rgb = nt.nodes.new('ShaderNodeRGB')
    nt.links.new(rgb.outputs[0], group.inputs["Color"])
    nt.links.new(group.outputs["Color Alpha"], bsdf.inputs['Alpha'])
    identifiers = [s.identifier for s in (*group.inputs, *group.outputs)]
    tree.channels[0].name = "Paint"
    compile_tree(tree)
    check([s.name for s in group.outputs] == ["Paint", "Paint Alpha"]
          and [s.identifier for s in (*group.inputs, *group.outputs)] == identifiers,
          f"the compiled sockets are renamed in place {[s.name for s in group.outputs]}")
    check(group.inputs["Paint"].is_linked and bsdf.inputs['Base Color'].is_linked and bsdf.inputs['Alpha'].is_linked,
          "so the material keeps its links to them")


def test_type():
    section("changing a channel's type")
    tree = new_tree("Channels Type")
    layer = tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Color")
    _nt, bsdf, group = material_for(tree)
    identifier = group.outputs['Color'].identifier
    tree.channels[0].type = 'FLOAT'
    check(fed_by(tree, "Color") == layer and sockets_follow(tree), "a type change keeps the channel's links")
    check(tree.get_output_node().inputs[0].bl_idname == 'NodeSocketColor', "the channel's socket stays RGBA")
    compile_tree(tree)
    outputs = {s.name: s.bl_socket_idname for s in tree.compiled.interface.items_tree if s.in_out == 'OUTPUT'}
    check(outputs == {"Color": 'NodeSocketFloat', "Color Alpha": 'NodeSocketFloat'},
          f"the compiled node group takes the new type, with the alpha beside it {outputs}")
    check(group.outputs['Color'].identifier == identifier and bsdf.inputs['Base Color'].is_linked,
          "the compiled socket is retyped in place, so the material keeps its link")


def interface(tree, in_out):
    """The compiled node group's sockets on one side, by name, in order."""
    return {s.name: s for s in tree.compiled.interface.items_tree
            if s.item_type == 'SOCKET' and s.in_out == in_out}


def material_for(tree):
    """A material that runs *tree*, with its Principled BSDF and the group node."""
    material = bpy.data.materials.new(tree.name)
    group = link_tree_to_material(material, tree)
    bsdf = next(n for n in material.node_tree.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled')
    return material.node_tree, bsdf, group


def test_defaults():
    section("a new channel's options follow its type")
    tree = new_tree("Channels Defaults")
    color = tree.channels["Color"]
    check(color.use_alpha and color.color_space == 'COLOR', "the first channel is a colour with alpha")
    ops.add_channel('EXEC_DEFAULT', name="Rough", type='FLOAT')
    rough = tree.channels["Rough"]
    check(not rough.use_alpha and rough.color_space == 'NONCOLOR', "a float channel is data without alpha")
    compile_tree(tree)
    for side in ('INPUT', 'OUTPUT'):
        check(list(interface(tree, side)) == ["Color", "Color Alpha", "Rough"],
              f"only the colour channel has an alpha {side.lower()} {list(interface(tree, side))}")
    alpha = interface(tree, 'INPUT')["Color Alpha"]
    check(alpha.subtype == 'FACTOR' and (alpha.min_value, alpha.max_value) == (0.0, 1.0),
          "the alpha input is a slider from 0 to 1")

    ops.add_layer('EXEC_DEFAULT', layer_type='IMAGE', resolution='1024')
    check(tree.nodes.active.image.colorspace_settings.name == 'Non-Color',
          "an image layer added to a Non-Color channel gets a Non-Color image")
    tree.active_channel_index = 0
    ops.add_layer('EXEC_DEFAULT', layer_type='IMAGE', resolution='1024')
    check(tree.nodes.active.image.colorspace_settings.name == 'sRGB',
          "and one added to a Color channel an sRGB image")


def test_use_alpha():
    section("Use Alpha adds and removes only the alpha sockets")
    tree = new_tree("Channels Alpha")
    tree.create_channel("Rough", 'FLOAT')
    nt, bsdf, group = material_for(tree)
    value = nt.nodes.new('ShaderNodeValue')
    nt.links.new(group.outputs['Color Alpha'], bsdf.inputs['Alpha'])
    nt.links.new(group.outputs['Rough'], bsdf.inputs['Roughness'])
    nt.links.new(value.outputs[0], group.inputs['Rough'])

    def others_linked():
        return (bsdf.inputs['Base Color'].is_linked and bsdf.inputs['Roughness'].is_linked
                and group.inputs['Rough'].is_linked)

    color = tree.channels["Color"]
    color.use_alpha = False
    check([s.name for s in group.outputs] == ["Color", "Rough"]
          and [s.name for s in group.inputs] == ["Color", "Rough"],
          f"turned off, the group node loses the alpha sockets {[s.name for s in group.outputs]}")
    check(others_linked(), "and the material keeps its other links")
    color.use_alpha = True
    check("Color Alpha" in group.outputs and "Color Alpha" in group.inputs,
          "turned back on, the alpha sockets come back")
    check(not bsdf.inputs['Alpha'].is_linked, "as new sockets, so the old alpha link is gone")
    check(others_linked(), "and the other links are still there")


def test_opaque_base():
    section("without alpha the layers stack over an opaque base")
    tree = new_tree("Channels Opaque")
    layer = tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Color")
    layer.fill_color = (1.0, 0.0, 0.0, 1.0)
    layer.opacity = 0.5
    blue = (0.0, 0.0, 1.0, 1.0)
    rgba = pixel_at(bake_group(tree.compiled, inputs={"Color": blue}, size=4), 0.5, 0.5, size=4)
    # The alpha input starts at 0, so the base is see-through.
    want = over(blue[:3] + (0.0,), layer.fill_color, 0.5)
    check(close(rgba, want), f"with alpha the half-opaque layer is over nothing {fmt(rgba)}, want {fmt(want)}")
    tree.channels["Color"].use_alpha = False
    # Without an alpha output only the colour is baked.
    rgba = pixel_at(bake_group(tree.compiled, alpha="Color", inputs={"Color": blue}, size=4), 0.5, 0.5, size=4)
    want = over(blue, layer.fill_color, 0.5)
    check(close(rgba[:3], want[:3]), f"without alpha it is over the input colour {fmt(rgba[:3])}, want {fmt(want)}")
    # Unlinked from the Group Input, the layer is over nothing, and the
    # result is only half opaque. The output lays it over the input.
    tree.links.remove(layer.inputs['Color'].links[0])
    # A link edit compiles from a timer, which a headless script never runs.
    compile_tree(tree)
    rgba = pixel_at(bake_group(tree.compiled, alpha="Color", inputs={"Color": blue}, size=4), 0.5, 0.5, size=4)
    check(close(rgba[:3], want[:3]),
          f"a result that is not opaque is flattened onto the input colour {fmt(rgba[:3])}, want {fmt(want)}")

    # Over a see-through base, Multiply would give the layer's own colour.
    tree = new_tree("Channels Opaque Multiply")
    tree.channels["Color"].use_alpha = False
    layer = tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Color")
    layer.fill_color = (1.0, 0.5, 0.0, 1.0)
    layer.blend_mode = 'MULTIPLY'
    rgba = pixel_at(bake_group(tree.compiled, alpha="Color", inputs={"Color": (0.5, 0.5, 1.0, 1.0)}, size=4),
                    0.5, 0.5, size=4)
    check(close(rgba[:3], (0.5, 0.25, 0.0)), f"a Multiply layer acts on the opaque base {fmt(rgba[:3])}")

    tree = new_tree("Channels Empty")
    tree.create_channel("Rough", 'FLOAT')
    output = next(s for s in tree.get_output_node().inputs if s.name == "Rough")
    tree.links.remove(output.links[0])
    compile_tree(tree)
    rgba = pixel_at(bake_group(tree.compiled, color="Rough", alpha="Rough", inputs={"Rough": 0.5}, size=4),
                    0.5, 0.5, size=4)
    check(close(rgba[:3], (0.5, 0.5, 0.5)),
          f"an empty channel is nothing over the base, so its input comes out {fmt(rgba[:3])}")


def test_cache_hash():
    section("a layer cache goes stale when the options change what it was baked from")
    tree = new_tree("Channels Cache")
    rough = tree.create_channel("Rough", 'FLOAT')
    layer = tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Color")

    def layer_hash():
        return build_ir(tree).ctx.subtree_hash(layer)

    before = layer_hash()
    tree.channels["Color"].use_alpha = False
    check(layer_hash() != before, "Use Alpha changes the hash of a layer above the Group Input")
    tree.channels["Color"].use_alpha = True
    check(layer_hash() == before, "and turning it back restores the hash")
    rough.range_min = 0.2
    rough.use_range = True
    check(layer_hash() != before, "so does Limit Range, which sets the default a cache bakes")
    rough.use_range = False
    check(layer_hash() == before, "and turning it off restores the hash")
    tree.channels["Color"].name = "Base"
    tree.move_channel(0, 1)
    check(layer_hash() == before, "a rename and a move change no pixels, so the hash stays")


def test_range():
    section("Limit Range turns a float input into a slider")
    tree = new_tree("Channels Range")
    rough = tree.create_channel("Rough", 'FLOAT')
    nt, _bsdf, group = material_for(tree)
    nt.links.new(nt.nodes.new('ShaderNodeValue').outputs[0], group.inputs['Rough'])
    identifier = interface(tree, 'INPUT')["Rough"].identifier
    rough.range_min = 0.8
    rough.range_max = 0.2
    rough.use_range = True
    socket = interface(tree, 'INPUT')["Rough"]
    check(socket.subtype == 'FACTOR' and close((socket.min_value, socket.max_value), (0.2, 0.8), 1e-6),
          f"the input is a slider, with min and max in order ({socket.min_value}, {socket.max_value})")
    check(socket.identifier == identifier and group.inputs['Rough'].is_linked,
          "it is the same socket, and the material's link to it survives")
    rough.use_range = False
    socket = interface(tree, 'INPUT')["Rough"]
    check(socket.subtype == 'NONE' and socket.min_value < -1e38 and socket.max_value > 1e38,
          "turned off, the input is a plain float again")
    check(socket.identifier == identifier and group.inputs['Rough'].is_linked,
          "still the same socket, still linked")


def test_move():
    section("moving a channel")
    tree = new_tree("Channels Move")
    ops.add_channel('EXEC_DEFAULT', name="Rough")
    ops.add_channel('EXEC_DEFAULT', name="Mask")
    check(names(tree) == ["Color", "Rough", "Mask"], f"three channels {names(tree)}")
    # All three are Color channels, so a move swaps sockets of the same
    # type. The layer shows whether the links move with their channel.
    layer = tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Color")
    check(fed_by(tree, "Color") == layer, "the Color channel has a layer")

    tree.active_channel_index = 0
    check(not ops.move_channel_up.poll() and ops.move_channel_down.poll(),
          "the first channel can move down only")
    check(ops.move_channel_down() == {'FINISHED'}, "Move Channel Down runs")
    check(names(tree) == ["Rough", "Color", "Mask"] and tree.active_channel_index == 1,
          f"the channel moved down and stays active {names(tree)}")
    check(sockets_follow(tree), "the group sockets follow the new order")
    check(fed_by(tree, "Color") == layer and fed_by(tree, "Rough") == tree.get_input_node(),
          "the layer moved with its channel, not onto the channel now in its place")

    tree.active_channel_index = 2
    check(ops.move_channel_up.poll() and not ops.move_channel_down.poll(),
          "the last channel can move up only")
    check(ops.move_channel_up() == {'FINISHED'}, "Move Channel Up runs")
    check(names(tree) == ["Rough", "Mask", "Color"] and tree.active_channel_index == 1,
          f"the channel moved up and stays active {names(tree)}")
    check(sockets_follow(tree), "the group sockets follow the new order")
    check(fed_by(tree, "Color") == layer and fed_by(tree, "Mask") == tree.get_input_node(),
          "moving the channel next to it leaves the layer in place")

    single = new_tree("Channels Single")
    check(names(single) == ["Color"] and not ops.move_channel_up.poll() and not ops.move_channel_down.poll(),
          "a lone channel cannot move")


def test_remove():
    section("removing a channel")
    tree = new_tree("Channels Remove")
    ops.add_channel('EXEC_DEFAULT', name="Rough")
    check(tree.active_channel_index == 1, "the last channel is active")
    check(ops.remove_channel('EXEC_DEFAULT') == {'FINISHED'}, "Remove Channel runs")
    check(names(tree) == ["Color"] and tree.active_channel_index == 0,
          f"the active channel moves up to the new last one {names(tree)}")
    check(sockets_follow(tree), "the group sockets follow")

    ops.remove_channel('EXEC_DEFAULT')
    check(names(tree) == [] and tree.active_channel is None, "the last channel can go too")
    check(sockets_follow(tree), "the group sockets are gone with it")
    check(not ops.remove_channel.poll(), "with no channel left there is nothing to remove")

    ops.add_channel('EXEC_DEFAULT', name="Color")
    check(names(tree) == ["Color"] and tree.active_channel_index == 0,
          f"a channel added to an empty tree is the active one {names(tree)}")
    check(sockets_follow(tree), "the group sockets follow")


guarded(test_add)
guarded(test_rename)
guarded(test_type)
guarded(test_defaults)
guarded(test_use_alpha)
guarded(test_opaque_base)
guarded(test_cache_hash)
guarded(test_range)
guarded(test_move)
guarded(test_remove)
finish("CHANNELS TEST")
