"""Walk and edit the layer stack of a Paint System tree.

Entry points: ``stack`` walks it. ``attach``, ``detach``, ``insert_*``,
``remove`` and ``move`` edit it. ``arrange_stack`` lays it out.

The links are the stack, and each link carries one RGBA value. The top
layer feeds a channel's socket on the active Group Output. Each layer
takes the stack below it on an input and gives the new stack on the
output paired with that input. A folder takes the top of its content on
``Content Color``. An input that takes a stack is a slot: any input of a
layer except its ``Mask`` and its virtual input, or an input of the Group
Output or a group layer (``is_slot``). Reroutes and other nodes are never
slots. The bottom layer of a folder has nothing linked below it. The
compiler reads that as a transparent backdrop.

A layer can sit in several stacks, one per pair of sockets (see "Pairs"
below). So a place in a stack is a layer and a pair, and every walk and
edit here takes both. The pair defaults to the first one, which every
layer has.

Edits keep one rule: each pair's output feeds at most one slot. It may
also feed masks, and edits leave those links alone. A move that would
loop such a link back into the layer, or put a layer in one channel
twice, is refused.

The socket accessors (``below_input``, ``stack_output``,
``content_input``, ``channel_input``) are the only code here that names
the sockets a stack runs through.

Callers batch edits in ``suspend_compile`` so the tree compiles once.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import NamedTuple

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
    """One row of a stack: *node*, sitting in the stack through its *pair*."""
    node: bpy.types.Node
    pair: int
    level: int
    parent: StackItem | None
    index_in_parent: int


class Position(NamedTuple):
    """A place in a stack: a layer and the pair it sits there through."""
    node: bpy.types.Node
    pair: int


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


# ── Pairs ────────────────────────────────────────────────────────────
#
# A layer takes each stack it sits in on one input and gives it back on
# one output. That input and output are a pair. Pairs are numbered by
# position: the n-th pair input goes with the n-th output. The first
# pair input is always the layer's first input. A folder's
# ``Content Color`` and ``Mask`` are shared by every pair.
#
# Identifiers cannot match an input to its output: inputs and outputs
# number their identifiers separately, and Blender gives a new socket the
# number of one removed earlier. Every pair socket is also named after
# its pair, not after the stack, so names cannot match them either.
#
# What a pair builds for its stack, a cache or a filter result, is kept
# in the layer's ``pairs`` collection, at the pair's position.

CONTENT = 'Content Color'
MASK = 'Mask'
VIRTUAL_SOCKET = 'NodeSocketVirtual'


def is_virtual(socket) -> bool:
    """Whether *socket* is a layer's virtual input, which a new link turns into a pair."""
    return socket.bl_idname == VIRTUAL_SOCKET


def is_pair_input(socket) -> bool:
    """Whether the layer input *socket* takes a stack for one of the layer's pairs."""
    return socket.identifier not in {CONTENT, MASK} and not is_virtual(socket)


def pair_inputs(node) -> list:
    """The inputs of *node*'s pairs, first pair first."""
    return [socket for socket in node.inputs if is_pair_input(socket)]


def pair_count(node) -> int:
    """How many pairs the layer *node* has. Its outputs are exactly the pair outputs."""
    return len(node.outputs)


def pair_of_input(socket) -> int | None:
    """The pair the layer input *socket* belongs to, or None for a shared or virtual input."""
    if not is_pair_input(socket):
        return None
    if len(socket.node.outputs) == 1:
        return 0
    return next(index for index, other in enumerate(pair_inputs(socket.node))
                if other.identifier == socket.identifier)


def pair_of_output(socket) -> int:
    """The pair the layer output *socket* belongs to."""
    outputs = socket.node.outputs
    if len(outputs) == 1:
        return 0
    return next(index for index, other in enumerate(outputs)
                if other.identifier == socket.identifier)


def virtual_input(node):
    """The layer's virtual input, or None while ``init`` has not added it yet."""
    return next((socket for socket in node.inputs if is_virtual(socket)), None)


