from bpy.utils import register_submodule_factory

submodules = (
    "io",
    "layers",
)

register, unregister = register_submodule_factory(__name__, submodules)
