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


# ── Clipping ─────────────────────────────────────────────────────────
#
# A clipped layer composites onto the content of the first unclipped layer
# below it, its base, instead of onto the stack. The base's blend then
# puts the base with its clipped layers over the stack below, as one. The
# base and the clipped layers under the top one therefore output the run
# so far, not the stack; the top one outputs the stack.


def layer_below(node):
    """The layer *node* composites over within its folder or channel, or None."""
    link = feeding_link(node.inputs['Color'])
    if link is None or link.from_socket.name != 'Color' or not is_layer(link.from_node):
        return None
    return link.from_node


def layer_above(node):
    """The layer compositing over *node* within its folder or channel, or None."""
    for link in node.outputs['Color'].links:
        consumer = link.to_node
        if (is_layer(consumer) and link.to_socket.name == 'Color'
                and feeding_link(link.to_socket) == link):
            return consumer
    return None


def clip_base(node):
    """The layer a clipped *node* clips to, or None when it composites normally.

    That is the first unclipped layer below it. Clipped layers with no
    layer below them, at the bottom of a folder or channel, composite as
    if they were not clipped.
    """
    if not node.is_clip:
        return None
    visited = {node.name}
    below = layer_below(node)
    while below is not None and below.is_clip:
        if below.name in visited:
            return None
        visited.add(below.name)
        below = layer_below(below)
    return below


def feeds_clip_run(node) -> bool:
    """Whether *node*'s outputs are a clip run that a layer above still blends.

    True for a base and for the clipped layers under the top of its run.
    """
    above = layer_above(node)
    if above is None or not above.is_clip:
        return False
    return not node.is_clip or clip_base(node) is not None


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


def insert_below(tree, node, target) -> None:
    """Place *node* directly below *target*, at the same level."""
    attach(tree, node, below_slot(target))


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


# ── Moving ───────────────────────────────────────────────────────────


@dataclass(eq=False)
class MoveOption:
    """One way to move a layer a row up or down.

    The layer is placed ``ABOVE``, ``BELOW``, ``INTO_TOP`` or ``INTO_BOTTOM``
    of *target*. *folder* is the folder the option is named after: the one
    entered or left, or the one ``MOVE_ADJACENT`` lands in (None for the
    top level).
    """
    action: str
    target: bpy.types.Node
    placement: str
    folder: bpy.types.Node | None


def movement_options(items: list[StackItem], node, direction: str) -> list[MoveOption]:
    """The moves one row ``'UP'`` or ``'DOWN'`` offers *node* in the stack *items*.

    Up, from directly below its folder: only out of it, above the folder
    (``MOVE_OUT``). Otherwise: into an empty folder on the row above, at
    its bottom (``MOVE_INTO``); into the folder holding the row above, when
    that is not the node's own (``MOVE_ADJACENT``); and above the previous
    sibling (``SKIP``).

    Down, the first row after the node's content decides. With no such row
    and a folder around the node: only out of it, below the folder
    (``MOVE_OUT_BOTTOM``). Otherwise: into that row at its top when it is a
    folder (``MOVE_INTO_TOP``); below it when it is the next sibling
    (``SKIP``); else out of the node's folder, below the folder
    (``MOVE_OUT_BOTTOM``), and when the row is further out than that,
    straight to its level above it (``MOVE_ADJACENT``).

    These are v2's options, except that v2 offered a ``SKIP`` that did
    nothing when there was no next sibling, and reached the next row's
    level directly with ``MOVE_ADJACENT`` where this also offers leaving
    one folder at a time.
    """
    position = next((i for i, item in enumerate(items) if item.node == node), None)
    if position is None:
        return []
    item = items[position]
    parent = item.parent.node if item.parent is not None else None
    options: list[MoveOption] = []

    if direction == 'UP':
        if position == 0:
            return options
        above = items[position - 1]
        if above.node == parent:
            return [MoveOption('MOVE_OUT', parent, 'ABOVE', parent)]
        if is_folder(above.node):
            options.append(MoveOption('MOVE_INTO', above.node, 'INTO_BOTTOM', above.node))
        if above.parent is not item.parent:
            options.append(MoveOption('MOVE_ADJACENT', above.node, 'BELOW',
                                      above.parent.node if above.parent is not None else None))
        siblings = [other for other in items if other.parent is item.parent]
        previous = siblings[item.index_in_parent - 1]
        options.append(MoveOption('SKIP', previous.node, 'ABOVE', None))
        return options

    end = position + 1
    while end < len(items) and items[end].level > item.level:
        end += 1
    following = items[end] if end < len(items) else None
    if following is None:
        if parent is not None:
            options.append(MoveOption('MOVE_OUT_BOTTOM', parent, 'BELOW', parent))
        return options
    if is_folder(following.node):
        options.append(MoveOption('MOVE_INTO_TOP', following.node, 'INTO_TOP', following.node))
    if following.parent is item.parent:
        options.append(MoveOption('SKIP', following.node, 'BELOW', None))
        return options
    options.append(MoveOption('MOVE_OUT_BOTTOM', parent, 'BELOW', parent))
    if following.parent is not item.parent.parent:
        options.append(MoveOption('MOVE_ADJACENT', following.node, 'ABOVE',
                                  following.parent.node if following.parent is not None else None))
    return options


def move(tree, channel_name: str, node, direction: str, action: str) -> bool:
    """Make the *action* move ``movement_options`` offers *node*; False if it offers no such move."""
    option = next((option for option in movement_options(stack(tree, channel_name), node, direction)
                   if option.action == action), None)
    if option is None:
        return False
    detach(tree, node)
    if option.placement == 'ABOVE':
        insert_above(tree, node, option.target)
    elif option.placement == 'BELOW':
        insert_below(tree, node, option.target)
    else:
        insert_into(tree, option.target, node, at_top=option.placement == 'INTO_TOP')
    return True


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
