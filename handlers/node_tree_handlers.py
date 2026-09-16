import bpy

from ..common import save_image
from ..compiler.bake import PS_IMAGE_KEY
from ..compiler.core import (block_compile, cleanup_orphan_artifacts, mark_dirty, ps_trees,
                             unblock_compile)
from ..gpu_passes import texel_map
from ..nodetree.tree import subscribe_name_changes
from ..undo import pixels


def paint_system_images() -> set[bpy.types.Image]:
    """Images the addon created and every image a Paint System node points at."""
    images = {image for image in bpy.data.images if image.get(PS_IMAGE_KEY)}
    for tree in ps_trees():
        for node in tree.nodes:
            for prop in node.bl_rna.properties:
                if prop.type == 'POINTER' and prop.fixed_type.identifier == 'Image':
                    image = getattr(node, prop.identifier)
                    if image is not None:
                        images.add(image)
    return images


@bpy.app.handlers.persistent
def on_depsgraph_update_post(scene, depsgraph=None):
    """Initialise trees created from the node editor header (no init hook exists)."""
    for tree in ps_trees():
        if not tree.is_initialized:
            tree.initialize()
    if depsgraph is None:
        return
    # A cached texel map describes the geometry as it was when it was
    # drawn, and stores world positions, so a moved or edited object needs
    # a new one (PS-092).
    for update in depsgraph.updates:
        if not (update.is_updated_geometry or update.is_updated_transform):
            continue
        original = getattr(update.id, 'original', None)
        if isinstance(original, bpy.types.Object):
            texel_map.invalidate(original.session_uid)


@bpy.app.handlers.persistent
def on_restore_pre(*args):
    # Blender calls NodeTree.update on half-restored data while it reads a
    # file or an undo step; compile once it is done instead.
    block_compile()


@bpy.app.handlers.persistent
def on_load_post(*args):
    unblock_compile()
    subscribe_name_changes()
    cleanup_orphan_artifacts()
    # Reading a file frees the undo stack and everything the addon pushed
    # onto it (PS-090).
    pixels.forget_undo_state()
    # Cached maps belong to objects of the file that was open (PS-092).
    texel_map.invalidate()
    # Runs before Blender records the file's initial undo step, so the
    # compile result is part of it.
    mark_dirty()


@bpy.app.handlers.persistent
def on_load_post_fail(*args):
    unblock_compile()
    pixels.forget_undo_state()
    texel_map.invalidate()


@bpy.app.handlers.persistent
def on_undo_post(*args):
    # Every undo step already holds a matching artifact, so this is normally
    # a fingerprint check per tree. It repairs files from before that held.
    unblock_compile()
    mark_dirty()
    # The steps the addon pushed may no longer be on the stack (PS-090).
    pixels.forget_baselines()
    # An undo can restore different geometry under the same world matrix,
    # which the cache key alone would not notice (PS-092).
    texel_map.invalidate()


@bpy.app.handlers.persistent
def on_save_pre(*args):
    """Save or pack painted layer images so their pixels survive reload (PS-056)."""
    for image in paint_system_images():
        save_image(image)


_handlers = [
    (bpy.app.handlers.depsgraph_update_post, on_depsgraph_update_post),
    (bpy.app.handlers.load_pre, on_restore_pre),
    (bpy.app.handlers.load_post, on_load_post),
    (bpy.app.handlers.load_post_fail, on_load_post_fail),
    (bpy.app.handlers.undo_pre, on_restore_pre),
    (bpy.app.handlers.undo_post, on_undo_post),
    (bpy.app.handlers.redo_pre, on_restore_pre),
    (bpy.app.handlers.redo_post, on_undo_post),
    (bpy.app.handlers.save_pre, on_save_pre),
]


def register():
    for handler_list, fn in _handlers:
        if fn not in handler_list:
            handler_list.append(fn)
    mark_dirty()


def unregister():
    for handler_list, fn in _handlers:
        if fn in handler_list:
            handler_list.remove(fn)
