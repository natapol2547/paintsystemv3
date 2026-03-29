import uuid
import bpy
from bpy.props import StringProperty, EnumProperty, PointerProperty

from ..nodes.builder import NodeTreeBuilder

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


def update_group_node_tree(self, context):
    self.update_shader_node_tree(context)
    node_tree = self.id_data
    if node_tree and node_tree.bl_idname == 'PaintSystemNodeTree':
        node_tree.sync_group_node_sockets()


class PaintSystemChannel(bpy.types.PropertyGroup):
    name: StringProperty(
        name="Name",
        default="Channel",
        update=update_group_node_tree,
        set_transform=set_name_transform,
    )
    type: EnumProperty(
        name="Type",
        items=CHANNEL_SOCKET_TYPES,
        default='COLOR',
        update=update_group_node_tree,
    )
    uuid: StringProperty(name="UUID")
    shader_node_tree: PointerProperty(
        type=bpy.types.NodeTree, name="Shader Node Tree", description="Shader Node Tree for this channel")

    def update_shader_node_tree(self, context):
        # Check if uuid is valid
        if not self.uuid:
            self.uuid = str(uuid.uuid4())

        target_name = self._get_shader_node_tree_name()
        if not self.shader_node_tree:
            self.shader_node_tree = bpy.data.node_groups.new(
                target_name, 'ShaderNodeTree')
        elif self.shader_node_tree.name != target_name:
            self.shader_node_tree.name = target_name

        builder = NodeTreeBuilder(self.shader_node_tree)
        builder.add_socket('INPUT', 'NodeSocketColor', 'Color')
        builder.add_socket('INPUT', 'NodeSocketFloat', 'Alpha', subtype='FACTOR',
                           min_value=0.0, max_value=1.0, default_value=1.0)
        builder.add_socket('OUTPUT', 'NodeSocketColor', 'Color')
        builder.add_socket('OUTPUT', 'NodeSocketFloat', 'Alpha', subtype='FACTOR',
                           min_value=0.0, max_value=1.0, default_value=1.0)
        builder.build()

    def _get_shader_node_tree_name(self):
        return f".PS {self.name} Channel ({self.uuid[:4]})"


classes = (
    PaintSystemChannel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
