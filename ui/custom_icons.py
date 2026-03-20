import bpy
import os

ICON_FOLDER = 'icons'

custom_icons = None


def register():
    from ..common import get_project_root_path
    import bpy.utils.previews
    # Custom Icon
    if not hasattr(bpy.utils, 'previews'):
        return
    global custom_icons
    custom_icons = bpy.utils.previews.new()

    folder = get_project_root_path() + os.sep + ICON_FOLDER + os.sep

    for f in os.listdir(folder):
        # Remove file extension
        icon_name = os.path.splitext(f)[0]
        custom_icons.load(icon_name, folder + f, 'IMAGE')


def unregister():
    global custom_icons
    if hasattr(bpy.utils, 'previews'):
        bpy.utils.previews.remove(custom_icons)
        custom_icons = None
