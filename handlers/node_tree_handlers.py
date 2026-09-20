import bpy

from ..common import save_image
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


def paint_system_images() -> set[bpy.types.Image]:
    """Images the addon created and every image a Paint System node points at.

    A filter result is in only while a layer still points at it. An orphan
    is a full-resolution image nothing can reach, waiting for the next
    file read to sweep it; packing it would carry it into the next
    ``.blend`` instead (PS-057).
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


@bpy.app.handlers.persistent
def on_depsgraph_update_post(scene, depsgraph=None):
    """Initialise trees created from the node editor header (no init hook exists)."""
    for tree in ps_trees():
        if not tree.is_initialized:
            tree.initialize()
    if depsgraph is None:
        return
    # Texel maps and overlay batches are keyed by the content of the
    # evaluated surface, so a geometry update only marks it suspect, and
    # the next resolve compares arrays. A texture paint stroke reports a
    # geometry update on 5.3 without changing the surface, and dropping the
    # caches here rebuilt them after every stroke (PS-092, PS-093). A move
    # changes no surface key; the texel map cache keys the world matrix.
    geometry_changed = False
    painted = []
    for update in depsgraph.updates:
        original = getattr(update.id, 'original', None)
        # Blender tags a painted image at the end of a stroke, which is
        # the only notice a filter layer below the stroke gets that its
        # pixels have stopped describing the stack (PS-057). Checked
        # before the geometry guard: an image update sets no such flag.
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
        # Unconditionally, even when nothing was newly marked: this is
        # what makes the refresh debounce hold for the length of a stroke
        # rather than starting one partway through it. A pass with
        # nothing to do costs a scan and unregisters itself.
        layer_job.notify()
    # Renaming or removing a UV map shows up only as a geometry update, and
    # the live selection samples a layer's UV map by name (PS-091). While
    # a view selection cannot be used, any update may be the fix: scaling
    # its object back from zero or linking it back into the scene reports
    # no geometry update (PS-093).
    if geometry_changed or (depsgraph.updates
                            and selection_session.current().reason in selection_raster.GEOMETRY_REASONS):
        selection_session.notify()


@bpy.app.handlers.persistent
def on_restore_pre(*args):
    # Blender calls NodeTree.update on half-restored data while it reads a
    # file or an undo step; compile once it is done instead.
    block_compile()
    # A refresh in flight holds the tree and the node it was started with,
    # and neither survives a restore (PS-090). It has written nothing, so
    # there is no half-built image to clean up (PS-057).
    layer_job.cancel_all()


@bpy.app.handlers.persistent
def on_load_post(*args):
    unblock_compile()
    subscribe_name_changes()
    cleanup_orphan_artifacts()
    # A file read is the one moment with no undo stack for a removal to
    # break, which is why the filter results are swept here and nowhere
    # else (PS-057).
    derived.cleanup_orphan_derived()
    # Reading a file frees the undo stack and everything the addon pushed
    # onto it (PS-090).
    pixels.forget_undo_state()
    # Cached maps and surface keys belong to objects of the file that was
    # open (PS-092).
    texel_map.invalidate()
    surface.forget()
    # Masks are keyed by content and would still be right, but nothing in
    # the new file is likely to ask for them; give the memory back.
    selection_raster.invalidate()
    # A file saved without `on_save_pre`, as an autosave, can still point
    # the stencil at the previous session's mask file.
    selection_stencil.on_file_loaded()
    # The overlay's batches and buffers belong to the old file's objects
    # and regions.
    selection_overlay.invalidate_all()
    # The file's selection and active layer arrive together; reconcile
    # everything derived from them, and try masks that failed before
    # again (PS-091).
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
    # Every undo step already holds a matching artifact, so this is normally
    # a fingerprint check per tree. It repairs files from before that held.
    unblock_compile()
    mark_dirty()
    # The steps the addon pushed may no longer be on the stack (PS-090).
    pixels.forget_baselines()
    # An undo can restore different geometry under the same world matrix,
    # so every surface key is checked again on its next resolve. Cached
    # maps stay: the undo of a stroke restores the same surface, and the
    # undo of a vertex move finds the map built before it (PS-092).
    surface.mark_suspect()
    # Selection masks stay: they are keyed by a digest of the ops and the
    # size, so the restored ops find their mask, if it is cached, without
    # a rebuild. Undo restores the ops and Blender's own settings
    # independently, so the session reconciles everything derived from the
    # selection even when its state matches (PS-091).
    selection_session.notify(force=True)


@bpy.app.handlers.persistent
def on_frame_change_post(scene, depsgraph=None):
    """An animated deformation changes surfaces with no depsgraph update to report it (PS-093)."""
    # A render job calls this from its own thread for each frame it
    # renders. The entries and timers are not thread safe, and a render
    # depsgraph changes no surface drawn in the viewport.
    if depsgraph is not None and depsgraph.mode == 'RENDER':
        return
    surface.mark_suspect()
    selection_session.notify()


@bpy.app.handlers.persistent
def on_save_pre(*args):
    """Save or pack painted layer images so their pixels survive reload (PS-056)."""
    # The file keeps the user's stencil settings and no reference to the
    # selection's stencil image, which then has no users and is not
    # written (PS-091). `on_save_post` applies the selection again.
    selection_stencil.restore_all()
    for image in paint_system_images():
        save_image(image)


@bpy.app.handlers.persistent
def on_save_post(*args):
    # Synchronously rather than on the next tick, so no stroke paints
    # unclipped in between (PS-091).
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
