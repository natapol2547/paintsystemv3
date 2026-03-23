import bpy

from bpy.props import BoolProperty
from bpy.utils import register_classes_factory
from .base import PaintSystemNode


class PaintSystemGroupInputNode(PaintSystemNode):
    bl_idname = 'PaintSystemGroupInputNode'
    bl_label = 'Group Input'
    bl_icon = 'GROUP_UVS'

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == 'PaintSystemNodeTree'

    def init(self, context):
        super().init(context)
        sync_group_node_sockets(self.id_data)

    def draw_buttons(self, context, layout):
        pass

    def draw_label(self):
        return "Group Input"


class PaintSystemGroupOutputNode(PaintSystemNode):
    bl_idname = 'PaintSystemGroupOutputNode'
    bl_label = 'Group Output'
    bl_icon = 'GROUP_UVS'

    is_active_output: BoolProperty(name="Is Active Output", default=False)

    def init(self, context):
        super().init(context)
        sync_group_node_sockets(self.id_data)

    def copy(self, node):
        super().copy(node)
        self.is_active_output = False

    def draw_buttons(self, context, layout):
        if not self.is_active_output:
            warning_box = layout.box()
            warning_box.label(text="Inactive Output", icon='ERROR')

    def draw_label(self):
        return "Group Output"


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


def _sync_sockets(sockets, channels):
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


def sync_group_node_sockets(node_tree):
    """Sync Group Input outputs and Group Output inputs to match node_tree.channels."""
    for node in node_tree.nodes:
        if node.bl_idname == 'PaintSystemGroupInputNode':
            _sync_sockets(node.outputs, node_tree.channels)
        elif node.bl_idname == 'PaintSystemGroupOutputNode':
            _sync_sockets(node.inputs, node_tree.channels)


classes = (
    PaintSystemGroupInputNode,
    PaintSystemGroupOutputNode,
)


register, unregister = register_classes_factory(classes)
