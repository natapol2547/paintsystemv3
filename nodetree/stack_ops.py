"""Walk and edit the layer stack of a Paint System tree.

Entry points: ``stack`` walks it. ``attach``, ``detach``, ``insert_*``,
``remove`` and ``move`` edit it. ``arrange_stack`` lays it out.

The links are the stack, and each link carries one RGBA value. The top
layer feeds a channel's socket on the active Group Output. Each layer
takes the stack below it on its ``Color`` input. A folder takes the top
of its content on ``Content Color``. An input that takes a stack is a
slot: any input of a layer except its ``Mask``, or an input of the Group
Output or a group layer (``is_slot``). Reroutes and other nodes are never
slots. The bottom layer of a folder has nothing linked below it. The
compiler reads that as a transparent backdrop.

Edits keep one rule: a layer's ``Color`` output feeds at most one slot.
It may also feed masks, and edits leave those links alone. A move that
would loop such a link back into the layer is refused.

The socket accessors (``below_input``, ``stack_output``,
``content_input``, ``channel_input``) are the only code here that names
the sockets a stack runs through.

Callers batch edits in ``suspend_compile`` so the tree compiles once.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import bpy

from ..compiler.builder import same_value


COLUMN_WIDTH = 260
ROW_HEIGHT = 320


# ── Link index ───────────────────────────────────────────────────────
#
# ``NodeSocket.links`` is a Python property that scans every link of the
# tree. So a walk down the stack costs O(layers * links). ``link_index``
# maps a tree's links by socket in one pass. It keeps that map for the
# length of a read-only walk, and ``socket_links`` reads it.
#
# Indexes are keyed by tree pointer. So a nested compile of a child tree
# cannot overwrite the index a parent build installed. A tree without an
# index falls back to ``NodeSocket.links``. An index must never outlive a
# link edit, so only read-only walks may install one.

_link_indexes: dict[int, dict[int, tuple]] = {}


def socket_links(socket) -> tuple:
    """The links touching *socket*, from the installed index when there is one."""
    if _link_indexes:
        index = _link_indexes.get(socket.id_data.as_pointer())
        if index is not None:
            return index.get(socket.as_pointer(), ())
    return socket.links


def _build_link_index(tree) -> dict[int, tuple]:
    """Map every socket pointer of *tree* to its links.

    The links are in ``NodeSocket.links`` order. That property walks
    ``tree.links`` in order. For an input, it then sorts by
    ``multi_input_sort_id``, descending. Python's sort is stable, so links
    with the same id keep tree order. Sorting the inputs here gives the
    same order. Muted and invalid links are kept, exactly as the property
    returns them. ``feeding_link`` filters them itself, and
    ``consumer_input`` does not filter them.
    """
    by_socket: defaultdict[int, list] = defaultdict(list)
    inputs: set[int] = set()
    for link in tree.links:
        by_socket[link.from_socket.as_pointer()].append(link)
        key = link.to_socket.as_pointer()
        by_socket[key].append(link)
        inputs.add(key)
    for key in inputs:
        entry = by_socket[key]
        if len(entry) > 1:
            entry.sort(key=lambda link: link.multi_input_sort_id, reverse=True)
    return {key: tuple(entry) for key, entry in by_socket.items()}


class link_index:
    """Read *tree*'s links from an index inside the ``with`` block.

    Nested blocks on the same tree share the outermost block's index. Only
    read-only walks may install one.
    """

    def __init__(self, tree):
        self.tree = tree
        self.key = tree.as_pointer()
        self._owner = False

    def __enter__(self) -> link_index:
        if self.key not in _link_indexes:
            _link_indexes[self.key] = _build_link_index(self.tree)
            self._owner = True
        return self

    def __exit__(self, *exc) -> bool:
        if self._owner:
            _link_indexes.pop(self.key, None)
            self._owner = False
        return False


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


def tree_references(tree, target, _visited=None) -> bool:
    """True if *target* is *tree* or is nested anywhere inside it through group layers."""
    if tree is None:
        return False
    if tree == target:
        return True
    if _visited is None:
        _visited = set()
    key = tree.as_pointer()
    if key in _visited:
        return False
    _visited.add(key)
    for node in tree.nodes:
        if node.bl_idname == 'PaintSystemGroupLayerNode':
            if tree_references(node.node_tree, target, _visited):
                return True
    return False


def socket_named(sockets, name: str):
    """The socket in *sockets* called *name*, or None.

    ``sockets[name]`` and ``sockets.get(name)`` match identifiers before
    names. A channel socket renamed in place keeps its old name as its
    identifier, so those lookups can return another channel's socket.
    """
    return next((socket for socket in sockets if socket.name == name), None)


def feeding_link(socket) -> bpy.types.NodeLink | None:
    """The first unmuted link into *socket*, which the compiler reads.

    ``is_valid`` is not checked. Blender validates links after
    ``NodeTree.update``, so a link created by the edit being compiled still
    reads as invalid there. The walks guard against the cycles validation
    would flag.
    """
    for link in socket_links(socket):
        if not link.is_muted:
            return link
    return None


def producing_link(socket) -> bpy.types.NodeLink | None:
    """``feeding_link`` followed back through any reroutes in front of *socket*.

    The link returned comes from the node that makes the value, so a reroute
    placed by hand in the node editor changes nothing. None when *socket*,
    or a reroute on the way, is unlinked. Reroutes that loop back on
    themselves also read as unlinked.
    """
    link = feeding_link(socket)
    seen = set()
    while link is not None and link.from_node.bl_idname == 'NodeReroute':
        reroute = link.from_node
        if reroute.name in seen:
            return None
        seen.add(reroute.name)
        link = feeding_link(reroute.inputs[0])
    return link


# ── Walking ──────────────────────────────────────────────────────────


def below_input(node):
    """The input a layer takes the stack below it on."""
    return node.inputs['Color']


def stack_output(node):
    """The output a layer gives its stack on."""
    return node.outputs['Color']


def content_input(folder):
    """The input a folder takes the top of its content on."""
    return folder.inputs['Content Color']


def channel_sockets(sockets, channels):
    """Yield (channel, socket) for each of *channels* that has a socket in *sockets*.

    For the sockets of a Group Input, a Group Output or a group layer,
    which are found by name (see ``socket_named``).
    """
    for channel in channels:
        socket = socket_named(sockets, channel.name)
        if socket is not None:
            yield channel, socket


def channel_input(tree, channel_name: str):
    """The active Group Output's socket for *channel_name*, or None."""
    output = tree.get_output_node()
    return socket_named(output.inputs, channel_name) if output is not None else None


