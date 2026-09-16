"""The selection clips native strokes through Blender's Stencil Mask (PS-091, slice 3).

`selection.stencil` writes the mask of the live selection to a PNG in the
session temporary directory, points the scene's stencil at it and gives
the user's settings back when the selection stops applying. Background
Blender runs no timers and no message bus, so these tests resolve the
session state themselves and pass it to `stencil.sync`. Checks that
compare the stencil with a built mask need a GPU context, which a
background session only has from Blender 5.2 on; without one the session
reports `NO_GPU` and the stencil blocks painting, which the remaining
checks cover.
"""
import dataclasses
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, guarded, import_from, register_addon, section, skip  # noqa: E402

register_addon()
core = import_from("gpu_passes.core")
raster = import_from("selection.raster")
session = import_from("selection.session")
stencil = import_from("selection.stencil")

LAYER_SIZE = (256, 128)
USER_STENCIL = "PS Stencil User"


def cube():
    return bpy.data.objects["Cube"]


def tree():
    return cube().active_material.paint_system.tree


def image_paint():
    return bpy.context.scene.tool_settings.image_paint


def backups():
    return bpy.context.window_manager.paint_system.stencil_scenes


def setup():
    obj = cube()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.paint_system.setup_material()
    bpy.ops.paint_system.add_layer(layer_type='IMAGE', resolution='1024')
    tree().nodes.active.image = bpy.data.images.new("PS Stencil Layer", *LAYER_SIZE)
    obj.data.uv_layers.new(name="UV2")
    bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode='TEXTURE_PAINT')


def set_user_stencil():
    """The stencil the user had before any selection: an image, not inverted, through UV2."""
    user = bpy.data.images.get(USER_STENCIL) or bpy.data.images.new(USER_STENCIL, 8, 8)
    settings = image_paint()
    settings.use_stencil_layer = False
    settings.invert_stencil = False
    settings.stencil_image = user
    cube().data.uv_layer_stencil_index = 1
    return user


def user_stencil_back():
    settings = image_paint()
    return (not settings.use_stencil_layer and not settings.invert_stencil
            and settings.stencil_image is not None and settings.stencil_image.name == USER_STENCIL
            and cube().data.uv_layer_stencil_index == 1)


def apply():
    """What the session's tick does after an undo: a sync that reaches the stencil even when nothing changed."""
    return session.sync(force=True)


def pixels(image):
    width, height = image.size
    values = np.empty(width * height * 4, np.float32)
    image.pixels.foreach_get(values)
    return values.reshape(height, width, 4)


def stencil_bytes():
    """The red channel of the stencil image as the bytes its file holds."""
    return np.rint(pixels(stencil.stencil_image())[..., 0] * 255).astype(np.uint8)


def mask_bytes(state):
    return raster.get_mask(tree().selection, state.size, state.tile).read_bytes()


def file_name():
    return os.path.basename(stencil.stencil_image().filepath)


def mask_file(state):
    return os.path.join(stencil._file_dir(), state.digest.hex() + ".png")


def remove_mask_file(state):
    """Delete the file of *state*'s mask, if a sync wrote it, so the next sync writes it again."""
    if os.path.exists(mask_file(state)):
        os.remove(mask_file(state))


@contextmanager
def patched(owner, name, value):
    original = getattr(owner, name)
    setattr(owner, name, value)
    try:
        yield
    finally:
        setattr(owner, name, original)


class FakeMask:
    """Stands in for a built mask where the test is about the file, not the GPU."""

    def __init__(self, size, value):
        self.size = size
        self.value = value

    def read_bytes(self):
        return np.full((self.size[1], self.size[0]), self.value, np.uint8)


setup()
# Starts the background GPU context where one exists (5.2 and later).
HAS_GPU = core.gpu_available()


