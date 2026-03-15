import bpy
from bpy.types import NodeTree
from bpy.props import CollectionProperty, IntProperty, PointerProperty

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
        """Called when the node tree topology changes (links/nodes added/removed).
        Will eventually rebuild the corresponding ShaderNodeGroup."""
        pass


classes = (
    PaintSystemNodeTree,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
