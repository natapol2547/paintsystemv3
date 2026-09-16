from bpy.utils import register_submodule_factory

submodules = (
    "channel",
    "selection",
    "stencil",
)

register, unregister = register_submodule_factory(__name__, submodules)
