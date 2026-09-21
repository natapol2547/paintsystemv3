"""Channels of a Paint System tree: add, rename, move and remove.

Each check goes through the operators or the channel properties, the way
the channel list in the sidebar uses them, and then looks at the channel
order, the active channel and the Group Input and Output sockets that
follow the channels.

Run:  blender -b --factory-startup --python tests/test_channels.py
"""
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, guarded, register_addon, section  # noqa: E402

register_addon()
ops = bpy.ops.paint_system


def new_tree(name):
    """A new tree the operators act on, through the scene's tree field."""
    tree = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    tree.initialize()
    bpy.context.scene.paint_system.active_node_tree = tree
    return tree


def names(tree):
    return [channel.name for channel in tree.channels]


def expected_sockets(channel_names):
    return [name for channel in channel_names for name in (channel, f"{channel} Alpha")]


def sockets_follow(tree):
    """Whether the Group Input outputs and Group Output inputs match the channels, in order."""
    want = expected_sockets(names(tree))
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
    inputs = tree.get_output_node().inputs
    check(all(inputs[name].is_linked and inputs[name].links[0].from_node == tree.get_input_node()
              for name in ("Rough", "Rough Alpha")),
          "the new channel passes its input straight through")

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
guarded(test_move)
guarded(test_remove)
finish("CHANNELS TEST")
