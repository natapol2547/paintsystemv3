"""Clip native brush strokes in the 3D view to the selection (PS-091, slice 3).

Blender's texture paint Stencil Mask protects the canvas wherever its
image is white, sampled through the mesh's stencil UV map. With
`invert_stencil` on it lets paint through in proportion to the image's
value instead, which is what a selection mask is: 1 selected, 0 not,
feathered in between. So while there is a selection in texture paint
mode, the scene's stencil settings belong to it:

* the stencil image is `.PS Selection Stencil`, a Non-Color PNG read from
  the session temporary directory. A generated image would be dirty after
  every write, and Blender then packs it on "Save All Images" and asks to
  save it on quit; clearing the flag by packing costs up to a second at
  4K. A file image is never dirty, and is named after the mask digest, so
  an undo step that restores an older file path restores the matching
  pixels too. Automatically Pack Resources and Pack Resources still pack
  it, and a packed image reloads its packed copy, so it is unpacked
  before it is pointed at another file.
* when a selection exists but its mask cannot be used, the stencil image
  is a single black pixel, which blocks painting altogether rather than
  letting it land outside the selection. When not even that file can be
  written, `.PS Selection Block`, a generated black image, blocks instead:
  it holds no pixels of its own, so it is never dirty either.
* the user's settings from before are backed up (`props/stencil.py`) and
  restored when the selection stops applying, before a file is saved and
  when the add-on is unregistered. User edits made while the selection
  holds them are set back on the next sync. A scene copied from one the
  selection holds copies the selection's settings, not the user's, so
  its backup is Blender's defaults.

`selection.session` decides when: its tick calls `sync` with the state
it resolved, and `handlers.node_tree_handlers` calls `restore_all`,
`on_file_loaded` and a forced session sync around saving and loading.
The message bus subscriptions here only cover the settings this module
owns. Writes only happen where a value differs, so the notifications
they cause settle after one more sync.
"""
import logging
import os
import shutil
import struct
import zlib

import bpy
import numpy as np

from . import raster

log = logging.getLogger(__name__)

IMAGE_KEY = "ps_selection_stencil"
IMAGE_NAME = ".PS Selection Stencil"
BLOCK_KEY = "ps_selection_block"
BLOCK_NAME = ".PS Selection Block"
FILE_DIR = "paint_system_selection"
BLOCK_FILE = "block.png"
FILE_BUDGET = 256 << 20
"""Bytes of mask files kept in the temporary directory before the oldest go."""

_msgbus_owner = object()
# File path the pixels of each stencil image were last loaded from, by
# `session_uid`. Undo restores `Image.filepath` but not the pixels.
_loaded: dict[int, str] = {}


def _file_dir() -> str:
    return os.path.join(bpy.app.tempdir, FILE_DIR)


def _write_png(path: str, grey: np.ndarray) -> None:
    """Write a greyscale PNG atomically. *grey* is uint8 (h, w), row 0 at the bottom."""
    height, width = grey.shape
    rows = np.empty((height, width + 1), np.uint8)
    rows[:, 0] = 0
    rows[:, 1:] = grey[::-1]

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    temporary = path + ".tmp"
    with open(temporary, "wb") as file:
        file.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                   + chunk(b"IDAT", zlib.compress(rows.tobytes(), 1)) + chunk(b"IEND", b""))
    os.replace(temporary, path)


def _prune(keep: str) -> None:
    """Delete the least recently written mask files beyond `FILE_BUDGET`."""
    entries = []
    for entry in os.scandir(_file_dir()):
        if entry.is_file() and entry.path != keep and entry.name != BLOCK_FILE:
            stat = entry.stat()
            entries.append((stat.st_mtime, stat.st_size, entry.path))
    total = sum(size for _, size, _ in entries)
    for _, size, path in sorted(entries):
        if total <= FILE_BUDGET:
            break
        os.remove(path)
        total -= size


def _mask_file(selection, size: tuple[int, int], tile: int) -> str | None:
    """The PNG of the selection's mask, written if missing, or `_block_file()` when it cannot be.

    None when not even the block file can be written.
    """
    width, height = raster.mask_size(size)
    # Named by the digest with surface keys, so a VIEW selection gets a new
    # file when the surface it was drawn on changes.
    digest = selection.prefix_digests(width, height, tile, surface_key=raster.view_key)[-1]
    path = os.path.join(_file_dir(), digest.hex() + ".png")
    if os.path.exists(path):
        return path
    try:
        mask = raster.peek_mask(selection, size, tile) or raster.get_mask(selection, size, tile)
        grey = mask.read_bytes()
        os.makedirs(_file_dir(), exist_ok=True)
        _write_png(path, grey)
        _prune(path)
    except raster.MaskUnavailable as error:
        log.warning("Selection blocks painting: %s", error)
        return _block_file()
    except OSError as error:
        log.warning("Selection blocks painting, its stencil could not be written: %s", error)
        return _block_file()
    return path


