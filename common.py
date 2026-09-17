import logging
import re
import bpy
import os

from .custom_icons import get_icon


# The add-on's module name, which keys its preferences entry: "paint_system"
# as a legacy add-on, "bl_ext.<repository>.paint_system" as an extension.
ADDON_ID = __package__

log = logging.getLogger(__name__)


def is_newer_than(major, minor=0, patch=0):
    return bpy.app.version >= (major, minor, patch)

# UI


_blender_icons: set[str] | None = None


def _blender_icon_names() -> set[str]:
    global _blender_icons
    if _blender_icons is None:
        parameter = bpy.types.UILayout.bl_rna.functions['prop'].parameters['icon']
        _blender_icons = set(parameter.enum_items.keys())
    return _blender_icons


def icon_kwargs(*names: str) -> dict:
    """Layout keyword arguments for the first of *names* that exists.

    A name is an addon icon from ``icons/`` or a Blender icon. Blender
    renames icons between versions, and forks such as Bforartists ship
    their own set, and an unknown name makes the layout call raise. So
    every icon a layout call draws goes through here, with the old name
    listed after the new one.
    """
    blender_icons = _blender_icon_names()
    for name in names:
        icon_id = get_icon(name)
        if icon_id is not None:
            return {'icon_value': icon_id}
        if name in blender_icons:
            return {'icon': name}
    return {'icon': 'NONE'}


def blender_icon(*names: str) -> str:
    """The first of *names* that is a Blender icon, else ``'NONE'``.

    For ``bl_icon`` on node and node tree classes: an unknown name there
    makes ``register_class`` raise, so the class resolves it when it is
    defined. List the old name after the new one, as for ``icon_kwargs``.
    """
    blender_icons = _blender_icon_names()
    return next((name for name in names if name in blender_icons), 'NONE')


# Path
def get_project_root_path() -> str:
    return os.path.dirname(os.path.abspath(__file__))


# Images


def save_image(image: bpy.types.Image) -> None:
    """Keep the unsaved pixels of *image* when the blend file is saved.

    A packed image, or one without a file, is packed again from memory. An
    image backed by a file is written to that file; when the write fails
    (a missing or read-only directory), the image drops its path and is
    packed instead. Images without unsaved changes are left alone.
    """
    if not image.is_dirty:
        return
    if image.packed_file is None and image.filepath:
        try:
            image.save()
            return
        except RuntimeError as error:
            log.warning("Could not save image %r to %r, packing it instead: %s",
                        image.name, image.filepath, error)
            image.filepath_raw = ''
    try:
        image.pack()
    except RuntimeError as error:
        log.warning("Could not pack image %r: %s", image.name, error)


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
    pattern = re.compile(rf"^{re.escape(base_name)}(?: (\d+))?$")

    for item in list_of_names:
        match = pattern.match(item)
        if match:
            if match.group(1):
                numbers_found.add(int(match.group(1)))

    next_number = max(numbers_found) + 1

    return f"{base_name} {next_number}"


def unique_name_kwargs(set_transform):
    """Keyword arguments for a unique-name ``StringProperty``.

    ``set_transform`` exists from Blender 5.0. Older versions get no
    transform; callers fall back to ``ensure_unique_name`` in ``update``.
    """
    if is_newer_than(5, 0):
        return {'set_transform': set_transform}
    return {}


def ensure_unique_name(self, dataptr, propname):
    """Rename *self* if its name clashes with a sibling (pre-5.0 fallback).

    Assigning ``self.name`` re-enters the property's ``update`` once; the
    second pass finds the name unique and stops.
    """
    siblings = [item.name for item in getattr(dataptr, propname) if item != self]
    unique = get_next_unique_name(self.name, siblings)
    if unique != self.name:
        self.name = unique
        return True
    return False


def transform_unique_name(self, dataptr, propname, new_value, curr_value, is_set):
    """Shared `set_transform` for name properties on PaintSystemNodeTree members.

    Resolves *new_value* to a name unique within ``node_tree.<collection_attr>``,
    and—when the resolved name differs from *curr_value*—invokes
    ``self.on_name_update(new_name, curr_value, is_set)`` if that hook is defined.
    """
    new_name = new_value
    if dataptr and propname:
        siblings = [item.name for item in getattr(dataptr, propname)
                    if item != self]
        new_name = get_next_unique_name(new_value, siblings)
    if curr_value != new_name:
        on_name_update = getattr(self, 'on_name_update', None)
        if on_name_update:
            on_name_update(new_name, curr_value, is_set)
    return new_name
