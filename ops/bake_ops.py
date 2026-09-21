from bpy.types import Operator
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy.utils import register_classes_factory

from ..context import button_layer, get_active_tree
from ..compiler.bake import bake_node_cache
from .node_tree_ops import RESOLUTION_ITEMS


class PAINTSYSTEM_OT_bake_cache(Operator):
    bl_idname = "paint_system.bake_cache"
    bl_label = "Bake Layer Cache"
    bl_description = "Bake this layer's output (including everything below it) into its cache image"
    bl_options = {'REGISTER', 'UNDO'}

    resolution: EnumProperty(name="Resolution", items=RESOLUTION_ITEMS, default='2048')
    margin: IntProperty(name="Margin", default=8, min=0, max=64)
    uv_map: StringProperty(name="UV Map")

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        return button_layer(context, tree) is not None and context.object is not None

    def execute(self, context):
        tree = get_active_tree(context)
        node = button_layer(context, tree)
        size = int(self.resolution)
        try:
            image = bake_node_cache(context, tree, node, context.object,
                                    width=size, height=size, margin=self.margin,
                                    uv_map=self.uv_map)
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        node.cache_enabled = True
        self.report({'INFO'}, f"Baked {image.name}")
        return {'FINISHED'}

    def invoke(self, context, event):
        tree = get_active_tree(context)
        node = button_layer(context, tree)
        if node is not None and node.cache_image is not None:
            self.resolution = str(node.cache_image.size[0]) if str(node.cache_image.size[0]) in {
                i[0] for i in RESOLUTION_ITEMS} else self.resolution
            self.uv_map = node.cache_uv_map
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "resolution")
        layout.prop(self, "margin")
        obj = context.object
        if obj is not None and obj.type == 'MESH':
            layout.prop_search(self, "uv_map", obj.data, "uv_layers")


classes = (
    PAINTSYSTEM_OT_bake_cache,
)


register, unregister = register_classes_factory(classes)
