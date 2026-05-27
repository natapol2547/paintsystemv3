import bpy
import uuid
from bpy.types import NodeTree
from bpy.props import BoolProperty, CollectionProperty, IntProperty, PointerProperty, StringProperty
from bpy.utils import register_classes_factory

from bpy_extras.node_utils import connect_sockets

from ..props.channel import PaintSystemChannel
from ..props.collection_manager import CollectionManager
from ..common import ensure_shader_node_tree


def _detect_change(old_names, new_names):
    """Compare two name lists and return (change_type, index) or (None, None).

    change_type is one of 'ADD', 'REMOVE', 'MOVE', 'RENAME'.
    """
    if len(new_names) > len(old_names):
        for i in range(len(new_names)):
            if i >= len(old_names) or old_names[i] != new_names[i]:
                return ('ADD', i)

    elif len(new_names) < len(old_names):
        for i in range(len(old_names)):
            if i >= len(new_names) or old_names[i] != new_names[i]:
                return ('REMOVE', i)

    else:
        for i in range(len(old_names)):
            if old_names[i] != new_names[i]:
                if old_names[i] in new_names and new_names[i] in old_names:
                    return ('MOVE', i)
                return ('RENAME', i)

    return (None, None)


def _get_socket_type(type: str) -> str:
    type_to_socket_type = {
        'COLOR': 'NodeSocketColor',
        'FLOAT': 'NodeSocketFloat',
        'VECTOR': 'NodeSocketVector',
    }
    return type_to_socket_type.get(type, 'NodeSocketColor')


def sync_sockets_to_channels(sockets, channels):
    """Apply minimal add/remove/move/rename ops so *sockets* matches *channels*."""
    while True:
        current_names = [s.name for s in sockets]
        expected_names = [ch.name for ch in channels]
        change, idx = _detect_change(current_names, expected_names)

        if change is None:
            break

        if change == 'ADD':
            ch = channels[idx]
            sock = sockets.new(_get_socket_type(ch.type), ch.name)
            if ch.type == 'COLOR':
                sock.default_value = (0, 0, 0, 0)
            sockets.move(len(sockets) - 1, idx)

        elif change == 'REMOVE':
            sockets.remove(sockets[idx])

        elif change == 'MOVE':
            target = expected_names.index(current_names[idx])
            sockets.move(idx, target)

        elif change == 'RENAME':
            sockets[idx].name = channels[idx].name

    for idx, ch in enumerate(channels):
        sock = sockets[idx]
        sock.hide_value = True
        socket_type = _get_socket_type(ch.type)
        if sock.bl_idname != socket_type:
            sockets.remove(sock)
            new_sock = sockets.new(socket_type, ch.name)
            if ch.type == 'COLOR':
                new_sock.default_value = (0, 0, 0, 0)
            sockets.move(len(sockets) - 1, idx)


def sync_group_nodes_referencing(tree):
    """Re-sync every PaintSystemGroupLayerNode whose node_tree is *tree*.

    Channels of a nested tree can change while parent group nodes reference it;
    those parents won't get a callback, so push the update to them here.
    """
    for ng in bpy.data.node_groups:
        if ng.bl_idname != 'PaintSystemNodeTree':
            continue
        for node in ng.nodes:
            if node.bl_idname == 'PaintSystemGroupLayerNode' and node.node_tree is tree:
                node.sync_sockets()


def _update_shader_node_tree(self, context):
    self.update_shader_node_tree(context)