def link_source(link) -> Position:
    """Where *link* comes from: a layer and the pair of the output it leaves.

    Any other node has one pair, 0, which covers all of its outputs.
    """
    node = link.from_node
    return Position(node, pair_of_output(link.from_socket) if is_layer(node) else 0)


def pair_reads(node, pair: int = 0) -> list:
    """The inputs the output of *node*'s *pair* is made from.

    For a layer, that is the pair's own input and the shared inputs,
    ``Content Color`` and ``Mask``, in socket order. Any other node reads
    all of its inputs.
    """
    if not is_layer(node):
        return list(node.inputs)
    below = below_input(node, pair)
    return [socket for socket in node.inputs
            if not is_virtual(socket) and (socket == below or not is_pair_input(socket))]


def pair_role(role: str, node, pair: int) -> str:
    """*role* made unique to *node*'s *pair*, for IR nodes a layer emits once per pair.

    The output a layer is made with has the identifier ``Color``, and its
    roles stay as they are, so a layer with one pair compiles as it did
    before pairs existed. Other pairs add their output's identifier.
    Unlike the position, it does not change when an earlier pair is
    removed, so the other pairs' compiled nodes are kept.
    """
    identifier = node.outputs[pair].identifier
    return role if identifier == 'Color' else f"{role}@{identifier}"


def pair_key(node, pair: int) -> str:
    """A key for *node*'s *pair* that lasts while the pair exists, for in-memory tables."""
    return f"{node.uuid}:{node.outputs[pair].identifier}"


# ── Walking ──────────────────────────────────────────────────────────


def below_input(node, pair: int = 0):
    """The input a layer takes the stack below it on, for its *pair*."""
    if pair == 0:
        return node.inputs[0]
    return pair_inputs(node)[pair]


def stack_output(node, pair: int = 0):
    """The output a layer gives its stack on, for its *pair*."""
    return node.outputs[pair]


def content_input(folder):
    """The input a folder takes the top of its content on. Every pair of the folder shows it."""
    return folder.inputs[CONTENT]


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

    That is every input of a layer except its ``Mask`` and its virtual
    input, and the channel sockets of a Group Output or a group layer.
    Other nodes, reroutes included, are never part of a stack, since the
    walks do not pass through them.
    """
    node = socket.node
    if is_layer(node):
        return socket.identifier != MASK and not is_virtual(socket)
    return node.bl_idname in {'PaintSystemGroupOutputNode', 'PaintSystemGroupLayerNode'}


def consumer_input(node, pair: int = 0):
    """The slot the output of *node*'s *pair* feeds, or None.

    Links into masks are skipped. A mask reads the layer's value without
    putting the layer in a stack.
    """
    return _consumer_slot(stack_output(node, pair))


def _consumer_slot(socket):
    for link in socket_links(socket):
        if is_slot(link.to_socket):
            return link.to_socket
    return None


def output_channel(tree, socket):
    """The channel whose stack the output *socket* feeds, or None when it reaches none.

    Follows the stack up the way the compiler reads it, to a socket of
    the active Group Output. A layer is left by the output paired with the
    input the stack came in on. A folder's content is shown by every pair
    of the folder, so it can reach several channels; the first one found,
    in pair order, is returned. A group layer is left by its output of the
    same name as the input.
    """
    output = tree.get_output_node()
    if output is None:
        return None
    with link_index(tree):
        return _output_channel(tree, output, socket, set())


def _output_channel(tree, output, socket, visited: set):
    while True:
        slot = _consumer_slot(socket)
        if slot is None:
            return None
        node = slot.node
        if node == output:
            return next((channel for channel in tree.channels if channel.name == slot.name), None)
        key = (node.name, slot.identifier)
        if key in visited:
            return None
        visited.add(key)
        if is_layer(node):
            pair = pair_of_input(slot)
            if pair is None:  # a folder's content
                for folder_output in node.outputs:
                    channel = _output_channel(tree, output, folder_output, visited)
                    if channel is not None:
                        return channel
                return None
            socket = stack_output(node, pair)
        elif node.bl_idname == 'PaintSystemGroupLayerNode':
            socket = socket_named(node.outputs, slot.name)
        else:
            return None  # a Group Output that is not the active one
        if socket is None:
            return None


def channel_of(tree, node, pair: int = 0):
    """The channel the stack of *node*'s *pair* feeds, or None when it reaches no output."""
    return output_channel(tree, stack_output(node, pair))


