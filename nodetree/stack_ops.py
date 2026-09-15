"""The layer stack of a Paint System tree: walking it and editing it.

The stack is the graph. The top layer feeds a channel's socket on the
active Group Output; every layer takes the stack below it on its ``Color``
and ``Alpha`` inputs, and a folder takes the top of its content on
``Content Color`` and ``Content Alpha``. Such a pair of input sockets is a
slot. The bottom layer of a folder has nothing linked below it, which the
compiler reads as a transparent backdrop.

Invariants the edits keep:

- a layer's ``Color`` output feeds at most one slot;
- a slot's alpha input is linked from the alpha partner of whatever feeds
  its colour input, and is unlinked when the colour input is.

Hand edits in the node editor can break the second one. The compiler
reads alpha from the colour link regardless (``CompileContext.source``),
and ``repair_alpha_links`` tidies the links before the next stack edit.
It cannot run from ``NodeTree.update``: Blender drops links created there.

Callers batch edits in ``suspend_compile`` so the tree compiles once.
"""
from __future__ import annotations

from dataclasses import dataclass

import bpy

from ..props.channel import channel_alpha_name


LAYER_SOCKET_PAIRS = {'Color': 'Alpha', 'Content Color': 'Content Alpha'}

COLUMN_WIDTH = 260
ROW_HEIGHT = 320


@dataclass(eq=False)
class StackItem:
    node: bpy.types.Node
    level: int
    parent: StackItem | None
    index_in_parent: int


def is_layer(node) -> bool:
    return getattr(node, 'is_layer_node', False)


def is_folder(node) -> bool:
    return getattr(node, 'is_folder', False)


def alpha_partner(node, color_name: str) -> str | None:
    """Name of the socket that carries the alpha of *node*'s socket *color_name*."""
    if is_layer(node):
        return LAYER_SOCKET_PAIRS.get(color_name)
    if color_name.endswith(" Alpha"):
        return None
    return channel_alpha_name(color_name)


def paired_color_input(socket):
    """The colour input whose alpha partner is the input *socket*, or None."""
    if socket.is_output:
        return None
    node = socket.node
    for color_in in node.inputs:
        if color_in != socket and alpha_partner(node, color_in.name) == socket.name:
            return color_in
    return None


def feeding_link(socket) -> bpy.types.NodeLink | None:
    """The link the compiler reads for *socket*: the first unmuted one.

    ``is_valid`` is not checked. Blender validates links after
    ``NodeTree.update``, so a link created by the edit being compiled still
    reads as invalid there. The walks guard against the cycles validation
    would flag.
    """
    for link in socket.links:
        if not link.is_muted:
            return link
    return None


# ── Walking ──────────────────────────────────────────────────────────


def channel_slot(tree, channel_name: str):
    output = tree.get_output_node()
    if output is None or channel_name not in output.inputs:
        return None
    return output.inputs[channel_name], output.inputs.get(channel_alpha_name(channel_name))


def content_slot(folder):
    return folder.inputs['Content Color'], folder.inputs['Content Alpha']


def below_slot(node):
    return node.inputs['Color'], node.inputs['Alpha']


def consumer_slot(node):
    """The slot *node*'s ``Color`` output feeds, or None."""
    for link in node.outputs['Color'].links:
        partner = alpha_partner(link.to_node, link.to_socket.name)
        return link.to_socket, link.to_node.inputs.get(partner) if partner else None
    return None


def _layers_down_from(socket, visited: set[str]):
    """Layer nodes feeding *socket* and each other through ``Color``, top first."""
    while True:
        link = feeding_link(socket)
        if link is None or link.from_socket.name != 'Color':
            return
        node = link.from_node
        if not is_layer(node) or node.name in visited:
            return
        visited.add(node.name)
        yield node
        socket = node.inputs['Color']


def stack(tree, channel_name: str) -> list[StackItem]:
    """Every layer in the channel, top first, each folder followed by its content."""
    slot = channel_slot(tree, channel_name)
    items: list[StackItem] = []
    if slot is None:
        return items
    visited: set[str] = set()

    def walk(socket, level, parent):
        for index, node in enumerate(_layers_down_from(socket, visited)):
            item = StackItem(node, level, parent, index)
            items.append(item)
            if is_folder(node):
                walk(node.inputs['Content Color'], level + 1, item)

    walk(slot[0], 0, None)
    return items