def is_slot(socket) -> bool:
    """Whether the input *socket* takes a stack.

    That is every input of a layer except its ``Mask``, and the channel
    sockets of a Group Output or a group layer. Other nodes, reroutes
    included, are never part of a stack, since the walks do not pass
    through them.
    """
    node = socket.node
    if is_layer(node):
        return socket.identifier != 'Mask'
    return node.bl_idname in {'PaintSystemGroupOutputNode', 'PaintSystemGroupLayerNode'}


def consumer_input(node):
    """The slot *node*'s stack output feeds, or None.

    Links into masks are skipped. A mask reads the layer's value without
    putting the layer in a stack.
    """
    for link in socket_links(stack_output(node)):
        if is_slot(link.to_socket):
            return link.to_socket
    return None


def layers_down_from(socket, visited: set[str]):
    """Layer nodes feeding *socket* and each other through their stack sockets, top first.

    *visited* is shared across a whole walk, folders included, so a cycle
    hand-made in the node editor ends the chain instead of looping.
    """
    while True:
        link = feeding_link(socket)
        if link is None:
            return
        node = link.from_node
        if not is_layer(node) or node.name in visited:
            return
        visited.add(node.name)
        yield node
        socket = below_input(node)


def stack(tree, channel_name: str) -> list[StackItem]:
    """Every layer in the channel, top first, each folder followed by its content."""
    top = channel_input(tree, channel_name)
    items: list[StackItem] = []
    if top is None:
        return items
    visited: set[str] = set()

    def walk(socket, level, parent):
        for index, node in enumerate(layers_down_from(socket, visited)):
            item = StackItem(node, level, parent, index)
            items.append(item)
            if is_folder(node):
                walk(content_input(node), level + 1, item)

    with link_index(tree):
        walk(top, 0, None)
    return items


