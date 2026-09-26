from bpy.utils import register_submodule_factory

submodules = (
    "io",
    "layers",
    "link_tabs",
)

register, unregister = register_submodule_factory(__name__, submodules)
