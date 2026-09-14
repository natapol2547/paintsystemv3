import bpy

from bpy.types import Node
from bpy.props import PointerProperty, StringProperty
from bpy.utils import register_classes_factory

from .base_layer_node import PaintSystemLayerNode, emit_image_texture
from ..base_node import mark_tree_dirty


IMAGE_LAYER_COLOR = (0.235291, 0.215529, 0.170224)


class PaintSystemImageLayerNode(PaintSystemLayerNode, Node):
    bl_idname = 'PaintSystemImageLayerNode'
    bl_label = 'Image Layer'
    bl_icon = 'IMAGE_DATA'

    image: PointerProperty(
        type=bpy.types.Image,
        name="Image",
        description="Image painted on by this layer",
        update=mark_tree_dirty,
    )
    uv_map: StringProperty(
        name="UV Map",
        description="UV map used to place the image (empty: active render UV map)",
        update=mark_tree_dirty,
    )

    def init(self, context):
        super().init(context)
        self.use_custom_color = True
        self.color = IMAGE_LAYER_COLOR

    def draw_buttons(self, context, layout):
        self.draw_layer_settings(context, layout)
        layout.template_ID(self, "image", new="image.new", open="image.open")
        obj = context.object
        if obj is not None and obj.type == 'MESH':
            layout.prop_search(self, "uv_map", obj.data, "uv_layers", text="UV")
        else:
            layout.prop(self, "uv_map")
        self.draw_cache_settings(context, layout)

    def draw_label(self):
        if self.image:
            return self.image.name
        return "Image Layer"

    def emit_source(self, ctx):
        if self.image is None:
            return (0.0, 0.0, 0.0, 1.0), 0.0
        return emit_image_texture(ctx, self, 'tex', self.image, self.uv_map)


classes = (
    PaintSystemImageLayerNode,
)


register, unregister = register_classes_factory(classes)
