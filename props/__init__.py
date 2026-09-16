from bpy.utils import register_submodule_factory

submodules = (
    "channel",
    "selection",
)

register, unregister = register_submodule_factory(__name__, submodules)