def _block_file() -> str | None:
    """A 1x1 black PNG: with the stencil inverted, nothing gets painted. None when it cannot be written."""
    path = os.path.join(_file_dir(), BLOCK_FILE)
    if not os.path.exists(path):
        try:
            os.makedirs(_file_dir(), exist_ok=True)
            _write_png(path, np.zeros((1, 1), np.uint8))
        except OSError as error:
            log.warning("Selection blocks painting without a file, none could be written: %s", error)
            return None
    return path


def stencil_image() -> bpy.types.Image | None:
    """The image the selection stencils with, if it exists."""
    return next((image for image in bpy.data.images if image.get(IMAGE_KEY)), None)


def _is_selection_image(image) -> bool:
    return image is not None and bool(image.get(IMAGE_KEY) or image.get(BLOCK_KEY))


def _block_image() -> bpy.types.Image:
    """The generated black image that blocks painting when no file can be written, created if needed."""
    image = next((image for image in bpy.data.images if image.get(BLOCK_KEY)), None)
    if image is None:
        image = bpy.data.images.new(BLOCK_NAME, 1, 1)
        image[BLOCK_KEY] = True
        image.generated_color = (0.0, 0.0, 0.0, 1.0)
        image.colorspace_settings.name = 'Non-Color'
    return image


def _show_file(path: str) -> bpy.types.Image:
    """The stencil image, created if needed, showing the pixels of *path*."""
    image = stencil_image()
    if image is None:
        image = bpy.data.images.new(IMAGE_NAME, 1, 1)
        image[IMAGE_KEY] = True
        image.source = 'FILE'
        image.colorspace_settings.name = 'Non-Color'
    if image.packed_file is not None:
        # A packed image reloads its packed copy whatever its file path.
        image.unpack(method='REMOVE')
        _loaded.pop(image.session_uid, None)
    if image.filepath != path:
        image.filepath = path
    if _loaded.get(image.session_uid) != path or not image.has_data:
        image.reload()
        # Reading the size loads the file now rather than on the first stroke.
        image.size[0]
        _loaded[image.session_uid] = path
    return image


def _set(owner, name: str, value) -> None:
    if getattr(owner, name) != value:
        setattr(owner, name, value)


def _settings():
    return bpy.context.window_manager.paint_system


def _hold(scene, mesh) -> None:
    """Back up the settings the selection is about to take, once per holding."""
    settings = _settings()
    index = settings.find_scene(scene)
    if index < 0:
        image_paint = scene.tool_settings.image_paint
        entry = settings.stencil_scenes.add()
        entry.scene = scene
        # A scene copied while the selection held its source carries the
        # selection's settings; the entry's defaults are Blender's.
        if not _is_selection_image(image_paint.stencil_image):
            entry.use_stencil_layer = image_paint.use_stencil_layer
            entry.invert_stencil = image_paint.invert_stencil
            entry.stencil_image = image_paint.stencil_image
        index = len(settings.stencil_scenes) - 1
    if mesh is None:
        return
    meshes = scene.paint_system.stencil_meshes
    backup = next((entry for entry in meshes if entry.mesh == mesh), None)
    if backup is None:
        backup = meshes.add()
        backup.mesh = mesh
        stencil_uv = mesh.uv_layer_stencil
        backup.uv_name = stencil_uv.name if stencil_uv is not None else ""
    # The scene's backup is the one undo restores along with the mesh. The
    # window manager keeps a copy for when the scene is removed while it holds.
    kept = settings.stencil_scenes[index].meshes
    if not any(entry.mesh == mesh for entry in kept):
        entry = kept.add()
        entry.mesh = mesh
        entry.uv_name = backup.uv_name


def is_applied(scene) -> bool:
    """Whether the scene's stencil settings currently belong to the selection. Safe from draw code."""
    return _settings().find_scene(scene) >= 0


def _restore_meshes(meshes) -> None:
    """Give the meshes in the backup collection *meshes* their stencil UV maps back, and empty it."""
    for entry in meshes:
        mesh = entry.mesh
        index = mesh.uv_layers.find(entry.uv_name) if mesh is not None else -1
        if index >= 0:
            _set(mesh, 'uv_layer_stencil_index', index)
    if len(meshes):
        meshes.clear()