def layers_down_from(socket, visited: set):
    """The positions feeding *socket* and each other through their pairs, top first.

    Each layer is left by the input paired with the output the walk came
    in on. *visited* holds (node name, pair) and is shared across a whole
    walk, folders included, so a cycle hand-made in the node editor ends
    the chain instead of looping.
    """
    while True:
        link = feeding_link(socket)
        if link is None:
            return
        node = link.from_node
        if not is_layer(node):
            return
        pair = pair_of_output(link.from_socket)
        key = (node.name, pair)
        if key in visited:
            return
        visited.add(key)
        yield Position(node, pair)
        socket = below_input(node, pair)


def stack(tree, channel_name: str) -> list[StackItem]:
    """Every layer in the channel, top first, each folder followed by its content.

    A layer wired into one channel twice by hand is listed twice. Moves
    refuse to make that (see ``move``).
    """
    top = channel_input(tree, channel_name)
    items: list[StackItem] = []
    if top is None:
        return items
    visited: set = set()

    def walk(socket, level, parent):
        for index, (node, pair) in enumerate(layers_down_from(socket, visited)):
            item = StackItem(node, pair, level, parent, index)
            items.append(item)
            if is_folder(node):
                walk(content_input(node), level + 1, item)

    with link_index(tree):
        walk(top, 0, None)
    return items


# ── Clipping ─────────────────────────────────────────────────────────
#
# A clipped layer composites onto the content of its base, which is the
# first unclipped layer below it. It does not composite onto the stack.
# The base's blend then puts the base, with its clipped layers, over the
# stack below as one. So the base and the clipped layers under the top
# one output the run so far, not the stack. Only the top one outputs the
# stack.
#
# Clipping is worked out per pair. ``is_clip`` is a setting, so it is
# shared, but the layers around a layer differ from one stack to the
# next. A layer can be a base in one stack and blend normally in another.


def layer_below(node, pair: int = 0) -> Position | None:
    """The position *node*'s *pair* composites over within its folder or channel, or None."""
    link = feeding_link(below_input(node, pair))
    if link is None or not is_layer(link.from_node):
        return None
    return Position(link.from_node, pair_of_output(link.from_socket))


def layer_above(node, pair: int = 0) -> Position | None:
    """The position compositing over *node*'s *pair* within its folder or channel, or None."""
    for link in socket_links(stack_output(node, pair)):
        consumer = link.to_node
        if not is_layer(consumer):
            continue
        consumer_pair = pair_of_input(link.to_socket)
        if consumer_pair is not None and feeding_link(link.to_socket) == link:
            return Position(consumer, consumer_pair)
    return None


def clip_base(node, pair: int = 0) -> Position | None:
    """The position a clipped *node*'s *pair* clips to, or None when it composites normally.

    That is the first unclipped layer below it. Clipped layers with no
    layer below them, at the bottom of a folder or channel, composite as
    if they were not clipped.
    """
    if not node.is_clip:
        return None
    visited = {(node.name, pair)}
    below = layer_below(node, pair)
    while below is not None and below.node.is_clip:
        key = (below.node.name, below.pair)
        if key in visited:
            return None
        visited.add(key)
        below = layer_below(*below)
    return below


def feeds_clip_run(node, pair: int = 0) -> bool:
    """Whether the output of *node*'s *pair* is a clip run that a layer above still blends.

    True for a base and for the clipped layers under the top of its run.
    """
    above = layer_above(node, pair)
    if above is None or not above.node.is_clip:
        return False
    return not node.is_clip or clip_base(node, pair) is not None


# ── Editing ──────────────────────────────────────────────────────────


def attach(tree, node, slot, pair: int = 0) -> None:
    """Put *node*'s detached *pair* into the input *slot*.

    Whatever fed the slot now feeds that pair.
    """
    link = feeding_link(slot)
    if link is not None:
        tree.links.new(link.from_socket, below_input(node, pair))
    tree.links.new(stack_output(node, pair), slot)


