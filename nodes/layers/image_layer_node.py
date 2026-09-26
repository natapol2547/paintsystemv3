import bpy

from bpy.types import Node
from bpy.props import PointerProperty, StringProperty
from bpy.utils import register_classes_factory

from .base_layer_node import (EMPTY_SOURCE, PaintSystemLayerNode, draw_uv_map, emit_image_texture,
                              update_tree_and_painting)
from ...common import blender_icon
from ...compiler.bake import create_managed_image
from ...props.channel import image_colorspace


class PaintSystemImageLayerNode(PaintSystemLayerNode, Node):
    bl_idname = 'PaintSystemImageLayerNode'
    bl_label = 'Image Layer'
    bl_icon = blender_icon('IMAGE_DATA')
    header_color = (0.43, 0.35, 0.20)

    ps_type = 'IMAGE'
    ps_label = "Image"
    ps_description = "Paintable image layer"
    ps_icon = ('image',)
    ps_menu_section = 'CONTENT'
    ps_add_options = ('resolution',)

    image: PointerProperty(
        type=bpy.types.Image,
        name="Image",
        description="Image painted on by this layer",
        update=update_tree_and_painting,
    )
    uv_map: StringProperty(
        name="UV Map",
        description="UV map used to place the image (empty: active render UV map)",
        update=update_tree_and_painting,
    )

    @property
    def paint_image(self):
        return self.image

    @classmethod
    def create(cls, tree, target=None, resolution='2048', **options):
        # *resolution* is the identifier of the operator's resolution enum.
        size = int(resolution)
        node = super().create(tree, target=target)
        # The layer goes into the active channel's stack.
        node.image = create_managed_image(f"{tree.name} {node.name}", size, size,
                                          colorspace=image_colorspace(tree.active_channel))
        return node

    def draw_source_settings(self, context, layout):
        layout.template_ID(self, "image", new="image.new", open="image.open")
        draw_uv_map(context, layout, self)

    def draw_label(self):
        super().draw_label()
        if self.image:
            return self.image.name
        return "Image Layer"

    def emit_source(self, ctx):
        if self.image is None:
            return EMPTY_SOURCE
        return emit_image_texture(ctx, self, 'tex', self.image, self.uv_map)


classes = (
    PaintSystemImageLayerNode,
)


register, unregister = register_classes_factory(classes)
