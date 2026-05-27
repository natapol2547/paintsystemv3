import bpy
from bpy.utils import register_submodule_factory

submodules = (
    "group_nodes",
)

register, unregister = register_submodule_factory(__name__, submodules)