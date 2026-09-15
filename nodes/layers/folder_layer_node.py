from bpy.types import Node
from bpy.props import BoolProperty
from bpy.utils import register_classes_factory

from .base_layer_node import PaintSystemLayerNode


FOLDER_LAYER_COLOR = (0.235291, 0.196, 0.121)


class PaintSystemFolderLayerNode(PaintSystemLayerNode, Node):
    """A layer whose content is the stack linked into ``Content Color``/``Content Alpha``.

    The content composites over a transparent backdrop, and the result
    blends over the stack below with the folder's own opacity and blend
    mode, like any other layer.
    """
    bl_idname = 'PaintSystemFolderLayerNode'
    bl_label = 'Folder'
    bl_icon = 'FILE_FOLDER'

    is_folder = True

    is_expanded: BoolProperty(name="Expanded", default=True,
                              description="Show the layers inside this folder")

    def init(self, context):
        super().init(context)
        color_in = self.inputs.new('NodeSocketColor', "Content Color")
        color_in.default_value = (0, 0, 0, 0)
        color_in.hide_value = True
        alpha_in = self.inputs.new('NodeSocketFloat', "Content Alpha")
        alpha_in.default_value = 0.0
        alpha_in.hide_value = True
        self.use_custom_color = True
        self.color = FOLDER_LAYER_COLOR

    def draw_buttons(self, context, layout):
        self.draw_layer_settings(context, layout)
        self.draw_cache_settings(context, layout)

    def emit_source(self, ctx):
        return (ctx.input_source(self.inputs['Content Color']),
                ctx.input_source(self.inputs['Content Alpha']))


classes = (
    PaintSystemFolderLayerNode,
)


register, unregister = register_classes_factory(classes)
