from bpy.utils import register_submodule_factory

submodules = (
    "channel_ops",
    "node_tree_ops",
    "node_group_ops",
    "bake_ops",
)

register, unregister = register_submodule_factory(__name__, submodules)
