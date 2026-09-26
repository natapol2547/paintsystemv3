from bpy.utils import register_submodule_factory

submodules = (
    "separate_color_node",
)

register, unregister = register_submodule_factory(__name__, submodules)