def test_png_round_trip():
    section("mask PNGs load back exactly")
    directory = tempfile.mkdtemp(prefix="ps_stencil_png_")
    width, height = 37, 23
    ramp = ((np.arange(width)[None, :] * 3 + np.arange(height)[:, None] * 7) % 256).astype(np.uint8)
    noise = np.random.default_rng(91).integers(0, 256, (height, width), dtype=np.uint8)
    for label, grey in (("ramp", ramp), ("random", noise)):
        path = os.path.join(directory, f"{label}.png")
        stencil._write_png(path, grey)
        image = bpy.data.images.load(path)
        image.colorspace_settings.name = 'Non-Color'
        loaded = pixels(image)
        got = np.rint(loaded[..., 0] * 255).astype(np.uint8)
        check(tuple(image.size) == (width, height) and np.array_equal(got, grey),
              f"{label}: every value reads back exactly, row 0 at the bottom")
        check(not image.is_dirty, f"{label}: the loaded image is not dirty")
        bpy.data.images.remove(image)
    check(not any(name.endswith(".tmp") for name in os.listdir(directory)), "no temporary file is left behind")
    shutil.rmtree(directory)


def test_apply_and_restore():
    section("a selection takes the stencil and gives the user's back")
    set_user_stencil()
    t = tree()
    t.selection.clear()
    apply()
    check(user_stencil_back() and not len(backups()), "no selection leaves the user's stencil alone")

    t.selection.add_op('BOX', points=[(0.2, 0.1), (0.7, 0.8)], feather=4.0)
    state = apply()
    settings = image_paint()
    image = stencil.stencil_image()
    check(settings.use_stencil_layer and settings.invert_stencil and settings.stencil_image == image
          and image is not None and image.name == stencil.IMAGE_NAME,
          "the stencil is on, inverted, with the selection's image")
    check(cube().data.uv_layer_stencil_index == cube().data.uv_layers.find(state.uv_map) == 0,
          f"through the layer's UV map ({state.uv_map})")
    check(image.colorspace_settings.name == 'Non-Color' and image.source == 'FILE' and not image.is_dirty,
          "the image is a clean Non-Color file image")
    expected = stencil.BLOCK_FILE if state.reason else state.digest.hex() + ".png"
    check(file_name() == expected, f"it reads {expected} ({state.reason or 'mask available'})")
    check(stencil.is_applied(bpy.context.scene) and len(backups()) == 1
          and [(entry.mesh, entry.uv_name) for entry in bpy.context.scene.paint_system.stencil_meshes]
          == [(cube().data, "UV2")], "the user's settings and stencil UV map are backed up")

    t.selection.clear()
    apply()
    check(user_stencil_back(), "clearing the selection restores the user's exact settings")
    check(not len(backups()) and not len(bpy.context.scene.paint_system.stencil_meshes)
          and not stencil.is_applied(bpy.context.scene), "and empties the backups")

    t.selection.add_op('ALL')
    apply()
    bpy.ops.object.mode_set(mode='OBJECT')
    apply()
    check(user_stencil_back(), "leaving texture paint mode restores them too")
    bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    apply()
    check(stencil.is_applied(bpy.context.scene), "and entering it again applies the selection")
    t.selection.clear()
    apply()


def test_session_reaches_the_stencil():
    section("the session hands each new state to the stencil once")
    tree().selection.add_op('BOX', points=[(0.3, 0.3), (0.6, 0.6)])
    apply()
    calls = []
    original = stencil.sync

    def counting(state, target):
        calls.append(state)
        original(state, target)

    with patched(stencil, "sync", counting):
        session.sync()
        check(not calls, "an unchanged state does not reach it")
        tree().selection.invert()
        state = session.sync()
        check(len(calls) == 1 and calls[0] == state,
              f"an edit reaches it exactly once, with the new state ({len(calls)} calls)")
    tree().selection.clear()
    apply()


def test_second_sync_writes_nothing():
    section("syncing an applied selection again writes nothing")
    set_user_stencil()
    tree().selection.add_op('BOX', points=[(0.1, 0.1), (0.5, 0.9)], feather=2.0)
    apply()
    writes = []
    original = stencil._set

    def counting(owner, name, value):
        if getattr(owner, name) != value:
            writes.append(name)
        original(owner, name, value)

    with patched(stencil, "_set", counting):
        apply()
    check(not writes, f"no setting is written again ({writes})")
    tree().selection.clear()
    apply()


