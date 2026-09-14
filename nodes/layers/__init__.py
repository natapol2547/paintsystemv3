from bpy.utils import register_submodule_factory

submodules = (
    "image_layer_node",
    "solid_color_layer_node",
    "group_layer_node",
)

register, unregister = register_submodule_factory(__name__, submodules)
