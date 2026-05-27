import bpy

from bpy.types import Node
from bpy.props import BoolProperty
from bpy.utils import register_classes_factory
from ..base_node import PaintSystemBaseNode


GROUP_IO_COLOR = (0.176468, 0.138996, 0.138996)


class PaintSystemGroupNode(PaintSystemBaseNode):
    def init(self, context):
        super().init(context)
        self.use_custom_color = True
        self.color = GROUP_IO_COLOR
        self.id_data.sync_group_node_sockets()


class PaintSystemGroupInputNode(PaintSystemGroupNode, Node):
    bl_idname = 'PaintSystemGroupInputNode'
    bl_label = 'Group Input'
    bl_icon = 'GROUP_UVS'

    def draw_buttons(self, context, layout):
        pass

    def draw_label(self):
        return "Group Input"


class PaintSystemGroupOutputNode(PaintSystemGroupNode, Node):
    bl_idname = 'PaintSystemGroupOutputNode'
    bl_label = 'Group Output'
    bl_icon = 'GROUP_UVS'

    is_active_output: BoolProperty(name="Is Active Output", default=False)

    def copy(self, node):
        super().copy(node)
        self.is_active_output = False

    def draw_buttons(self, context, layout):
        if not self.is_active_output:
            warning_box = layout.box()
            warning_box.label(text="Inactive Output", icon='ERROR')

    def draw_label(self):
        return "Group Output"


classes = (
    PaintSystemGroupInputNode,
    PaintSystemGroupOutputNode,
)


register, unregister = register_classes_factory(classes)
