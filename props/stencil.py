"""The user's Stencil Mask settings, held while a selection clips strokes (PS-091).

While a selection limits brush strokes, Blender's Stencil Mask settings
belong to it (`selection/stencil.py`). Each backup lives where memfile
undo treats it the same way as the setting it backs up:

* The image paint settings are tool settings, which undo keeps as they
  are. Their backup is on the window manager, which undo keeps too and
  which files do not save.
* A mesh's stencil UV map is mesh data, which undo restores. Its backup
  is on the scene (`PaintSystemSceneSettings.stencil_meshes`), which undo
  restores too. The window manager entry of the scene keeps a copy, used
  only when the scene is removed while the selection holds it.
"""
import bpy
from bpy.props import BoolProperty, CollectionProperty, PointerProperty, StringProperty


class PaintSystemStencilMeshBackup(bpy.types.PropertyGroup):
    """The stencil UV map a mesh had before the selection set it.

    The mesh is held by pointer, which follows a rename and reads None once
    the mesh is removed. It adds a user to the mesh while the entry exists.
    """

    mesh: PointerProperty(name="Mesh", type=bpy.types.Mesh)
    uv_name: StringProperty(name="UV Map")


class PaintSystemStencilSceneBackup(bpy.types.PropertyGroup):
    """A scene's image paint stencil settings from before the selection took them."""

    scene: PointerProperty(name="Scene", type=bpy.types.Scene)
    use_stencil_layer: BoolProperty(name="Use Stencil Layer", default=False)
    invert_stencil: BoolProperty(name="Invert Stencil", default=False)
    stencil_image: PointerProperty(name="Stencil Image", type=bpy.types.Image)
    meshes: CollectionProperty(type=PaintSystemStencilMeshBackup)


class PaintSystemWindowManagerSettings(bpy.types.PropertyGroup):
    """Session state that must outlive undo and never be saved."""

    stencil_scenes: CollectionProperty(type=PaintSystemStencilSceneBackup)

    def find_scene(self, scene) -> int:
        """Index of *scene*'s entry in `stencil_scenes`, or -1."""
        return next((index for index, entry in enumerate(self.stencil_scenes) if entry.scene == scene), -1)


classes = (
    PaintSystemStencilMeshBackup,
    PaintSystemStencilSceneBackup,
    PaintSystemWindowManagerSettings,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.paint_system = PointerProperty(type=PaintSystemWindowManagerSettings)


def unregister():
    del bpy.types.WindowManager.paint_system
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