def descendants(folder) -> list[bpy.types.Node]:
    """Every layer inside *folder*, nested folders included."""
    found = []
    visited: set[str] = set()

    def walk(node):
        for child in layers_down_from(content_input(node), visited):
            found.append(child)
            if is_folder(child):
                walk(child)

    with link_index(folder.id_data):
        walk(folder)
    return found


# ── Clipping ─────────────────────────────────────────────────────────
#
# A clipped layer composites onto the content of its base, which is the
# first unclipped layer below it. It does not composite onto the stack.
# The base's blend then puts the base, with its clipped layers, over the
# stack below as one. So the base and the clipped layers under the top
# one output the run so far, not the stack. Only the top one outputs the
# stack.


def layer_below(node):
    """The layer *node* composites over within its folder or channel, or None."""
    link = feeding_link(below_input(node))
    if link is None or not is_layer(link.from_node):
        return None
    return link.from_node


def layer_above(node):
    """The layer compositing over *node* within its folder or channel, or None."""
    for link in socket_links(stack_output(node)):
        consumer = link.to_node
        if (is_layer(consumer) and link.to_socket == below_input(consumer)
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
    """Put the detached *node* into the input *slot*.

    Whatever fed the slot now feeds *node*.
    """
    link = feeding_link(slot)
    if link is not None:
        tree.links.new(link.from_socket, below_input(node))
    tree.links.new(stack_output(node), slot)


def detach(tree, node) -> None:
    """Take *node* out of the stack and close the gap behind it.

    Links from *node* into masks stay.
    """
    slot = consumer_input(node)
    link = feeding_link(below_input(node))
    below = link.from_socket if link else None

    doomed = list(below_input(node).links)
    doomed += [link for link in stack_output(node).links if is_slot(link.to_socket)]
    for link in doomed:
        tree.links.remove(link)
    if slot is not None and below is not None:
        tree.links.new(below, slot)


def insert_on_top(tree, node, channel_name: str) -> None:
    slot = channel_input(tree, channel_name)
    if slot is not None:
        attach(tree, node, slot)


def insert_above(tree, node, target) -> None:
    """Place *node* directly above *target*, at the same level."""
    slot = consumer_input(target)
    if slot is not None:
        attach(tree, node, slot)
    else:
        tree.links.new(stack_output(target), below_input(node))


def insert_below(tree, node, target) -> None:
    """Place *node* directly below *target*, at the same level."""
    attach(tree, node, below_input(target))


def insert_into(tree, folder, node, *, at_top: bool = True) -> None:
    """Place *node* inside *folder*, at the top or the bottom of its content."""
    content = [] if at_top else list(layers_down_from(content_input(folder), set()))
    attach(tree, node, below_input(content[-1]) if content else content_input(folder))


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

    *placement* puts the layer ``ABOVE``, ``BELOW``, ``INTO_TOP`` or
    ``INTO_BOTTOM`` of *target*. *folder* is the folder the option is named
    after. That is the folder entered or left, or the one ``MOVE_ADJACENT``
    lands in. It is None for the top level.
    """
    action: str
    target: bpy.types.Node
    placement: str
    folder: bpy.types.Node | None


def movement_options(items: list[StackItem], node, direction: str) -> list[MoveOption]:
    """Return the moves one row ``'UP'`` or ``'DOWN'`` for *node* in *items*.

    Up:

    - Directly below its own folder, the only move is out of it, above the
      folder (``MOVE_OUT``).
    - Otherwise the moves are:

      - into an empty folder on the row above, at its bottom
        (``MOVE_INTO``);
      - into the folder that holds the row above, when that is not the
        node's own folder (``MOVE_ADJACENT``);
      - above the previous sibling (``SKIP``).

    Down, the first row after the node's content decides:

    - With no such row but a folder around the node, the only move is out
      of it, below the folder (``MOVE_OUT_BOTTOM``).
    - Otherwise the moves are:

      - into that row at its top, when it is a folder (``MOVE_INTO_TOP``);
      - below that row, when it is the next sibling (``SKIP``);
      - when it is not the next sibling, out of the node's folder, below
        the folder (``MOVE_OUT_BOTTOM``);
      - when it is further out than the node's folder, also straight to
        the row's level, above it (``MOVE_ADJACENT``).

    These match v2's options, with two deliberate differences:

    - v2 offered a ``SKIP`` that did nothing when there was no next
      sibling.
    - v2 reached the next row's level directly with ``MOVE_ADJACENT``.
      This also offers leaving one folder at a time.
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


def reads_from(node, source) -> bool:
    """Whether *node* reads *source* through any of its inputs, masks included.

    Follows every link upstream, through reroutes, until it runs out.
    """
    seen: set[str] = set()
    pending = [node]
    with link_index(node.id_data):
        while pending:
            for socket in pending.pop().inputs:
                link = producing_link(socket)
                if link is None:
                    continue
                if link.from_node == source:
                    return True
                if link.from_node.name not in seen:
                    seen.add(link.from_node.name)
                    pending.append(link.from_node)
    return False


def move(tree, channel_name: str, node, direction: str, action: str) -> bool:
    """Make the *action* move that ``movement_options`` offers *node*.

    Returns False if no such move is offered, or if it would loop a link
    into a mask back into *node*. That happens when a layer moves above a
    layer it masks: it would read the masked layer's result and feed its
    mask at the same time. The compiler cannot order that, so the layer is
    put back where it was.
    """
    option = next((option for option in movement_options(stack(tree, channel_name), node, direction)
                   if option.action == action), None)
    if option is None:
        return False
    # Checking after the move sees the real links. Predicting the loop
    # would mean simulating every kind of placement.
    home = consumer_input(node)
    looped = reads_from(node, node)
    detach(tree, node)
    if option.placement == 'ABOVE':
        insert_above(tree, node, option.target)
    elif option.placement == 'BELOW':
        insert_below(tree, node, option.target)
    else:
        insert_into(tree, option.target, node, at_top=option.placement == 'INTO_TOP')
    if looped or not reads_from(node, node):
        return True
    detach(tree, node)
    attach(tree, node, home)
    return False


def _move_node(node, x: float, y: float) -> None:
    """Write ``node.location`` only when that would move the node.

    Every RNA write tags the tree, and the materials that use it, for an
    update. That update takes time linear in the size of the tree. So
    putting a node back where it already is costs as much as a real move.
    A stack edit lays out every layer again, and most of them keep their
    place.
    """
    location = node.location
    if same_value(location[0], x) and same_value(location[1], y):
        return
    node.location = (x, y)


def arrange_stack(tree, channel_name: str) -> None:
    """Lay the stack out from the Group Output, right to left.

    Folder content sits above its folder, one row higher per nesting level.
    """
    output = tree.get_output_node()
    if output is None:
        return
    items = stack(tree, channel_name)
    x, y = output.location
    for index, item in enumerate(items):
        _move_node(item.node, x - COLUMN_WIDTH * (index + 1),
                   y + ROW_HEIGHT * item.level)
    group_input = tree.get_input_node()
    if group_input is not None:
        _move_node(group_input, x - COLUMN_WIDTH * (len(items) + 1), y)
