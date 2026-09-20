from bpy.utils import register_submodule_factory

submodules = (
    "main_panels",
    "layers_panels",
    "action_bar",
)

register, unregister = register_submodule_factory(__name__, submodules)