def detach(tree, node, pair: int = 0) -> None:
    """Take *node*'s *pair* out of its stack and close the gap behind it.

    The node's other pairs, and links from this pair into masks, stay.
    """
    slot = consumer_input(node, pair)
    link = feeding_link(below_input(node, pair))
    below = link.from_socket if link else None

    doomed = list(below_input(node, pair).links)
    doomed += [link for link in stack_output(node, pair).links if is_slot(link.to_socket)]
    for link in doomed:
        tree.links.remove(link)
    if slot is not None and below is not None:
        tree.links.new(below, slot)


def insert_on_top(tree, node, channel_name: str, pair: int = 0) -> None:
    slot = channel_input(tree, channel_name)
    if slot is not None:
        attach(tree, node, slot, pair)


def insert_above(tree, node, target: Position, pair: int = 0) -> None:
    """Place *node*'s *pair* directly above the position *target*, at the same level."""
    slot = consumer_input(*target)
    if slot is not None:
        attach(tree, node, slot, pair)
    else:
        tree.links.new(stack_output(*target), below_input(node, pair))


def insert_below(tree, node, target: Position, pair: int = 0) -> None:
    """Place *node*'s *pair* directly below the position *target*, at the same level."""
    attach(tree, node, below_input(*target), pair)


def insert_into(tree, folder, node, *, at_top: bool = True, pair: int = 0) -> None:
    """Place *node*'s *pair* inside *folder*, at the top or the bottom of its content."""
    content = [] if at_top else list(layers_down_from(content_input(folder), set()))
    attach(tree, node, below_input(*content[-1]) if content else content_input(folder), pair)


def removal(node, pair: int = 0) -> list[Position]:
    """The positions ``remove(tree, node, pair)`` takes out, in the order it takes them.

    A layer is deleted with its last pair, and a folder's content goes
    with the folder. A layer inside it that sits in other stacks too only
    loses the pairs inside. The operators read this to say what goes
    before anything is removed.
    """
    left: dict[str, int] = {}
    found: list[Position] = []

    def take(node, pair):
        found.append(Position(node, pair))
        count = left.setdefault(node.name, pair_count(node))
        left[node.name] = count - 1
        if count == 1 and is_folder(node):
            for child, child_pair in list(layers_down_from(content_input(node), set())):
                take(child, child_pair)

    with link_index(node.id_data):
        take(node, pair)
    return found


def remove(tree, node, pair: int = 0) -> None:
    """Take *node*'s *pair* out of its stack and close the gap.

    The node is deleted with its last pair. Deleting a folder takes its
    content with it, as ``removal`` describes. The content is taken top
    first and read again after each step, because removing a pair
    renumbers the pairs after it.
    """
    if pair_count(node) == 1 and is_folder(node):
        while True:
            link = feeding_link(content_input(node))
            if link is None or not is_layer(link.from_node):
                break
            remove(tree, link.from_node, pair_of_output(link.from_socket))
    detach(tree, node, pair)
    if pair_count(node) > 1:
        node.remove_pair(pair)
    else:
        tree.nodes.remove(node)


# ── Moving ───────────────────────────────────────────────────────────


@dataclass(eq=False)
class MoveOption:
    """One way to move a layer a row up or down.

    *placement* puts the layer ``ABOVE``, ``BELOW``, ``INTO_TOP`` or
    ``INTO_BOTTOM`` of *target*, which sits in the stack through its
    *target_pair*. *folder* is the folder the option is named after. That
    is the folder entered or left, or the one ``MOVE_ADJACENT`` lands in.
    It is None for the top level.
    """
    action: str
    target: bpy.types.Node
    target_pair: int
    placement: str
    folder: bpy.types.Node | None


def _option(action: str, target: StackItem, placement: str, folder: StackItem | None) -> MoveOption:
    return MoveOption(action, target.node, target.pair, placement,
                      folder.node if folder is not None else None)