def test_digest_files_are_reused():
    section("a mask file is written once per digest")
    if not HAS_GPU:
        skip("mask files need a GPU context (background Blender before 5.2)")
        return
    t = tree()
    t.selection.clear()
    t.selection.add_op('BOX', points=[(0.1, 0.2), (0.6, 0.7)], feather=6.0)
    state_a = apply()
    check(file_name() == state_a.digest.hex() + ".png" and np.array_equal(stencil_bytes(), mask_bytes(state_a)),
          "selection A: the stencil holds its mask")
    t.selection.add_op('ELLIPSE', mode='ADD', points=[(0.5, 0.1), (0.9, 0.5)], feather=3.0)
    state_b = apply()
    check(file_name() == state_b.digest.hex() + ".png" and np.array_equal(stencil_bytes(), mask_bytes(state_b)),
          "selection B: the stencil holds its mask")
    written = []
    original = stencil._write_png

    def counting(path, grey):
        written.append(path)
        original(path, grey)

    t.selection.ops.remove(len(t.selection.ops) - 1)
    with patched(stencil, "_write_png", counting):
        again = apply()
    check(again.digest == state_a.digest and not written, f"back to A reuses its file ({len(written)} writes)")
    check(np.array_equal(stencil_bytes(), mask_bytes(again)), "and the stencil shows A's pixels again")


def test_stale_pixels_reload():
    section("a file path changed behind the module's back is reloaded")
    if not HAS_GPU:
        skip("mask files need a GPU context (background Blender before 5.2)")
        return
    t = tree()
    t.selection.clear()
    t.selection.add_op('BOX', points=[(0.1, 0.1), (0.4, 0.9)])
    state_a = apply()
    path_a = stencil.stencil_image().filepath
    t.selection.add_op('INVERT')
    state_b = apply()
    # What memfile undo does: the ID's file path goes back, its pixels do
    # not. Setting `filepath` would free the pixels; `filepath_raw` does not.
    stencil.stencil_image().filepath_raw = path_a
    check(np.array_equal(stencil_bytes(), mask_bytes(state_b)), "setting the path alone keeps B's pixels")
    t.selection.ops.remove(len(t.selection.ops) - 1)
    apply()
    check(np.array_equal(stencil_bytes(), mask_bytes(state_a)), "the next sync reloads A's pixels")
    t.selection.clear()
    apply()


def test_block_mode():
    section("a selection that cannot be used blocks painting")
    t = tree()
    t.selection.clear()
    t.selection.add_op('BOX', points=[(0.25, 0.25), (0.75, 0.75)], feather=1.0)
    state = session.sync(force=True)
    target = session.resolve_target(bpy.context)[0]
    remove_mask_file(state)

    def blocked():
        image = stencil.stencil_image()
        return (file_name() == stencil.BLOCK_FILE and tuple(image.size) == (1, 1)
                and stencil_bytes().max() == 0 and image_paint().invert_stencil)

    stencil.sync(dataclasses.replace(state, reason=session.UDIM, message="UDIM"), None)
    check(blocked(), "a target problem gives the black 1x1 stencil")

    usable = dataclasses.replace(state, reason="", message="")

    def unavailable(selection, size, tile=1001):
        raise raster.MaskUnavailable('TOO_COMPLEX', "The lasso outline is too complex to build")

    with patched(raster, "peek_mask", lambda selection, size, tile=1001: None), \
            patched(raster, "get_mask", unavailable):
        stencil.sync(usable, target)
    check(blocked(), "a mask that turns out unavailable blocks")

    def unwritable(path, grey):
        raise OSError("read-only file system")

    with patched(raster, "peek_mask", lambda selection, size, tile=1001: FakeMask(size, 255)), \
            patched(stencil, "_write_png", unwritable):
        stencil.sync(usable, target)
    check(blocked() and not os.path.exists(mask_file(state)), "a mask file that cannot be written blocks")

    # Neither the mask nor block.png can be written: a full disk, or a
    # temporary directory without write permission.
    os.remove(os.path.join(stencil._file_dir(), stencil.BLOCK_FILE))

    def generated_blocks():
        image = image_paint().stencil_image
        return (image is not None and image.get(stencil.BLOCK_KEY) and image.source == 'GENERATED'
                and tuple(image.size) == (1, 1) and pixels(image)[..., :3].max() == 0 and not image.is_dirty
                and image_paint().use_stencil_layer and image_paint().invert_stencil)

    with patched(raster, "peek_mask", lambda selection, size, tile=1001: FakeMask(size, 255)), \
            patched(stencil, "_write_png", unwritable):
        stencil.sync(usable, target)
        check(generated_blocks(), "with no file at all, a generated black image blocks")
        stencil.sync(dataclasses.replace(state, reason=session.UDIM, message="UDIM"), None)
        check(generated_blocks(), "and so it does for a target problem")
        remove_mask_file(state)
        reached = session.sync(force=True)
        check(reached.selected and generated_blocks(),
              f"a session sync blocks too, without raising ({reached.reason})")
    stencil.sync(dataclasses.replace(state, reason=session.UDIM, message="UDIM"), None)
    check(blocked(), "once the file can be written, block.png takes over again")
    t.selection.clear()
    apply()


