"""Layer images keep their painted pixels across save and reopen (PS-056).

Sets up the factory cube with image layers whose images cover each save
path: a managed image made by the addon, an image on disk, a packed image
that also has a file path, an image whose directory cannot be created and
a generated image the addon did not make, held as a layer's cache. Paints
them, saves, and checks where every image's pixels went, then reopens the
file and reads them back. Images no Paint System node uses, and images
without unsaved changes, must be left alone.

File load frees every ID, so nothing is kept across the reopen; images are
looked up again by name.
"""
import logging
import os
import sys
import tempfile

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, close, finish, fmt, guarded, import_from, register_addon, section  # noqa: E402

register_addon()
node_tree_handlers = import_from("handlers.node_tree_handlers")

RED = (1.0, 0.0, 0.0, 1.0)
GREEN = (0.0, 1.0, 0.0, 1.0)
BLUE = (0.0, 0.0, 1.0, 1.0)
SIZE = 8
OLD_MTIME = 1_000_000_000


class Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def cube():
    return bpy.data.objects["Cube"]


def active_tree():
    mat = cube().active_material
    return mat.paint_system.tree if mat else None


def run(op, **props):
    return op('EXEC_DEFAULT', True, **props) == {'FINISHED'}


def image_layers(tree):
    return [item.node for item in tree.stack() if item.node.bl_idname == 'PaintSystemImageLayerNode']


def fill(image, color):
    image.pixels.foreach_set(np.tile(np.asarray(color, dtype=np.float32), image.size[0] * image.size[1]))
    image.update()


def first_pixel(image):
    return tuple(image.pixels[0:4])


def write_png(path, color):
    image = bpy.data.images.new("PNG Writer", SIZE, SIZE, alpha=True)
    image.filepath_raw = path
    image.file_format = 'PNG'
    fill(image, color)
    image.save()
    bpy.data.images.remove(image)


def load(path):
    return bpy.data.images.load(path, check_existing=False)


def disk_pixel(path):
    image = load(path)
    pixel = first_pixel(image)
    bpy.data.images.remove(image)
    return pixel


def test_save_and_reopen():
    folder = tempfile.mkdtemp(prefix="ps_images_")
    blocker = os.path.join(folder, "blocker")
    with open(blocker, "w") as file:
        file.write("A file where the broken image expects a directory.")
    paths = {name: os.path.join(folder, f"{name}.png") for name in ("disk", "packed", "broken", "clean")}
    for path in paths.values():
        write_png(path, RED)
    broken_path = os.path.join(blocker, "missing", "broken.png")

    section("setup")
    bpy.context.view_layer.objects.active = cube()
    check(run(bpy.ops.paint_system.setup_material), "setup_material finished")
    for _ in range(5):
        check(run(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024'), "add image layer")
    check(run(bpy.ops.paint_system.add_layer, layer_type='SOLID_COLOR'), "add solid layer")
    tree = active_tree()
    managed_layer, disk_layer, packed_layer, broken_layer, clean_layer = image_layers(tree)
    solid = next(item.node for item in tree.stack() if item.node.bl_idname == 'PaintSystemSolidColorLayerNode')

    managed = managed_layer.image
    check(managed is not None and managed.get('ps_managed') and not managed.filepath,
          "the first layer keeps its managed image without a file")
    disk = disk_layer.image = load(paths["disk"])
    packed = packed_layer.image = load(paths["packed"])
    packed.pack()
    broken = broken_layer.image = load(paths["broken"])
    clean = clean_layer.image = load(paths["clean"])
    generated = solid.cache_image = bpy.data.images.new("Generated Cache", SIZE, SIZE, alpha=True)
    unrelated = bpy.data.images.new("Unrelated", SIZE, SIZE, alpha=True)
    unrelated.use_fake_user = True
    names = {image: image.name for image in (managed, disk, packed, broken, clean, generated, unrelated)}

    fill(managed, BLUE)
    fill(disk, GREEN)
    fill(packed, BLUE)
    fill(broken, GREEN)
    broken.filepath_raw = broken_path
    fill(generated, BLUE)
    fill(unrelated, BLUE)
    first_pixel(clean)
    os.utime(paths["clean"], (OLD_MTIME, OLD_MTIME))
    painted = (managed, disk, packed, broken, generated, unrelated)
    check(all(image.is_dirty for image in painted), "painting marks every painted image dirty")
    check(not clean.is_dirty, "the unpainted image is clean")

    section("save")
    records = Records()
    node_tree_handlers.log.addHandler(records)
    try:
        saved = bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "images.blend"))
    finally:
        node_tree_handlers.log.removeHandler(records)
    check(saved == {'FINISHED'}, "saving finished despite the broken image path")

    check(managed.packed_file is not None, "the managed image is packed")
    check(disk.packed_file is None and not disk.is_dirty, "the image on disk is saved, not packed")
    got = disk_pixel(paths["disk"])
    check(close(got, GREEN), f"the file on disk holds the painted pixels {fmt(got)}")
    check(packed.packed_file is not None, "a packed image with a file path stays packed")
    got = disk_pixel(paths["packed"])
    check(close(got, RED), f"the packed image's file is not written {fmt(got)}")
    check(broken.packed_file is not None and not broken.filepath,
          "an image whose directory cannot be created drops its path and is packed")
    check(any(names[broken] in message for message in records.messages),
          f"the fallback logs a warning {records.messages}")
    check(not os.path.exists(os.path.dirname(broken_path)), "no directory is left behind for the broken path")
    check(generated.packed_file is not None, "a generated cache image the addon did not create is packed")
    check(unrelated.packed_file is None and unrelated.is_dirty,
          "an image no Paint System node uses is left alone")
    check(clean.packed_file is None and os.path.getmtime(paths["clean"]) == OLD_MTIME,
          "an image without unsaved changes is not written")
    del tree, managed_layer, disk_layer, packed_layer, broken_layer, clean_layer, solid
    del managed, disk, packed, broken, clean, generated, unrelated, painted

    section("reopen")
    names = list(names.values())
    check(bpy.ops.wm.open_mainfile(filepath=os.path.join(folder, "images.blend")) == {'FINISHED'}, "reopened")
    images = {name: bpy.data.images.get(name) for name in names}
    check(all(image is not None for image in images.values()), f"every image is still in the file {names}")
    managed_name, disk_name, packed_name, broken_name, _clean_name, generated_name, _unrelated_name = names
    for name, color, where in ((managed_name, BLUE, "packed"),
                               (disk_name, GREEN, "on disk"),
                               (packed_name, BLUE, "packed"),
                               (broken_name, GREEN, "packed"),
                               (generated_name, BLUE, "packed")):
        image = images[name]
        got = first_pixel(image) if image is not None else None
        check(got is not None and close(got, color), f"{name} ({where}) reads back its painted pixels {got}")
    disk = images[disk_name]
    check(disk is not None and disk.packed_file is None
          and os.path.normpath(bpy.path.abspath(disk.filepath)) == os.path.normpath(paths["disk"]),
          "the image on disk still points at its file")


guarded(test_save_and_reopen)
finish("IMAGES TEST")
