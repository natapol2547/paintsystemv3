import math
import os

import bpy
import bpy.utils.previews


# The add-on's module name. Blender stores the add-on's preferences under
# it. It is "paint_system" for a legacy add-on and
# "bl_ext.<repository>.paint_system" for an extension.
ADDON_ID = __package__


def addon_preferences(context):
    """The add-on's preferences, or None when it has no entry (a test, a reload)."""
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon is not None else None


def redraw_paint_views(window_manager) -> None:
    """Redraw every 3D view and image editor in every window.

    Does nothing when *window_manager* is None.
    """
    for window in getattr(window_manager, 'windows', ()):
        for area in window.screen.areas:
            if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                area.tag_redraw()


def is_newer_than(major, minor=0, patch=0):
    return bpy.app.version >= (major, minor, patch)


def node_location(node):
    """*node*'s location in its node tree, outside any frame, as a Vector.

    ``location`` is relative to the parent frame. Blender 4.5 and later
    also have ``location_absolute``. Older versions only have the relative
    one, so the parents' locations are added up.
    """
    location = getattr(node, 'location_absolute', None)
    if location is not None:
        return location.copy()
    location = node.location.copy()
    parent = node.parent
    while parent is not None:
        location += parent.location
        parent = parent.parent
    return location

# UI


# Previews of the add-on icons in icons/, keyed by file name without the
# extension. None while the add-on is not registered.
_icon_previews = None


def load_icons() -> None:
    global _icon_previews
    _icon_previews = bpy.utils.previews.new()
    folder = os.path.join(os.path.dirname(__file__), 'icons')
    for file_name in os.listdir(folder):
        name = os.path.splitext(file_name)[0]
        _icon_previews.load(name, os.path.join(folder, file_name), 'IMAGE')


def unload_icons() -> None:
    global _icon_previews
    bpy.utils.previews.remove(_icon_previews)
    _icon_previews = None


def get_icon(name: str) -> int | None:
    """Icon id of the add-on icon *name*, or None when there is no such icon."""
    if _icon_previews is None or name not in _icon_previews:
        return None
    return _icon_previews[name].icon_id


_blender_icons: set[str] | None = None


def _blender_icon_names() -> set[str]:
    global _blender_icons
    if _blender_icons is None:
        parameter = bpy.types.UILayout.bl_rna.functions['prop'].parameters['icon']
        _blender_icons = set(parameter.enum_items.keys())
    return _blender_icons


def icon_kwargs(*names: str) -> dict:
    """Layout keyword arguments for the first of *names* that exists.

    Each name is an add-on icon from ``icons/`` or a Blender icon. Blender
    renames icons between versions, and forks such as Bforartists ship
    their own set. A layout call raises on an unknown icon name. So every
    icon a layout call draws goes through here, with the new name first
    and the old name after it.
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

    Use it where a layout call is not involved, such as ``bl_icon`` on
    node and node tree classes or a gizmo's ``icon``. An unknown name in
    ``bl_icon`` makes ``register_class`` raise, so the class picks its
    icon when it is defined. List the new name first and the old name
    after it, as for ``icon_kwargs``.
    """
    blender_icons = _blender_icon_names()
    return next((name for name in names if name in blender_icons), 'NONE')


def rounded_rect(x0, y0, x1, y1, radius, segments=6):
    """Outline points of a rounded rectangle, counter-clockwise from the right edge.

    (x0, y0) is the bottom-left corner and (x1, y1) the top-right one. The
    radius is clamped so the corners fit. The shape is convex, so a triangle
    fan over the points fills it.
    """
    radius = max(0.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    corners = ((x1 - radius, y1 - radius, 0.0), (x0 + radius, y1 - radius, 0.5),
               (x0 + radius, y0 + radius, 1.0), (x1 - radius, y0 + radius, 1.5))
    points = []
    for cx, cy, start in corners:
        for step in range(segments + 1):
            angle = math.pi * (start + 0.5 * step / segments)
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points