def descendants(folder) -> list[bpy.types.Node]:
    """Every layer inside *folder*, nested folders included."""
    found = []
    visited: set[str] = set()

    def walk(node):
        for child in _layers_down_from(node.inputs['Content Color'], visited):
            found.append(child)
            if is_folder(child):
                walk(child)

    walk(folder)
    return found


# ── Editing ──────────────────────────────────────────────────────────


def attach(tree, node, slot) -> None:
    """Put the detached *node* into *slot*; whatever fed the slot now feeds *node*."""
    color_in, alpha_in = slot
    link = feeding_link(color_in)
    below_color = link.from_socket if link else None
    link = feeding_link(alpha_in) if alpha_in is not None else None
    below_alpha = link.from_socket if link else None

    if below_color is not None:
        tree.links.new(below_color, node.inputs['Color'])
    if below_alpha is not None:
        tree.links.new(below_alpha, node.inputs['Alpha'])
    tree.links.new(node.outputs['Color'], color_in)
    if alpha_in is not None:
        tree.links.new(node.outputs['Alpha'], alpha_in)


def detach(tree, node) -> None:
    """Take *node* out of the stack and close the gap behind it."""
    slot = consumer_slot(node)
    link = feeding_link(node.inputs['Color'])
    below_color = link.from_socket if link else None
    link = feeding_link(node.inputs['Alpha'])
    below_alpha = link.from_socket if link else None

    for socket in (node.outputs['Color'], node.outputs['Alpha'],
                   node.inputs['Color'], node.inputs['Alpha']):
        for link in list(socket.links):
            tree.links.remove(link)
    if slot is None:
        return
    color_in, alpha_in = slot
    if below_color is not None:
        tree.links.new(below_color, color_in)
    if below_alpha is not None and alpha_in is not None:
        tree.links.new(below_alpha, alpha_in)


def insert_on_top(tree, node, channel_name: str) -> None:
    slot = channel_slot(tree, channel_name)
    if slot is not None:
        attach(tree, node, slot)


def insert_above(tree, node, target) -> None:
    """Place *node* directly above *target*, at the same level."""
    slot = consumer_slot(target)
    if slot is not None:
        attach(tree, node, slot)
    else:
        tree.links.new(target.outputs['Color'], node.inputs['Color'])
        tree.links.new(target.outputs['Alpha'], node.inputs['Alpha'])


def insert_into(tree, folder, node, *, at_top: bool = True) -> None:
    """Place *node* inside *folder*, at the top or the bottom of its content."""
    content = [] if at_top else list(_layers_down_from(folder.inputs['Content Color'], set()))
    attach(tree, node, below_slot(content[-1]) if content else content_slot(folder))


def remove(tree, node) -> None:
    """Remove *node*, and a folder's content with it, closing the gap."""
    content = descendants(node) if is_folder(node) else []
    detach(tree, node)
    for child in content:
        tree.nodes.remove(child)
    tree.nodes.remove(node)


def repair_alpha_links(tree) -> int:
    """Make every slot's alpha input follow its colour input. Returns the number of fixes."""
    fixes = 0
    for node in tree.nodes:
        for color_in in node.inputs:
            partner = alpha_partner(node, color_in.name)
            alpha_in = node.inputs.get(partner) if partner else None
            if alpha_in is None:
                continue
            link = feeding_link(color_in)
            if link is None:
                expected = None
            else:
                source_partner = alpha_partner(link.from_node, link.from_socket.name)
                expected = link.from_node.outputs.get(source_partner) if source_partner else None
                if expected is None:
                    continue
            current = feeding_link(alpha_in)
            if current is not None and expected is not None and current.from_socket == expected:
                continue
            if current is None and expected is None:
                continue
            for stale in list(alpha_in.links):
                tree.links.remove(stale)
            if expected is not None:
                tree.links.new(expected, alpha_in)
            fixes += 1
    return fixes


def arrange_stack(tree, channel_name: str) -> None:
    """Lay the stack out right to left from the Group Output, folder content above its folder."""
    output = tree.get_output_node()
    if output is None:
        return
    items = stack(tree, channel_name)
    x, y = output.location
    for index, item in enumerate(items):
        item.node.location = (x - COLUMN_WIDTH * (index + 1), y + ROW_HEIGHT * item.level)
    group_input = tree.get_input_node()
    if group_input is not None:
        group_input.location = (x - COLUMN_WIDTH * (len(items) + 1), y)
