import bpy
from bpy.types import NodeTree
from bpy.props import CollectionProperty, IntProperty, PointerProperty, StringProperty
from bpy.utils import register_classes_factory

from bpy_extras.node_utils import connect_sockets

from . import stack_ops
from ..common import blender_icon
from ..props.channel import PaintSystemChannel, channel_socket_specs, channel_alpha_name
from ..props.selection import PaintSystemSelection
from ..compiler.core import ensure_tree_uuid, mark_dirty, ps_trees, suspend_compile, tree_updated


GROUP_INPUT_ID = 'PaintSystemGroupInputNode'
GROUP_OUTPUT_ID = 'PaintSystemGroupOutputNode'
GROUP_LAYER_ID = 'PaintSystemGroupLayerNode'


def sync_sockets(sockets, specs) -> None:
    """Make *sockets* match *specs* (list of (name, socket_type, props)).

    A rename (same count, same types, new names) is applied in place so
    existing links survive. Anything else is reconciled by removing, adding
    and moving sockets. That includes a reorder of sockets of the same
    type: a moved socket keeps its links, while renaming in place would
    leave each link on the socket that now has another channel's name.
    """
    desired = [(name, socket_type) for name, socket_type, _ in specs]

    current = [(s.name, s.bl_idname) for s in sockets]
    same_types = len(current) == len(desired) and all(c[1] == d[1] for c, d in zip(current, desired))
    reordered = current != desired and sorted(current) == sorted(desired)
    if same_types and not reordered:
        for sock, (name, _) in zip(sockets, desired):
            if sock.name != name:
                sock.name = name
    else:
        desired_set = set(desired)
        for sock in list(sockets):
            if (sock.name, sock.bl_idname) not in desired_set:
                sockets.remove(sock)
        existing = {(s.name, s.bl_idname) for s in sockets}
        for name, socket_type, _ in specs:
            if (name, socket_type) not in existing:
                sockets.new(socket_type, name)
                existing.add((name, socket_type))
        for idx, (name, socket_type) in enumerate(desired):
            cur = next(i for i, s in enumerate(sockets)
                       if s.name == name and s.bl_idname == socket_type)
            if cur != idx:
                sockets.move(cur, idx)

    for sock, (_, _, props) in zip(sockets, specs):
        for key, value in props.items():
            try:
                setattr(sock, key, value)
            except (AttributeError, TypeError):
                pass


def sync_group_nodes_referencing(tree) -> None:
    """Re-sync every group layer node (in any tree) that wraps *tree*."""
    for ng in ps_trees():
        wrappers = [n for n in ng.nodes
                    if n.bl_idname == GROUP_LAYER_ID and n.node_tree == tree]
        for node in wrappers:
            node.sync_sockets()
        if wrappers and ng != tree:
            mark_dirty(ng)


def _get_active_layer_index(tree) -> int:
    active = tree.nodes.active
    return tree.nodes.find(active.name) if active is not None else -1


def _set_active_layer_index(tree, index: int) -> None:
    if not 0 <= index < len(tree.nodes) or not stack_ops.is_layer(tree.nodes[index]):
        return
    tree.activate_layer_node(tree.nodes[index])
    # Imported here: ``context`` imports this package.
    from ..context import update_active_image
    update_active_image(bpy.context)


