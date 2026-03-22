import bpy
from bpy.props import StringProperty, EnumProperty

from ..common import get_next_unique_name


CHANNEL_SOCKET_TYPES = [
    ('COLOR', "Color", "Color (RGBA) channel"),
    ('FLOAT', "Float", "Float (scalar) channel"),
    ('VECTOR', "Vector", "Vector (XYZ) channel"),
]


def set_name_transform(self, new_value, curr_value, is_set):
    node_tree = self.id_data
    if node_tree and node_tree.bl_idname == 'PaintSystemNodeTree':
        return get_next_unique_name(
            new_value, [channel.name for channel in node_tree.channels if channel != self])
    return new_value


def update_node_tree(self, context):
    from ..nodes.group_nodes import sync_group_node_sockets
    node_tree = self.id_data
    if node_tree and node_tree.bl_idname == 'PaintSystemNodeTree':
        sync_group_node_sockets(node_tree)


class PaintSystemChannel(bpy.types.PropertyGroup):
    name: StringProperty(
        name="Name",
        default="Channel",
        update=update_node_tree,
        set_transform=set_name_transform,
    )
    type: EnumProperty(
        name="Type",
        items=CHANNEL_SOCKET_TYPES,
        default='COLOR',
        update=update_node_tree,
    )


classes = (
    PaintSystemChannel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
