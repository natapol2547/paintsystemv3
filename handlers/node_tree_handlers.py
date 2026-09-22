import logging

import bpy

from ..compiler.bake import PS_IMAGE_KEY
from ..compiler.core import (block_compile, cleanup_orphan_artifacts, mark_dirty, ps_trees,
                             unblock_compile)
from ..filters import derived, freshness, layer_job
from ..gpu_passes import surface, texel_map
from ..nodetree.tree import subscribe_name_changes
from ..selection import overlay as selection_overlay
from ..selection import raster as selection_raster
from ..selection import session as selection_session
from ..selection import stencil as selection_stencil
from ..undo import pixels

log = logging.getLogger(__name__)


def paint_system_images() -> set[bpy.types.Image]:
    """Images the addon created, plus every image a Paint System node points at.

    A filter result is included only while a layer still points at it.
    An orphaned filter result is a full-resolution image that nothing
    uses, and the next file read deletes it. Packing it here would carry
    it into the saved ``.blend`` instead.
    """
    pointed_at = set()
    for tree in ps_trees():
        for node in tree.nodes:
            for prop in node.bl_rna.properties:
                if prop.type == 'POINTER' and prop.fixed_type.identifier == 'Image':
                    image = getattr(node, prop.identifier)
                    if image is not None:
                        pointed_at.add(image)
    made = {image for image in bpy.data.images
            if image.get(PS_IMAGE_KEY) and image.get(derived.OWNER_KEY) is None}
    return made | pointed_at


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


@bpy.app.handlers.persistent
def on_depsgraph_update_post(scene, depsgraph=None):
    """Initialise new trees, and pass geometry, image and scene changes on.

    A tree created from the node editor header gets no init call, so it
    is initialised here.
    """
    for tree in ps_trees():
        if not tree.is_initialized:
            tree.initialize()
    if depsgraph is None:
        return
    # Texel maps and overlay batches are keyed by the content of the
    # evaluated surface. So a geometry update only marks the surface as
    # suspect, and the next `surface.resolve_key` compares the arrays.
    # Do not drop the caches here. On 5.3 a texture paint stroke reports a
    # geometry update without changing the surface, so every stroke would
    # rebuild them. Moving an object changes no surface key, because the
    # texel map cache has the world matrix in its own key.
    geometry_changed = False
    painted = []
    for update in depsgraph.updates:
        original = getattr(update.id, 'original', None)
        # Blender tags a painted image at the end of a stroke. That is
        # the only signal a filter layer that reads the image gets that
        # its own pixels are out of date. This check comes before the
        # geometry check, because an image update does not set
        # `is_updated_geometry`.
        if isinstance(original, bpy.types.Image):
            painted.append(original.session_uid)
            continue
        if not update.is_updated_geometry:
            continue
        if isinstance(original, bpy.types.Object):
            surface.mark_suspect(original.session_uid)
            geometry_changed = True
    if painted:
        freshness.note_image_changed(painted)
        # Notify even when nothing new was marked. Each call pushes the
        # refresh deadline back, so a refresh does not start partway
        # through a stroke. A pass with nothing to do costs one scan and
        # then unregisters itself.
        layer_job.notify()
    # A filter layer refused for want of a mesh or a UV map waits for the
    # scene to change, and such a change compiles no tree.
    layer_job.scene_changed()
    # The live selection looks up a layer's UV map by name, and renaming
    # or removing a UV map shows up only as a geometry update. While the
    # selection cannot be used for one of the `GEOMETRY_REASONS`, any
    # update may be the fix. Scaling its object back from zero, or linking
    # it back into the scene, reports no geometry update.
    if geometry_changed or (depsgraph.updates
                            and selection_session.current().reason in selection_raster.GEOMETRY_REASONS):
        selection_session.notify()


@bpy.app.handlers.persistent
def on_restore_pre(*args):
    # Blender calls NodeTree.update on half-restored data while it reads a
    # file or an undo step. Block compiles until the restore is done, and
    # compile once after it.
    block_compile()
    # A running refresh holds the tree and the node it started with, and
    # neither survives a restore. It has written nothing yet, so there is
    # no half-built image to clean up.
    layer_job.cancel_all()


