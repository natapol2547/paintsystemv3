# SPDX-License-Identifier: GPL-3.0-or-later
"""The colour a Fill stores, which is the colour a stroke would store (PS-052).

Two conversions stand between the swatch the user sees and the values a
byte image holds, and both changed in 5.0:

- where the colour lives. Painting reads it from the brush or from the
  unified paint settings, and 5.3 also lets a brush opt in to the
  unified colour of its own;
- what the value means. Up to 4.5 `Brush.color` is `COLOR_GAMMA`, a
  display sRGB value that a stroke writes into a byte image raw. From
  5.0 it is `COLOR`, scene linear, and a stroke converts it into the
  image's colour space.

Both are read from the RNA rather than from the version number, so a
build that moves either one is followed rather than guessed at.
"""
import bpy
from mathutils import Color

from ..context import paint_settings
from .core import Refused

DATA_SPACES = frozenset(("Non-Color", "Raw", "Generic Data", "Data"))
"""Colour spaces that hold data, which a stroke writes without converting."""


def paint_struct(context):
    """The `Paint` a Paint System stroke would read in this context.

    `context.paint_settings` follows the mode, and from 5.3 the active
    tool. It has nothing to answer from where there is no space data, as
    in a background session or a script; image painting is the only mode
    a layer is painted in, so its settings stand in there.
    """
    return paint_settings(context) or context.tool_settings.image_paint


def unified_settings(context):
    """The unified paint settings of image painting in this context."""
    unified = getattr(paint_struct(context), 'unified_paint_settings', None)
    if unified is not None:
        return unified
    # Until 5.0 there is one set for every mode, on the tool settings.
    return context.scene.tool_settings.unified_paint_settings


def color_owner(context):
    """Where the colour a stroke would use is stored: a brush, or the unified settings."""
    settings = paint_struct(context)
    unified = unified_settings(context)
    brush = getattr(settings, 'brush', None)
    if brush is None:
        # No brush in object mode from 4.5 on; the unified colour is all
        # there is to read.
        return unified
    # A brush of its own from 5.3, on the unified settings before that.
    use_unified = getattr(brush, 'use_unified_color', None)
    if use_unified is None:
        use_unified = unified.use_unified_color
    return unified if use_unified else brush


def is_scene_linear(owner) -> bool:
    """Whether *owner*'s colour is scene linear (5.0+) rather than display sRGB."""
    return owner.bl_rna.properties['color'].subtype == 'COLOR'


def stored_fill_color(context, image: bpy.types.Image) -> tuple[float, float, float]:
    """The values one full-strength dab of the current colour would store in *image*.

    Raises `Refused` for a byte image in a colour space Python cannot
    convert into, where a Fill could not match a stroke.
    """
    owner = color_owner(context)
    color = Color(owner.color[:3])
    space = image.colorspace_settings.name
    data = space in DATA_SPACES or getattr(image.colorspace_settings, 'is_data', False)
    if not is_scene_linear(owner):
        # Display sRGB: a stroke writes it into a byte image as it is.
        if image.is_float:
            color = color.from_srgb_to_scene_linear()
        return tuple(color)
    if image.is_float or data or space == "Linear Rec.709":
        return tuple(color)
    if space == "sRGB":
        return tuple(color.from_scene_linear_to_srgb())
    if space == "ACEScg" and hasattr(color, 'from_scene_linear_to_acescg'):
        return tuple(color.from_scene_linear_to_acescg())
    raise Refused(f"Fill cannot match a stroke on a byte image in the {space} colour space yet")
