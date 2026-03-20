import bpy

from bpy.utils import register_submodule_factory

submodules = (
    "channel_ops",
    "node_tree_ops",
)

register, unregister = register_submodule_factory(__name__, submodules)
