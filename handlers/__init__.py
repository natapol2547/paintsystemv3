from bpy.utils import register_submodule_factory

submodules = (
    "node_tree_handlers",
    "paint_handlers",
)

register, unregister = register_submodule_factory(__name__, submodules)