def restore(scene) -> None:
    """Give the scene's stencil settings back to the user."""
    settings = _settings()
    index = settings.find_scene(scene)
    if index >= 0:
        entry = settings.stencil_scenes[index]
        image_paint = scene.tool_settings.image_paint
        _set(image_paint, 'use_stencil_layer', entry.use_stencil_layer)
        _set(image_paint, 'invert_stencil', entry.invert_stencil)
        _set(image_paint, 'stencil_image', entry.stencil_image)
        settings.stencil_scenes.remove(index)
    _restore_meshes(scene.paint_system.stencil_meshes)


def _forget_removed_scenes() -> None:
    """Drop the entries of scenes that are gone, giving their meshes back from the window manager's copy."""
    scenes = _settings().stencil_scenes
    for index in reversed(range(len(scenes))):
        if scenes[index].scene is None:
            _restore_meshes(scenes[index].meshes)
            scenes.remove(index)


def restore_all() -> None:
    """Give every scene's stencil settings back; before a save and on unregister."""
    _forget_removed_scenes()
    for scene in bpy.data.scenes:
        restore(scene)


def _recover(scene) -> None:
    """Undo what a file saved without `restore_all`, as an autosave, still holds.

    The user's settings went with the session, so the stencil goes back to
    Blender's defaults.
    """
    image_paint = scene.tool_settings.image_paint
    if not _is_selection_image(image_paint.stencil_image):
        return
    image_paint.stencil_image = None
    image_paint.use_stencil_layer = False
    image_paint.invert_stencil = False
    _restore_meshes(scene.paint_system.stencil_meshes)


def sync(state, target) -> None:
    """Make the stencil settings match the session's *state*.

    *target* is the `session.Target` *state* was resolved from, or None.
    The selection applies in texture paint mode: through its mask file
    when the mask is available, blocking all paint when `state.reason`
    says why it is not. A mask that selects nothing (`state.empty`) is no
    selection, so the user's settings come back.
    """
    scene = bpy.context.scene
    # Tool settings are per scene: another scene keeps nothing of the selection's.
    _forget_removed_scenes()
    scenes = _settings().stencil_scenes
    for index in reversed(range(len(scenes))):
        if scenes[index].scene != scene:
            restore(scenes[index].scene)
    if not (state.paint_mode and state.selected) or state.empty:
        restore(scene)
        return
    if state.reason:
        path = _block_file()
    else:
        path = _mask_file(target.tree.selection, state.size, state.tile)
    image = _show_file(path) if path is not None else _block_image()
    mesh = target.object.data if target is not None and target.object is not None else None
    _hold(scene, mesh)
    image_paint = scene.tool_settings.image_paint
    _set(image_paint, 'use_stencil_layer', True)
    _set(image_paint, 'invert_stencil', True)
    _set(image_paint, 'stencil_image', image)
    if mesh is not None:
        index = mesh.uv_layers.find(state.uv_map)
        if index >= 0:
            _set(mesh, 'uv_layer_stencil_index', index)


def on_file_loaded() -> None:
    """Forget the previous file's stencil and release what a new one still holds; on load."""
    _loaded.clear()
    _settings().stencil_scenes.clear()
    for scene in bpy.data.scenes:
        _recover(scene)
    subscribe()


def draw_image_header(self, context) -> None:
    """Tell image editor painters that the selection does not clip strokes there."""
    space = context.space_data
    if space is None or space.mode != 'PAINT' or not is_applied(context.scene):
        return
    self.layout.label(text="Selection does not limit painting here", icon='INFO')


def _on_settings_changed(*args) -> None:
    # Imported here: the session imports this module. A forced sync sets
    # the settings back even though the session's state is unchanged.
    from . import session
    session.notify(force=True)


def subscribe() -> None:
    """(Re)subscribe to the settings the selection owns; the message bus forgets subscribers on file load."""
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    keys = (
        (bpy.types.ImagePaint, "use_stencil_layer"),
        (bpy.types.ImagePaint, "invert_stencil"),
        (bpy.types.ImagePaint, "stencil_image"),
        (bpy.types.Mesh, "uv_layer_stencil_index"),
    )
    for key in keys:
        bpy.msgbus.subscribe_rna(key=key, owner=_msgbus_owner, args=(), notify=_on_settings_changed)


def register() -> None:
    subscribe()
    bpy.types.IMAGE_HT_tool_header.append(draw_image_header)


def unregister() -> None:
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    bpy.types.IMAGE_HT_tool_header.remove(draw_image_header)
    restore_all()
    for image in [image for image in bpy.data.images if _is_selection_image(image)]:
        bpy.data.images.remove(image)
    _loaded.clear()
    shutil.rmtree(_file_dir(), ignore_errors=True)
