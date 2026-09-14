from bpy.utils import register_submodule_factory

submodules = (
    "main_panels",
)

register, unregister = register_submodule_factory(__name__, submodules)