def test_other_scene_is_restored():
    section("tool settings are per scene")
    set_user_stencil()
    home = bpy.context.scene
    other = bpy.data.scenes.new("PS Stencil Other")
    try:
        tree().selection.add_op('ALL')
        state = apply()
        check(stencil.is_applied(home), "the selection holds the active scene's stencil")
        with bpy.context.temp_override(scene=other):
            stencil.sync(dataclasses.replace(state, reason=session.UDIM, message="UDIM"), None)
        check(user_stencil_back() and not stencil.is_applied(home), "syncing another scene restores the first")
        check(stencil.is_applied(other) and other.tool_settings.image_paint.stencil_image == stencil.stencil_image(),
              "and applies to the other one")
        apply()
        check(stencil.is_applied(home) and not stencil.is_applied(other)
              and other.tool_settings.image_paint.stencil_image is None
              and not other.tool_settings.image_paint.use_stencil_layer,
              "switching back restores the other scene's own settings")
    finally:
        tree().selection.clear()
        apply()
        bpy.data.scenes.remove(other)
    stencil.sync(session.State(paint_mode=True), None)
    check(not len(backups()), "an entry for a removed scene is dropped")


def test_removed_scene_gives_meshes_back():
    section("a scene removed while it holds the stencil")
    set_user_stencil()
    obj = cube()
    other = bpy.data.scenes.new("PS Stencil Removed")
    other.collection.objects.link(obj)
    other.view_layers[0].objects.active = obj
    tree().selection.add_op('ALL')
    try:
        with bpy.context.temp_override(scene=other, view_layer=other.view_layers[0]):
            state = session.sync(force=True)
        check(state.paint_mode and stencil.is_applied(other) and obj.data.uv_layer_stencil_index == 0,
              f"the selection holds the other scene and set the mesh's stencil UV map ({state.reason})")
    finally:
        bpy.data.scenes.remove(other)
    tree().selection.clear()
    apply()
    check(obj.data.uv_layer_stencil_index == 1, "the mesh still gets its stencil UV map back")
    check(not len(backups()), "and the removed scene's entry is dropped")


def test_copied_scene_is_not_backed_up_as_the_users():
    section("a scene copied while the selection holds its stencil")
    set_user_stencil()
    home = bpy.context.scene
    tree().selection.add_op('ALL')
    apply()
    names = set(bpy.data.scenes.keys())
    check(bpy.ops.scene.new(type='LINK_COPY') == {'FINISHED'}, "Linked Copy runs")
    copy = next(scene for scene in bpy.data.scenes if scene.name not in names)
    try:
        copied = copy.tool_settings.image_paint
        check(copied.stencil_image == stencil.stencil_image(), "the copy starts with the selection's stencil")
        with bpy.context.temp_override(scene=copy, view_layer=copy.view_layers[0]):
            apply()
            check(stencil.is_applied(copy) and not stencil.is_applied(home), "the copy is held")
            tree().selection.clear()
            apply()
        check(copied.stencil_image is None and not copied.use_stencil_layer and not copied.invert_stencil,
              "clearing the selection there gives it Blender's defaults, not the selection's stencil")
        check(cube().data.uv_layer_stencil_index == 1, "and the shared mesh its user's stencil UV map")
    finally:
        stencil.restore(copy)
        # A windowed session switches the window to the new scene.
        for window in bpy.context.window_manager.windows:
            if window.scene == copy:
                window.scene = home
        bpy.data.scenes.remove(copy)
    apply()
    check(user_stencil_back(), "the first scene keeps the user's settings")