class PaintSystemNodeTree(NodeTree):
    bl_idname = 'PaintSystemNodeTree'
    bl_label = 'Paint System'
    bl_icon = blender_icon('BRUSH_DATA')
    bl_use_group_interface = False

    version: IntProperty(name="Version", default=2)
    channels: CollectionProperty(type=PaintSystemChannel)
    active_channel_index: IntProperty(name="Active Channel", default=0)
    uuid: StringProperty(name="UUID")

    # The selection on the active layer image, as the operations that built
    # it (PS-091). Document data, so Blender's undo covers it and it is
    # saved with the file; the mask is derived and never stored.
    selection: PointerProperty(type=PaintSystemSelection)

    # The layer list shows ``nodes`` directly; its active row is the active
    # node, so nothing is stored that could disagree with the graph.
    active_layer_index: IntProperty(
        name="Active Layer",
        description="Index in nodes of the active layer",
        get=_get_active_layer_index,
        set=_set_active_layer_index,
    )

    # Build artifact. Owned by this tree, rebuilt by the compiler, never edited by hand.
    compiled: PointerProperty(
        type=bpy.types.NodeTree,
        name="Compiled Shader Group",
        description="Shader node group compiled from this tree",
    )

    # -- Blender callbacks ------------------------------------------------

    def update(self):
        # Called on link changes, node add/remove, and during file load and
        # undo (where the compiler holds off until the post handler).
        # Never mutate this tree here: Blender drops links created by the
        # callback and builds no sockets for new group nodes.
        tree_updated(self)

    # -- lifecycle --------------------------------------------------------

    @property
    def is_initialized(self) -> bool:
        return len(self.channels) > 0 or len(self.nodes) > 0

    def initialize(self):
        """Populate a brand-new tree: io nodes, a Color channel, passthrough link."""
        ensure_tree_uuid(self)
        with suspend_compile(self):
            self.ensure_io_nodes()
            if len(self.channels) == 0:
                self.create_channel('Color', 'COLOR')

    def ensure_io_nodes(self):
        if self.get_input_node() is None:
            node = self.nodes.new(GROUP_INPUT_ID)
            node.location = (-300, 0)
        output = self.get_output_node()
        if output is None:
            candidates = [n for n in self.nodes if n.bl_idname == GROUP_OUTPUT_ID]
            if candidates:
                candidates[0].is_active_output = True
            else:
                node = self.nodes.new(GROUP_OUTPUT_ID)
                node.location = (300, 0)
                node.is_active_output = True

    # -- channels ---------------------------------------------------------

    @property
    def active_channel(self):
        if 0 <= self.active_channel_index < len(self.channels):
            return self.channels[self.active_channel_index]
        return None

    def on_channels_changed(self):
        # Socket renames fire tree updates one socket at a time; compile
        # once all of them match the channels again.
        with suspend_compile(self):
            self.sync_group_node_sockets()
            sync_group_nodes_referencing(self)

    def sync_group_node_sockets(self):
        specs = channel_socket_specs(self.channels)
        for node in self.nodes:
            if node.bl_idname == GROUP_INPUT_ID:
                sync_sockets(node.outputs, specs)
            elif node.bl_idname == GROUP_OUTPUT_ID:
                sync_sockets(node.inputs, specs)

    def create_channel(self, name: str = "Channel", type: str = 'COLOR'):
        """Add a channel below the active one, make it active and pass its input straight through."""
        with suspend_compile(self):
            self.channels.add()
            last = len(self.channels) - 1
            # Clamped because the active index can be -1 with no channels,
            # and Blender 5.3+ raises IndexError on an out-of-range target.
            index = max(0, min(self.active_channel_index + 1, last))
            self.channels.move(last, index)
            self.active_channel_index = index
            # Looked up again: the item `add` returned now refers to the last slot.
            channel = self.channels[index]
            # Name and type are set once the channel is in place. Each update
            # syncs the sockets: the first adds the new channel's sockets in
            # its row and keeps the other channels' links.
            channel.name = name
            channel.type = type
            channel.ensure_uuid()
            input_node = self.get_input_node()
            output_node = self.get_output_node()
            if input_node and output_node:
                for sock_name in (channel.name, channel_alpha_name(channel.name)):
                    connect_sockets(input_node.outputs[sock_name], output_node.inputs[sock_name])
        return channel

    def can_move_active_channel(self, offset: int) -> bool:
        """Whether the active channel can move *offset* rows and stay in the list."""
        return self.active_channel is not None and 0 <= self.active_channel_index + offset < len(self.channels)

    def move_channel(self, index: int, new_index: int):
        """Move the channel at *index* to *new_index* and make it active."""
        # Clamped because Blender 5.3+ raises IndexError on an out-of-range target.
        new_index = max(0, min(new_index, len(self.channels) - 1))
        if index != new_index:
            self.channels.move(index, new_index)
        self.active_channel_index = new_index
        self.on_channels_changed()

    def delete_channel(self, index: int):
        """Remove the channel at *index*; the active index stays in range, or -1 once none are left."""
        self.channels.remove(index)
        self.active_channel_index = min(self.active_channel_index, len(self.channels) - 1)
        self.on_channels_changed()

    def delete_active_channel(self):
        self.delete_channel(self.active_channel_index)

    # -- node queries -----------------------------------------------------

    def get_output_node(self) -> bpy.types.Node | None:
        for node in self.nodes:
            if node.bl_idname == GROUP_OUTPUT_ID and node.is_active_output:
                return node
        return None

    def get_input_node(self) -> bpy.types.Node | None:
        for node in self.nodes:
            if node.bl_idname == GROUP_INPUT_ID:
                return node
        return None

    def _channel_name(self, channel_name: str | None) -> str | None:
        if channel_name is not None:
            return channel_name
        channel = self.active_channel
        return channel.name if channel else None

    def stack(self, channel_name: str | None = None) -> list[stack_ops.StackItem]:
        """Layers of *channel_name* (default: the active channel), top first.

        Each folder is followed by its content; see ``nodetree/stack_ops.py``.
        """
        channel_name = self._channel_name(channel_name)
        return stack_ops.stack(self, channel_name) if channel_name is not None else []

    def insert_layer_node(self, bl_idname: str, channel_name: str | None = None,
                          target: bpy.types.Node | None = None) -> bpy.types.Node:
        """Add a layer node to the channel's stack and make it active.

        With no *target* the layer goes on top. A folder *target* receives it
        at the top of its content; any other layer gets it directly above.
        """
        channel_name = self._channel_name(channel_name)
        with suspend_compile(self):
            stack_ops.repair_alpha_links(self)
            node = self.nodes.new(bl_idname)
            if target is not None and target.is_folder:
                stack_ops.insert_into(self, target, node)
            elif target is not None:
                stack_ops.insert_above(self, node, target)
            elif channel_name is not None:
                stack_ops.insert_on_top(self, node, channel_name)
            if channel_name is not None:
                stack_ops.arrange_stack(self, channel_name)
            self.activate_layer_node(node)
            self.reveal_layer_node(node, channel_name)
        return node

    def remove_layer_node(self, node: bpy.types.Node, channel_name: str | None = None) -> None:
        """Remove a layer (a folder with its content) and close the gap."""
        channel_name = self._channel_name(channel_name)
        with suspend_compile(self):
            stack_ops.repair_alpha_links(self)
            stack_ops.remove(self, node)
            if channel_name is not None:
                stack_ops.arrange_stack(self, channel_name)

    def move_layer_node(self, node: bpy.types.Node, direction: str, action: str,
                        channel_name: str | None = None) -> bool:
        """Move a layer a row ``'UP'`` or ``'DOWN'`` by one of ``stack_ops.movement_options``.

        A folder takes its content along. Returns False, changing nothing,
        when the move is not on offer.
        """
        channel_name = self._channel_name(channel_name)
        if channel_name is None:
            return False
        with suspend_compile(self):
            stack_ops.repair_alpha_links(self)
            moved = stack_ops.move(self, channel_name, node, direction, action)
            if moved:
                stack_ops.arrange_stack(self, channel_name)
                self.reveal_layer_node(node, channel_name)
        return moved

    def activate_layer_node(self, node: bpy.types.Node) -> None:
        """Make *node* the active and only selected node."""
        for other in self.nodes:
            other.select = other == node
        self.nodes.active = node

    def reveal_layer_node(self, node: bpy.types.Node, channel_name: str | None = None) -> None:
        """Expand every folder around *node* so its row shows in the layer list."""
        item = next((item for item in self.stack(channel_name) if item.node == node), None)
        parent = item.parent if item is not None else None
        while parent is not None:
            if not parent.node.is_expanded:
                parent.node.is_expanded = True
            parent = parent.parent


classes = (
    PaintSystemNodeTree,
)

owner = object()


def on_ps_nodetree_name_change():
    # Artifact names derive from the tree name; recompile picks up the rename.
    mark_dirty()


_register, _unregister = register_classes_factory(classes)


def subscribe_name_changes():
    """(Re)subscribe to tree renames. Loading a file drops every subscription."""
    bpy.msgbus.clear_by_owner(owner)
    bpy.msgbus.subscribe_rna(
        key=(PaintSystemNodeTree, "name"),
        owner=owner,
        args=(),
        notify=on_ps_nodetree_name_change,
    )


def register():
    _register()
    subscribe_name_changes()


def unregister():
    _unregister()
    bpy.msgbus.clear_by_owner(owner)
