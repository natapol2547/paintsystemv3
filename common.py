import re
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


# Unique Name


def get_next_unique_name(name: str, list_of_names: list[str]) -> str:
    """
    Finds the next unique name in a sequence from a list of strings.

    Args:
        name: The string to use as the base for the new name (e.g., 'Image 7').
        list_of_names: A list of existing names.

    Returns:
        The next unique name in the sequence.
    """
    if name not in list_of_names:
        return name
    # Extract the non-numeric part of the name to get the base name.
    base_name_match = re.match(r'(\D*)', name)
    if not base_name_match:
        # Fallback if the name has no non-numeric part, though unlikely.
        return name + " 1"

    base_name = base_name_match.group(1).strip()

    # A set to store all the numbers found for this base name sequence.
    # We add 0 to handle the case where the base name itself exists (e.g., 'Image').
    # This implies that 'Image 1' would be the next in sequence.
    numbers_found = {0}
    pattern = re.compile(f"^{re.escape(base_name)}(?: (\d+))?$")

    for item in list_of_names:
        match = pattern.match(item)
        if match:
            if match.group(1):
                numbers_found.add(int(match.group(1)))

    next_number = max(numbers_found) + 1

    return f"{base_name} {next_number}"
