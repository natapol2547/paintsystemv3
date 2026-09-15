import bpy

from ..compiler.core import (block_compile, cleanup_orphan_artifacts, mark_dirty, ps_trees,
                             unblock_compile)
from ..nodetree.tree import subscribe_name_changes


PS_IMAGE_KEY = "ps_managed"


@bpy.app.handlers.persistent
def on_depsgraph_update_post(scene, depsgraph=None):
    """Initialise trees created from the node editor header (no init hook exists)."""
    for tree in ps_trees():
        if not tree.is_initialized:
            tree.initialize()


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
    # Runs before Blender records the file's initial undo step, so the
    # compile result is part of it.
    mark_dirty()


@bpy.app.handlers.persistent
def on_load_post_fail(*args):
    unblock_compile()


@bpy.app.handlers.persistent
def on_undo_post(*args):
    # Every undo step already holds a matching artifact, so this is normally
    # a fingerprint check per tree. It repairs files from before that held.
    unblock_compile()
    mark_dirty()


@bpy.app.handlers.persistent
def on_save_pre(*args):
    """Pack generated images the addon created so painted pixels survive reload."""
    for image in bpy.data.images:
        if image.get(PS_IMAGE_KEY) and image.is_dirty and not image.filepath:
            try:
                image.pack()
            except RuntimeError:
                pass


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
