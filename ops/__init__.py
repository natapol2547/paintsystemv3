from bpy.utils import register_submodule_factory

submodules = (
    "channel_ops",
    "node_tree_ops",
    "layer_ops",
    "paint_ops",
    "selection_ops",
    "pixel_ops",
    "node_group_ops",
    "filter_layer_ops",
)

register, unregister = register_submodule_factory(__name__, submodules)
