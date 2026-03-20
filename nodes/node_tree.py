import bpy
from bpy.types import NodeTree
from bpy.props import CollectionProperty, IntProperty, PointerProperty

from .group_nodes import sync_group_node_sockets
from ..props.channel import PaintSystemChannel


class PaintSystemNodeTree(NodeTree):
    bl_idname = 'PaintSystemNodeTree'
    bl_label = 'Paint System'
    bl_icon = 'BRUSH_DATA'
    bl_use_group_interface = False

    channels: CollectionProperty(type=PaintSystemChannel)
    active_channel_index: IntProperty(name="Active Channel", default=0)
    shader_node_group: PointerProperty(
        type=bpy.types.NodeTree,
        name="Shader Node Group",
        description="Companion ShaderNodeGroup built from this tree",
    )

    def update(self):
        print("PaintSystemNodeTree updated")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        print("PaintSystemNodeTree initialized")

        group_in = self.nodes.new('PaintSystemGroupInputNode')
        group_in.location = (-200, 0)

        group_out = self.nodes.new('PaintSystemGroupOutputNode')
        group_out.location = (200, 0)

        # ch = self.channels.add()
        # ch.name = "Color"
        # ch.socket_type = 'NodeSocketColor'
        # self.active_channel_index = 0

        # sync_group_node_sockets(self)


classes = (
    PaintSystemNodeTree,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
