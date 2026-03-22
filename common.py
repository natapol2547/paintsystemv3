import bpy
import pathlib
import os

from .custom_icons import get_icon


ADDON_ID = "paint_system"

# Version Check


def is_newer_than(major, minor=0, patch=0):
    return bpy.app.version >= (major, minor, patch)


def is_online() -> bool:
    """Check if the internet is connected."""
    if not is_newer_than(4, 2):
        return True
    if not hasattr(bpy.app, 'online_access'):
        return False
    return bpy.app.online_access

# UI


def get_icon_from_socket_type(socket_type: str) -> int:
    type_to_icon = {
        'COLOR': 'color_socket',
        'VECTOR': 'vector_socket',
        'FLOAT': 'float_socket',
    }
    return get_icon(type_to_icon.get(socket_type, 'color_socket'))


def get_image_editor_icon(current_image_editor: str) -> int:
    if not current_image_editor:
        return None
    editor_path = pathlib.Path(current_image_editor)
    app_name = editor_path.name.lower()
    if "clipstudiopaint" in app_name:
        return get_icon("clip_studio_paint")
    elif "photoshop" in app_name:
        return get_icon("photoshop")
    elif "gimp" in app_name:
        return get_icon("gimp")
    elif "krita" in app_name:
        return get_icon("krita")
    elif "affinity" in app_name:
        return get_icon("affinity")
    return get_icon("image")


# Path
def get_project_root_path() -> str:
    return os.path.dirname(os.path.abspath(__file__))
