import bpy
import uuid
from bpy.types import NodeTree
from bpy.props import CollectionProperty, IntProperty, PointerProperty, StringProperty
from bpy.utils import register_classes_factory

from bpy_extras.node_utils import connect_sockets

from ..props.channel import PaintSystemChannel, channel_socket_specs, channel_alpha_name
from ..props.collection_manager import CollectionManager
from ..compiler.core import mark_dirty


GROUP_INPUT_ID = 'PaintSystemGroupInputNode'
GROUP_OUTPUT_ID = 'PaintSystemGroupOutputNode'
GROUP_LAYER_ID = 'PaintSystemGroupLayerNode'


def sync_sockets(sockets, specs) -> None:
    """Make *sockets* match *specs* (list of (name, socket_type, props)).

    A pure rename (same count, same types, one name changed) is applied in
    place so existing links survive. Anything else is reconciled by
    removing/adding/moving sockets.
    """
    desired = [(name, socket_type) for name, socket_type, _ in specs]

    current = [(s.name, s.bl_idname) for s in sockets]
    if len(current) == len(desired) and all(c[1] == d[1] for c, d in zip(current, desired)):
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
    for ng in bpy.data.node_groups:
        if ng.bl_idname != 'PaintSystemNodeTree':
            continue
        for node in ng.nodes:
            if node.bl_idname == GROUP_LAYER_ID and node.node_tree == tree:
                node.sync_sockets()
        if ng != tree and any(
                n.bl_idname == GROUP_LAYER_ID and n.node_tree == tree for n in ng.nodes):
            mark_dirty(ng)


