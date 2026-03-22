import bpy
import uuid
from bpy.types import NodeTree
from bpy.props import BoolProperty, CollectionProperty, IntProperty, PointerProperty, StringProperty

from bpy_extras.node_utils import connect_sockets
from .group_nodes import sync_group_node_sockets
from ..props.channel import PaintSystemChannel
from ..props.collection_manager import CollectionManager


class PaintSystemNodeTree(NodeTree):
    bl_idname = 'PaintSystemNodeTree'
    bl_label = 'Paint System'
    bl_icon = 'BRUSH_DATA'
    bl_use_group_interface = False

    version: IntProperty(name="Version", default=1)
    channels: CollectionProperty(type=PaintSystemChannel)
    active_channel_index: IntProperty(name="Active Channel", default=0)
    shader_node_group: PointerProperty(
        type=bpy.types.NodeTree,
        name="Shader Node Group",
        description="Companion ShaderNodeGroup built from this tree",
    )
    uuid: StringProperty(name="UUID")
    is_new_status: BoolProperty(name="Is Newly Created", default=True)

    def init(self, context):
        self.uuid = str(uuid.uuid4())
        group_in = self.nodes.new('PaintSystemGroupInputNode')
        group_in.location = (-200, 0)
        group_out = self.nodes.new('PaintSystemGroupOutputNode')

        self.create_channel('Color', 'COLOR')

        connect_sockets(group_out.inputs['Color'], group_in.outputs['Color'])

        self.is_new_status = False

    def update(self):
        pass
        # if self.uuid is None:
        #     self.uuid = str(uuid.uuid4())

    def create_channel(self, name: str = "Channel", type: str = 'COLOR'):
        self.channels_manager.add(
            properties={'name': name, 'type': type})
        sync_group_node_sockets(self)

    def delete_channel(self, index: int):
        self.channels_manager.remove(index)
        sync_group_node_sockets(self)

    def delete_active_channel(self):
        self.delete_channel(self.channels_manager.active_index)

    @property
    def is_new(self):
        return self.is_new_status

    @property
    def channels_manager(self):
        return CollectionManager(self, 'channels', self, 'active_channel_index')


classes = (
    PaintSystemNodeTree,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
