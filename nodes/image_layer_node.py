import bpy

from bpy.types import Node
from bpy.props import PointerProperty, FloatProperty, EnumProperty

from .base import PaintSystemBaseLayerNode


class PaintSystemImageLayerNode(PaintSystemBaseLayerNode):
    bl_idname = 'PaintSystemImageLayerNode'
    bl_label = 'Image Layer'
    bl_icon = 'IMAGE_DATA'

    image: PointerProperty(
        type=bpy.types.Image,
        name="Image",
        description="Image used by this layer",
    )

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == 'PaintSystemNodeTree'

    def init(self, context):
        super().init(context)
        # self.create_panel("image_settings", "Image Settings", True)

    def draw_buttons(self, context, layout):
        self.draw_layer_settings(context, layout)
        # header, panel = self.draw_panel(layout, "image_settings")
        # if panel:
        #     panel.template_ID(self, "image", new="image.new")

    def draw_label(self):
        if self.image:
            return self.image.name
        return "Image Layer"


classes = (
    PaintSystemImageLayerNode,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