class PaintSystemNodeTree(NodeTree):
    bl_idname = 'PaintSystemNodeTree'
    bl_label = 'Paint System'
    bl_icon = 'BRUSH_DATA'
    bl_use_group_interface = False

    version: IntProperty(name="Version", default=2)
    channels: CollectionProperty(type=PaintSystemChannel)
    active_channel_index: IntProperty(name="Active Channel", default=0)
    uuid: StringProperty(name="UUID")

    # Build artifact. Owned by this tree, rebuilt by the compiler, never edited by hand.
    compiled: PointerProperty(
        type=bpy.types.NodeTree,
        name="Compiled Shader Group",
        description="Shader node group compiled from this tree",
    )
    compiled_hash: StringProperty(name="Compiled Fingerprint")

    group_node_name: StringProperty(
        name="Group Node Name",
        description="Name of the group node that opened this tree for editing",
        options={'SKIP_SAVE'},
    )

    # -- Blender callbacks ------------------------------------------------

    def update(self):
        # Called on link changes, node add/remove, and during file load.
        # Never mutate the tree here; the compiler's normalize pass repairs invariants.
        mark_dirty(self)

    # -- lifecycle --------------------------------------------------------

    @property
    def is_initialized(self) -> bool:
        return len(self.channels) > 0 or len(self.nodes) > 0

    def initialize(self):
        """Populate a brand-new tree: io nodes, a Color channel, passthrough link."""
        if not self.uuid:
            self.uuid = str(uuid.uuid4())
        self.ensure_io_nodes()
        if len(self.channels) == 0:
            self.create_channel('Color', 'COLOR')
        mark_dirty(self)

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
    def channels_manager(self):
        return CollectionManager(self, 'channels', self, 'active_channel_index',
                                 callback=self.on_channels_changed)

    @property
    def active_channel(self):
        if 0 <= self.active_channel_index < len(self.channels):
            return self.channels[self.active_channel_index]
        return None

    def on_channels_changed(self):
        self.sync_group_node_sockets()
        sync_group_nodes_referencing(self)
        mark_dirty(self)

    def sync_group_node_sockets(self):
        specs = channel_socket_specs(self.channels)
        for node in self.nodes:
            if node.bl_idname == GROUP_INPUT_ID:
                sync_sockets(node.outputs, specs)
            elif node.bl_idname == GROUP_OUTPUT_ID:
                sync_sockets(node.inputs, specs)

    def create_channel(self, name: str = "Channel", type: str = 'COLOR'):
        channel = self.channels_manager.add(properties={'name': name, 'type': type})
        channel.ensure_uuid()
        input_node = self.get_input_node()
        output_node = self.get_output_node()
        if input_node and output_node:
            for sock_name in (channel.name, channel_alpha_name(channel.name)):
                connect_sockets(input_node.outputs[sock_name], output_node.inputs[sock_name])
        return channel

    def delete_channel(self, index: int):
        self.channels_manager.remove(index)

    def delete_active_channel(self):
        self.delete_channel(self.channels_manager.active_index)

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

    def layer_nodes(self) -> list:
        return [n for n in self.nodes if getattr(n, 'is_layer_node', False)]

    def layer_chain(self, channel_name: str | None = None) -> list:
        """Layer nodes feeding *channel_name* on the active output, top-most first.

        Follows each layer's 'Color' input upstream. Only the linear stack is
        returned; branches off the main chain are not included.
        """
        if channel_name is None:
            ch = self.active_channel
            if ch is None:
                return []
            channel_name = ch.name
        output = self.get_output_node()
        if output is None or channel_name not in output.inputs:
            return []
        chain = []
        socket = output.inputs[channel_name]
        visited = set()
        while socket.links:
            node = socket.links[0].from_node
            if node.name in visited or not getattr(node, 'is_layer_node', False):
                break
            visited.add(node.name)
            chain.append(node)
            socket = node.inputs.get('Color')
            if socket is None:
                break
        return chain

    def insert_layer_node(self, bl_idname: str, channel_name: str | None = None,
                          below: bpy.types.Node | None = None) -> bpy.types.Node:
        """Add a layer node and splice it into the channel's stack.

        By default it goes on top (directly before the output). If *below* is
        a layer node in the chain, the new node is inserted above it.
        """
        if channel_name is None:
            ch = self.active_channel
            channel_name = ch.name if ch else None
        output = self.get_output_node()
        node = self.nodes.new(bl_idname)

        if output is None or channel_name is None or channel_name not in output.inputs:
            return node

        alpha_name = channel_alpha_name(channel_name)
        if below is not None and 'Color' in below.outputs:
            # Insert between `below` and whatever it feeds.
            targets = [(l.to_node, l.to_socket) for l in below.outputs['Color'].links]
            alpha_targets = [(l.to_node, l.to_socket) for l in below.outputs['Alpha'].links]
            connect_sockets(below.outputs['Color'], node.inputs['Color'])
            connect_sockets(below.outputs['Alpha'], node.inputs['Alpha'])
            for to_node, to_socket in targets:
                connect_sockets(node.outputs['Color'], to_socket)
            for to_node, to_socket in alpha_targets:
                connect_sockets(node.outputs['Alpha'], to_socket)
            node.location = (below.location.x + 40, below.location.y - 40)
        else:
            color_in = output.inputs[channel_name]
            alpha_in = output.inputs.get(alpha_name)
            prev_color = color_in.links[0].from_socket if color_in.links else None
            prev_alpha = alpha_in.links[0].from_socket if alpha_in and alpha_in.links else None
            if prev_color is not None:
                connect_sockets(prev_color, node.inputs['Color'])
            if prev_alpha is not None:
                connect_sockets(prev_alpha, node.inputs['Alpha'])
            connect_sockets(node.outputs['Color'], color_in)
            if alpha_in is not None:
                connect_sockets(node.outputs['Alpha'], alpha_in)
            node.location = (output.location.x - 260, output.location.y)
        self.nodes.active = node
        return node


classes = (
    PaintSystemNodeTree,
)

owner = object()


def on_ps_nodetree_name_change():
    # Artifact names derive from the tree name; recompile picks up the rename.
    mark_dirty()


_register, _unregister = register_classes_factory(classes)


def register():
    _register()
    bpy.msgbus.subscribe_rna(
        key=(PaintSystemNodeTree, "name"),
        owner=owner,
        args=(),
        notify=on_ps_nodetree_name_change,
    )


def unregister():
    _unregister()
    bpy.msgbus.clear_by_owner(owner)