def movement_options(items: list[StackItem], node, direction: str) -> list[MoveOption]:
    """Return the moves one row ``'UP'`` or ``'DOWN'`` for *node*'s first row in *items*.

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
    parent = item.parent
    options: list[MoveOption] = []

    if direction == 'UP':
        if position == 0:
            return options
        above = items[position - 1]
        if above is parent:
            return [_option('MOVE_OUT', parent, 'ABOVE', parent)]
        if is_folder(above.node):
            options.append(_option('MOVE_INTO', above, 'INTO_BOTTOM', above))
        if above.parent is not item.parent:
            options.append(_option('MOVE_ADJACENT', above, 'BELOW', above.parent))
        siblings = [other for other in items if other.parent is item.parent]
        previous = siblings[item.index_in_parent - 1]
        options.append(_option('SKIP', previous, 'ABOVE', None))
        return options

    end = position + 1
    while end < len(items) and items[end].level > item.level:
        end += 1
    following = items[end] if end < len(items) else None
    if following is None:
        if parent is not None:
            options.append(_option('MOVE_OUT_BOTTOM', parent, 'BELOW', parent))
        return options
    if is_folder(following.node):
        options.append(_option('MOVE_INTO_TOP', following, 'INTO_TOP', following))
    if following.parent is item.parent:
        options.append(_option('SKIP', following, 'BELOW', None))
        return options
    options.append(_option('MOVE_OUT_BOTTOM', parent, 'BELOW', parent))
    if following.parent is not item.parent.parent:
        options.append(_option('MOVE_ADJACENT', following, 'ABOVE', following.parent))
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


def repeats(tree) -> int:
    """How many rows repeat a layer already listed in the same channel's stack.

    Blender checks for cycles node by node, so a layer can sit in one
    channel twice without one, for example inside a linked folder and
    again below it. The layer list shows a layer once per channel, so the
    editing operators never make that.
    """
    count = 0
    for channel in tree.channels:
        names = [item.node.name for item in stack(tree, channel.name)]
        count += len(names) - len(set(names))
    return count


def move(tree, channel_name: str, node, direction: str, action: str) -> bool:
    """Make the *action* move that ``movement_options`` offers *node* in *channel_name*.

    Only the pair that sits in that channel's stack moves. Returns False
    if no such move is offered, or if the move would loop a link into a
    mask back into *node*, or put a layer in one channel twice.

    A loop happens when a layer moves above a layer it masks: it would
    read the masked layer's result and feed its mask at the same time. The
    compiler cannot order that. A repeat happens when a layer that also
    sits in another channel moves into a folder linked into that channel.
    Either way, the layer is put back where it was.
    """
    items = stack(tree, channel_name)
    option = next((option for option in movement_options(items, node, direction)
                   if option.action == action), None)
    if option is None:
        return False
    pair = next(item.pair for item in items if item.node == node)
    target = Position(option.target, option.target_pair)
    # Checking after the move sees the real links. Predicting either
    # problem would mean simulating every kind of placement.
    home = consumer_input(node, pair)
    looped = reads_from(node, node)
    repeated = repeats(tree)
    detach(tree, node, pair)
    if option.placement == 'ABOVE':
        insert_above(tree, node, target, pair)
    elif option.placement == 'BELOW':
        insert_below(tree, node, target, pair)
    else:
        insert_into(tree, option.target, node, at_top=option.placement == 'INTO_TOP', pair=pair)
    if (looped or not reads_from(node, node)) and repeats(tree) <= repeated:
        return True
    detach(tree, node, pair)
    attach(tree, node, home, pair)
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
    A layer in several stacks is placed where the last stack laid out puts
    its first row.
    """
    output = tree.get_output_node()
    if output is None:
        return
    items = stack(tree, channel_name)
    x, y = output.location
    placed = set()
    for index, item in enumerate(items):
        if item.node.name in placed:
            continue
        placed.add(item.node.name)
        _move_node(item.node, x - COLUMN_WIDTH * (index + 1),
                   y + ROW_HEIGHT * item.level)
    group_input = tree.get_input_node()
    if group_input is not None:
        _move_node(group_input, x - COLUMN_WIDTH * (len(items) + 1), y)
