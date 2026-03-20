import bpy

from bpy.utils import register_submodule_factory

submodules = (
    "custom_icons",
    "main",
)

_register, _unregister = register_submodule_factory(__name__, submodules)


def register():
    _register()


def unregister():
    _unregister()