@bpy.app.handlers.persistent
def on_load_post(*args):
    unblock_compile()
    subscribe_name_changes()
    cleanup_orphan_artifacts()
    # Orphaned filter results are deleted here and nowhere else. A file
    # read is the one moment with no undo stack that a removal could
    # break.
    derived.cleanup_orphan_derived()
    # Reading a file frees the undo stack and everything the addon pushed
    # onto it.
    pixels.forget_undo_state()
    derived.forget_packed()
    # Cached maps and surface keys belong to the objects of the previous
    # file.
    texel_map.invalidate()
    surface.forget()
    # Masks are keyed by content and would still be valid, but the new
    # file is unlikely to need them. Free the memory.
    selection_raster.invalidate()
    # A file saved without `on_save_pre`, such as an autosave, can still
    # point the stencil at the previous session's mask file.
    selection_stencil.on_file_loaded()
    # The overlay's batches and buffers belong to the old file's objects
    # and regions.
    selection_overlay.invalidate_all()
    # The file brings its own selection and active layer. Sync everything
    # derived from them, and retry masks that failed before.
    selection_session.forget_failures()
    selection_session.notify(force=True)
    # Runs before Blender records the file's initial undo step, so the
    # compile result is part of it.
    mark_dirty()


@bpy.app.handlers.persistent
def on_load_post_fail(*args):
    unblock_compile()
    pixels.forget_undo_state()
    texel_map.invalidate()
    surface.forget()
    selection_raster.invalidate()
    selection_overlay.invalidate_all()
    selection_session.forget_failures()
    selection_session.notify(force=True)


@bpy.app.handlers.persistent
def on_undo_post(*args):
    # Every undo step should already hold a matching compiled artifact, so
    # this is usually one fingerprint check per tree. It repairs steps
    # from older files where that is not the case.
    unblock_compile()
    mark_dirty()
    # The steps the addon pushed may no longer be on the stack.
    pixels.forget_baselines()
    # Undo brings back a filter result's packed file and stamps, but not
    # its decoded pixels.
    derived.free_stale_buffers()
    # Undo can restore different geometry under the same world matrix, so
    # every surface key is checked again on its next resolve. Cached maps
    # are kept. Undoing a stroke restores the same surface, and undoing a
    # vertex move finds the map that was built before the move.
    surface.mark_suspect()
    # Selection masks are kept. They are keyed by a hash of the ops and
    # the size, so the restored ops find their cached mask without a
    # rebuild. Undo restores the ops and Blender's own settings
    # separately, so the session syncs everything derived from the
    # selection even when its state has not changed.
    selection_session.notify(force=True)


@bpy.app.handlers.persistent
def on_frame_change_post(scene, depsgraph=None):
    """Mark every surface as suspect when the frame changes.

    Animated deformation changes surfaces without a depsgraph update that
    reports it.
    """
    # A render job calls this from its own thread for each frame it
    # renders. The surface entries and timers are not thread safe, and a
    # render depsgraph changes no surface drawn in the viewport.
    if depsgraph is not None and depsgraph.mode == 'RENDER':
        return
    surface.mark_suspect()
    selection_session.notify()


@bpy.app.handlers.persistent
def on_save_pre(*args):
    """Save or pack painted layer images so their pixels survive a reload."""
    # Put the user's stencil settings back before saving. The file then
    # has no reference to the selection's stencil image, so that image
    # has no users and is not written. `on_save_post` applies the
    # selection again.
    selection_stencil.restore_all()
    for image in paint_system_images():
        save_image(image)


@bpy.app.handlers.persistent
def on_save_post(*args):
    # Sync now rather than on the next tick, so that no stroke can paint
    # unclipped in between.
    selection_session.sync(force=True)


_handlers = [
    (bpy.app.handlers.depsgraph_update_post, on_depsgraph_update_post),
    (bpy.app.handlers.load_pre, on_restore_pre),
    (bpy.app.handlers.load_post, on_load_post),
    (bpy.app.handlers.load_post_fail, on_load_post_fail),
    (bpy.app.handlers.undo_pre, on_restore_pre),
    (bpy.app.handlers.undo_post, on_undo_post),
    (bpy.app.handlers.redo_pre, on_restore_pre),
    (bpy.app.handlers.redo_post, on_undo_post),
    (bpy.app.handlers.frame_change_post, on_frame_change_post),
    (bpy.app.handlers.save_pre, on_save_pre),
    (bpy.app.handlers.save_post, on_save_post),
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
