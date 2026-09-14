from bpy.types import Node
from bpy.props import FloatVectorProperty
from bpy.utils import register_classes_factory

from .base_layer_node import PaintSystemLayerNode
from ..base_node import mark_tree_dirty


SOLID_LAYER_COLOR = (0.17, 0.235, 0.19)


class PaintSystemSolidColorLayerNode(PaintSystemLayerNode, Node):
    bl_idname = 'PaintSystemSolidColorLayerNode'
    bl_label = 'Solid Color'
    bl_icon = 'COLOR'

    fill_color: FloatVectorProperty(
        name="Color", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.8, 0.8, 0.8, 1.0), update=mark_tree_dirty,
    )

    def init(self, context):
        super().init(context)
        self.use_custom_color = True
        self.color = SOLID_LAYER_COLOR

    def draw_buttons(self, context, layout):
        self.draw_layer_settings(context, layout)
        layout.prop(self, "fill_color", text="")
        self.draw_cache_settings(context, layout)

    def emit_source(self, ctx):
        r, g, b, a = self.fill_color
        return (r, g, b, 1.0), a


classes = (
    PaintSystemSolidColorLayerNode,
)


register, unregister = register_classes_factory(classes)