def test_renamed_mesh_is_restored():
    section("a mesh renamed while held gets its stencil UV map back")
    set_user_stencil()
    mesh = cube().data
    name = mesh.name
    tree().selection.add_op('ALL')
    apply()
    check(mesh.uv_layer_stencil_index == 0, "the selection set the stencil UV map")
    mesh.name = "PS Stencil Renamed"
    impostor = bpy.data.meshes.new(name)
    try:
        tree().selection.clear()
        apply()
        check(mesh.uv_layer_stencil_index == 1, "clearing restores the renamed mesh's UV map")
        check(not len(bpy.context.scene.paint_system.stencil_meshes), "and drops the backup")
    finally:
        bpy.data.meshes.remove(impostor)
        mesh.name = name


def test_resized_image():
    section("a resized layer image gets a mask at its new size")
    layer = tree().nodes.active
    tree().selection.add_op('BOX', points=[(0.2, 0.2), (0.8, 0.8)], feather=2.0)
    apply()
    layer.image.scale(128, 64)
    state = apply()
    check(state.size == (128, 64), f"the state follows the new size ({state.size})")
    if HAS_GPU:
        check(tuple(stencil.stencil_image().size) == (128, 64) and file_name() == state.digest.hex() + ".png",
              f"and so does the stencil image ({tuple(stencil.stencil_image().size)})")
    else:
        skip("mask files need a GPU context (background Blender before 5.2)")
    layer.image.scale(*LAYER_SIZE)
    tree().selection.clear()
    apply()


def test_prune_keeps_current_and_block():
    section("old mask files are pruned past the budget")
    directory = stencil._file_dir()
    stencil._block_file()
    old = []
    for index in range(3):
        path = os.path.join(directory, f"{index:040x}.png")
        with open(path, "wb") as file:
            file.write(b"\0" * 1024)
        os.utime(path, (1000 + index, 1000 + index))
        old.append(path)
    tree().selection.add_op('BOX', points=[(0.3, 0.3), (0.35, 0.35)])
    state = dataclasses.replace(session.sync(force=True), reason="", message="")
    remove_mask_file(state)
    with patched(stencil, "FILE_BUDGET", 1), \
            patched(raster, "peek_mask", lambda selection, size, tile=1001: FakeMask(size, 128)):
        stencil.sync(state, session.resolve_target(bpy.context)[0])
    current = mask_file(state)
    check(not any(os.path.exists(path) for path in old), "older files beyond the budget are deleted")
    check(os.path.exists(current) and os.path.exists(os.path.join(directory, stencil.BLOCK_FILE)),
          "the current file and block.png stay")
    os.remove(current)
    tree().selection.clear()
    apply()


def test_file_recovery():
    section("a file saved while the selection held the stencil is recovered")
    set_user_stencil()
    tree().selection.add_op('ALL')
    apply()
    # As read back from an autosave: the stencil is still the selection's,
    # and the window manager's backup did not come with the file.
    backups().clear()
    stencil.on_file_loaded()
    settings = image_paint()
    check(settings.stencil_image is None and not settings.use_stencil_layer and not settings.invert_stencil,
          "the stencil goes back to Blender's defaults")
    check(cube().data.uv_layer_stencil_index == 1 and not len(bpy.context.scene.paint_system.stencil_meshes),
          "the mesh gets its stencil UV map back")
    check(not stencil._loaded, "and the loaded file paths are forgotten")
    apply()
    check(stencil.is_applied(bpy.context.scene), "the next sync applies the selection again")
    tree().selection.clear()
    apply()


def save(path):
    """Save through the add-on's handlers: the user's stencil goes into the file, then the selection takes it back."""
    bpy.ops.wm.save_as_mainfile(filepath=path)