class PaintSystemNodeTree(NodeTree):
    bl_idname = 'PaintSystemNodeTree'
    bl_label = 'Paint System'
    bl_icon = 'BRUSH_DATA'
    bl_use_group_interface = False

    version: IntProperty(name="Version", default=1)
    channels: CollectionProperty(type=PaintSystemChannel)
    active_channel_index: IntProperty(name="Active Channel", default=0)
    shader_node_tree: PointerProperty(
        type=bpy.types.NodeTree,
        name="Shader Node Group",
        description="Companion ShaderNodeGroup built from this tree",
    )
    uuid: StringProperty(name="UUID")
    is_new_status: BoolProperty(name="Is Newly Created", default=True)
    group_node_name: StringProperty(
        name="Group Node Name",
        description="Name of the group node that opened this tree for editing",
        options={'SKIP_SAVE'},
    )

    def init(self, context):
        self.uuid = str(uuid.uuid4())
        group_in = self.nodes.new('PaintSystemGroupInputNode')
        group_in.location = (-200, 0)
        group_out = self.nodes.new('PaintSystemGroupOutputNode')

        self.create_channel('Color', 'COLOR')

        connect_sockets(group_out.inputs['Color'], group_in.outputs['Color'])

        self.is_new_status = False

    def update(self):
        output_node = self.get_output_node()
        # Set the first output node as the active output node
        if not output_node:
            for node in self.nodes:
                if node.bl_idname == 'PaintSystemGroupOutputNode':
                    # print(f"Setting {node.name} as active output node")
                    node.is_active_output = True
                    break
        # Update group nodetree
        self.update_shader_node_tree(bpy.context)

    def copy(self):
        nodetree = super().copy(self)
        # Create a copy of channels and layers shader node tree
        self.shader_node_tree = nodetree.shader_node_tree.copy(
        ) if nodetree.shader_node_tree else None
        self.update_shader_node_tree(bpy.context)
        for n in nodetree.nodes:
            if hasattr(n, 'shader_node_tree') and n.shader_node_tree:
                n.shader_node_tree = n.shader_node_tree.copy()
                n.update_shader_node_tree(bpy.context)
        for ch in nodetree.channels:
            ch.shader_node_tree = ch.shader_node_tree.copy() if ch.shader_node_tree else None
            ch.update_shader_node_tree(bpy.context)

    def update_shader_node_tree(self, context):
        if not self.uuid:
            self.uuid = str(uuid.uuid4())
        self.shader_node_tree = ensure_shader_node_tree(
            self.shader_node_tree, self._get_shader_node_tree_name())

    def sync_group_node_sockets(self):
        """Sync Group Input outputs and Group Output inputs to match self.channels."""
        for node in self.nodes:
            if node.bl_idname == 'PaintSystemGroupInputNode':
                sync_sockets_to_channels(node.outputs, self.channels)
            elif node.bl_idname == 'PaintSystemGroupOutputNode':
                sync_sockets_to_channels(node.inputs, self.channels)

    def create_channel(self, name: str = "Channel", type: str = 'COLOR'):
        channel = self.channels_manager.add(
            properties={'name': name, 'type': type})
        channel.update_shader_node_tree(bpy.context)
        # Connect group input to group output
        input_node = self.get_input_node()
        output_node = self.get_output_node()
        if input_node and output_node:
            connect_sockets(input_node.outputs[name], output_node.inputs[name])

    def delete_channel(self, index: int):
        self.channels_manager.remove(index)

    def delete_active_channel(self):
        self.delete_channel(self.channels_manager.active_index)

    def get_output_node(self) -> bpy.types.Node | None:
        for node in self.nodes:
            if node.bl_idname == 'PaintSystemGroupOutputNode' and node.is_active_output:
                return node
        return None

    def get_input_node(self) -> bpy.types.Node | None:
        for node in self.nodes:
            if node.bl_idname == 'PaintSystemGroupInputNode':
                return node
        return None

    def _get_shader_node_tree_name(self):
        return f"{self.name} Group ({self.uuid[:4]})"

    @property
    def is_new(self):
        return self.is_new_status

    @property
    def channels_manager(self):
        return CollectionManager(self, 'channels', self, 'active_channel_index', callback=lambda: sync_group_nodes_referencing(self))


classes = (
    PaintSystemNodeTree,
)

owner = object()
subscribe_to = (PaintSystemNodeTree, "name")


def on_ps_nodetree_name_change():
    for ng in bpy.data.node_groups:
        if ng.bl_idname == 'PaintSystemNodeTree':
            print(
                f"Updating shader node tree for {ng.name} due to name change")
            ng.update_shader_node_tree(bpy.context)


_register, _unregister = register_classes_factory(classes)


def register():
    _register()
    bpy.msgbus.subscribe_rna(
        key=subscribe_to,
        owner=owner,
        args=(),
        notify=on_ps_nodetree_name_change,
    )


def unregister():
    _unregister()
    bpy.msgbus.clear_by_owner(owner)
