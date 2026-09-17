import bpy

from bpy.types import Node
from bpy.props import PointerProperty, StringProperty
from bpy.utils import register_classes_factory

from .base_layer_node import PaintSystemLayerNode, emit_image_texture, update_tree_and_painting
from ...common import blender_icon
from ...compiler.bake import create_managed_image


IMAGE_LAYER_COLOR = (0.235291, 0.215529, 0.170224)


class PaintSystemImageLayerNode(PaintSystemLayerNode, Node):
    bl_idname = 'PaintSystemImageLayerNode'
    bl_label = 'Image Layer'
    bl_icon = blender_icon('IMAGE_DATA')

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

    def init(self, context):
        super().init(context)
        self.use_custom_color = True
        self.color = IMAGE_LAYER_COLOR

    @classmethod
    def create(cls, tree, target=None, resolution='2048', **options):
        # *resolution* is the identifier of the operator's resolution enum.
        size = int(resolution)
        node = super().create(tree, target=target)
        node.image = create_managed_image(f"{tree.name} {node.name}", size, size)
        return node

    def draw_source_settings(self, context, layout):
        layout.template_ID(self, "image", new="image.new", open="image.open")
        obj = getattr(context, 'object', None)
        if obj is not None and obj.type == 'MESH':
            layout.prop_search(self, "uv_map", obj.data, "uv_layers", text="UV")
        else:
            layout.prop(self, "uv_map")

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