def test_autopack():
    section("a packed stencil image still follows the selection")
    if not HAS_GPU:
        skip("mask files need a GPU context (background Blender before 5.2)")
        return
    directory = tempfile.mkdtemp(prefix="ps_stencil_pack_")
    t = tree()
    t.selection.add_op('BOX', points=[(0.1, 0.1), (0.5, 0.5)], feather=3.0)
    apply()
    bpy.data.use_autopack = True
    try:
        save(os.path.join(directory, "autopack.blend"))
        t.selection.invert()
        state = apply()
        check(np.array_equal(stencil_bytes(), mask_bytes(state)),
              "after an autopack save, a new selection shows its own mask")
        bpy.data.use_autopack = False
        bpy.ops.file.pack_all()
        check(stencil.stencil_image().packed_file is not None, "Pack Resources packs the stencil image")
        t.selection.invert()
        state = apply()
        check(np.array_equal(stencil_bytes(), mask_bytes(state)),
              "after Pack Resources, a new selection shows its own mask")
    finally:
        bpy.data.use_autopack = False
        t.selection.clear()
        apply()
        shutil.rmtree(directory)


def test_save_and_load():
    section("saving leaves the stencil out of the file")
    set_user_stencil()
    directory = tempfile.mkdtemp(prefix="ps_stencil_save_")
    path = os.path.join(directory, "stencil.blend")
    tree().selection.clear()
    apply()
    save(path)
    # Blender 4.2 in the background reports a file dirty right after any save.
    plain_save_is_clean = not bpy.data.is_dirty
    tree().selection.add_op('BOX', points=[(0.2, 0.2), (0.6, 0.6)])
    apply()
    save(path)
    if plain_save_is_clean:
        check(not bpy.data.is_dirty, "the file is not dirty after the save")
    else:
        skip("a save without a selection leaves this session dirty too")
    check(stencil.is_applied(bpy.context.scene) and image_paint().stencil_image == stencil.stencil_image(),
          "the selection holds the stencil again right after saving")
    copy = os.path.join(directory, "copy.blend")
    shutil.copy(path, copy)
    with bpy.data.libraries.load(copy) as (data_from, _):
        saved_images = list(data_from.images)
    check(stencil.IMAGE_NAME not in saved_images, f"the saved file has no stencil image ({saved_images})")

    check(stencil._loaded, "the stencil image has loaded a mask file")
    bpy.ops.wm.open_mainfile(filepath=path)
    check(not stencil._loaded, "the load handler forgets the loaded file paths")
    shutil.rmtree(directory)


def test_reopened_file():
    section("a reopened file")
    check(user_stencil_back(), "holds the user's stencil settings")
    apply()
    check(stencil.is_applied(bpy.context.scene), "and its selection applies again")


def test_unregister():
    section("unregister gives everything back")
    set_user_stencil()
    home = bpy.context.scene
    other = bpy.data.scenes.new("PS Stencil Unregister")
    tree().selection.add_op('ALL')
    apply()
    other_paint = other.tool_settings.image_paint
    stencil._hold(other, None)
    other_paint.use_stencil_layer = True
    other_paint.stencil_image = stencil.stencil_image()
    stencil._block_image()
    directory = stencil._file_dir()
    check(os.path.isdir(directory) and stencil.is_applied(home) and stencil.is_applied(other), "two scenes are held")
    stencil.unregister()
    check(user_stencil_back() and not other_paint.use_stencil_layer and other_paint.stencil_image is None,
          "every scene has its own settings back")
    check(stencil.stencil_image() is None and not os.path.exists(directory)
          and not any(image.get(stencil.BLOCK_KEY) for image in bpy.data.images),
          "the stencil and block images and the mask files are gone")
    check(not len(backups()), "no backup is left")


guarded(test_png_round_trip)
guarded(test_apply_and_restore)
guarded(test_session_reaches_the_stencil)
guarded(test_second_sync_writes_nothing)
guarded(test_digest_files_are_reused)
guarded(test_stale_pixels_reload)
guarded(test_block_mode)
guarded(test_other_scene_is_restored)
guarded(test_removed_scene_gives_meshes_back)
guarded(test_copied_scene_is_not_backed_up_as_the_users)
guarded(test_renamed_mesh_is_restored)
guarded(test_resized_image)
guarded(test_prune_keeps_current_and_block)
guarded(test_file_recovery)
guarded(test_autopack)
guarded(test_save_and_load)
# A script that opens a file in a windowed session is left with a context
# that has no window, and so no active object.
with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
    guarded(test_reopened_file)
    guarded(test_unregister)
# GPU textures still referenced when Python exits are freed after the GPU context (see test_selection_raster).
session.release()
raster.release()
finish("SELECTION STENCIL TEST")
