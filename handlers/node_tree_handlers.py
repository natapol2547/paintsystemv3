import bpy

from ..compiler.core import mark_dirty, ps_trees, cleanup_orphan_artifacts


PS_IMAGE_KEY = "ps_managed"


@bpy.app.handlers.persistent
def on_depsgraph_update_post(scene, depsgraph=None):
    """Initialise trees created from the node editor header (no init hook exists)."""
    for tree in ps_trees():
        if not tree.is_initialized:
            tree.initialize()


@bpy.app.handlers.persistent
def on_load_post(*args):
    cleanup_orphan_artifacts()
    mark_dirty()


@bpy.app.handlers.persistent
def on_undo_post(*args):
    # The compiled artifact is derived data; after undo/redo just rebuild what changed.
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
    (bpy.app.handlers.load_post, on_load_post),
    (bpy.app.handlers.undo_post, on_undo_post),
    (bpy.app.handlers.save_pre, on_save_pre),
]
if hasattr(bpy.app.handlers, 'redo_post'):
    _handlers.append((bpy.app.handlers.redo_post, on_undo_post))


def register():
    for handler_list, fn in _handlers:
        if fn not in handler_list:
            handler_list.append(fn)
    mark_dirty()


def unregister():
    for handler_list, fn in _handlers:
        if fn in handler_list:
            handler_list.remove(fn)
