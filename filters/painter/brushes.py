# SPDX-License-Identifier: GPL-3.0-or-later
"""The brushes a painter layer stamps with (PS-053).

Two presets ship with the add-on, the two v2 did, alongside v2's default
circle. A brush is the alpha channel of its image, centred on a square,
as v2 read one.

A preset is read from disk once per session and kept. A filter layer
rebuilds on its own after every change below it, and loading twenty-one
PNGs each time would cost more than the painting does.
"""
from __future__ import annotations

import logging
import os

import bpy
import numpy as np

from ..core import Refused
from . import plan

log = logging.getLogger(__name__)

CIRCLE = 'CIRCLE'

# Identifier, label, and the folder under ``presets`` holding the images.
PRESETS = (
    ('GOUACHE_SHORT_1', "Gouache Short 1", "gouache_short_1"),
    ('GOUACHE_SHORT_2', "Gouache Short 2", "gouache_short_2"),
)

_FOLDER = os.path.join(os.path.dirname(__file__), "presets")
_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")

_cache: dict[str, list[np.ndarray]] = {}


def brush_items() -> list[tuple[str, str, str]]:
    """``EnumProperty`` items for a painter layer's brush."""
    items = [(name, label, f"Stamp with the {label} brushes")
             for name, label, _folder in PRESETS]
    items.append((CIRCLE, "Circle", "Stamp with a soft round brush"))
    return items


def masks(name: str) -> list[np.ndarray]:
    """The brushes of *name*, square float32 masks with row 0 at the bottom."""
    if name == CIRCLE:
        return [plan.circle()]
    if name not in _cache:
        folder = next((folder for preset, _label, folder in PRESETS if preset == name), None)
        if folder is None:
            raise Refused(f"There is no brush preset called {name}")
        _cache[name] = _load_folder(os.path.join(_FOLDER, folder))
    return _cache[name]


def _load_folder(folder: str) -> list[np.ndarray]:
    files = sorted(entry for entry in os.listdir(folder)
                   if entry.lower().endswith(_EXTENSIONS))
    loaded = [_load_file(os.path.join(folder, entry)) for entry in files]
    loaded = [mask for mask in loaded if mask is not None]
    if not loaded:
        raise Refused(f"The brush preset in {folder} has no images it can read")
    return loaded


def _load_file(path: str) -> np.ndarray | None:
    """The mask of the image at *path*, through a datablock that is removed again.

    Blender has no way to decode an image without one. It is loaded
    fresh rather than found, so that removing it cannot take away an
    image the file was already using.
    """
    try:
        image = bpy.data.images.load(path, check_existing=False)
    except RuntimeError as error:
        log.warning("Could not read the brush %s: %s", path, error)
        return None
    try:
        return mask_of(image)
    finally:
        bpy.data.images.remove(image)


def mask_of(image: bpy.types.Image) -> np.ndarray:
    """*image* as a brush, clamped and centred on a square.

    The alpha channel where there is one, and the luminance where there
    is not, which is how v2 read a brush.
    """
    width, height = image.size
    channels = image.channels
    values = np.empty(width * height * channels, dtype=np.float32)
    image.pixels.foreach_get(values)
    values = values.reshape(height, width, channels)
    if channels >= 4:
        mask = values[..., 3]
    elif channels == 3:
        mask = values @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    else:
        mask = values[..., 0]
    return plan.square(np.clip(mask, 0.0, 1.0).astype(np.float32))


def release() -> None:
    _cache.clear()
