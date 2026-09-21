"""Keep texture painting on the active layer when the selection changes.

Each kind of change reaches the addon in a different way:

- Selecting another object causes a depsgraph update.
- Selecting another material slot causes no depsgraph update, but it
  notifies the message bus.
- Clicking a node in the node editor makes it active with no update,
  message bus notification or handler call. The editor does redraw, so a
  draw callback notices the change. A timer then does the sync, because
  a draw callback cannot write data.
- Mode and scene switches notify the message bus. This module passes
  them on to the selection session.
"""
import bpy

from ..context import get_ps_object, node_editor_tree, update_active_image
from ..selection import session as selection_session


# Object and material pointers the canvas was last synced for.
_last_selection: tuple[int, int] | None = None
# Active node pointer of each Paint System tree at its last draw, keyed by
# tree pointer.
_seen_active_nodes: dict[int, int] = {}
_draw_handle = None
_msgbus_owner = object()


def _pointer(id_data) -> int:
    return id_data.as_pointer() if id_data is not None else 0


def reset() -> None:
    """Forget what was synced, so the next update syncs again."""
    global _last_selection
    _last_selection = None
    _seen_active_nodes.clear()


def sync_canvas():
    """Timer callback: sync from the current context."""
    update_active_image(bpy.context)
    return None


def sync_selection(force: bool = False) -> None:
    """Sync when the active object or its active material changed since the last sync."""
    global _last_selection
    obj = get_ps_object(bpy.context.view_layer.objects.active)
    material = obj.active_material if obj is not None else None
    selection = (_pointer(obj), _pointer(material))
    if selection == _last_selection and not force:
        return
    _last_selection = selection
    update_active_image(bpy.context)


@bpy.app.handlers.persistent
def on_depsgraph_update_post(scene, depsgraph=None):
    sync_selection()


def on_active_material_index(*args):
    sync_selection()


def on_object_mode(*args):
    # The selection session's state records whether texture paint mode is on.
    selection_session.notify()


def on_scene_change(*args):
    # The selection session's state belongs to one scene.
    selection_session.notify()


def subscribe() -> None:
    """Subscribe to the message bus again.

    The message bus forgets all subscribers when a file loads.
    """
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.Object, "active_material_index"),
                             owner=_msgbus_owner, args=(), notify=on_active_material_index)
    bpy.msgbus.subscribe_rna(key=(bpy.types.Object, "mode"),
                             owner=_msgbus_owner, args=(), notify=on_object_mode)
    bpy.msgbus.subscribe_rna(key=(bpy.types.Window, "scene"),
                             owner=_msgbus_owner, args=(), notify=on_scene_change)


@bpy.app.handlers.persistent
def on_load_post(*args):
    reset()
    subscribe()
    sync_selection(force=True)


@bpy.app.handlers.persistent
def on_undo_post(*args):
    # The restored step can hold the new selection with the old canvas,
    # because the sync runs after the undo step for the selection change
    # was pushed. Forget the last sync so the next update syncs again.
    reset()


def on_node_editor_draw():
    tree = node_editor_tree(bpy.context)
    if tree is None:
        return
    active = _pointer(tree.nodes.active)
    key = tree.as_pointer()
    if _seen_active_nodes.get(key) == active:
        return
    _seen_active_nodes[key] = active
    if not bpy.app.timers.is_registered(sync_canvas):
        bpy.app.timers.register(sync_canvas, first_interval=0.0)


_handlers = [
    (bpy.app.handlers.depsgraph_update_post, on_depsgraph_update_post),
    (bpy.app.handlers.load_post, on_load_post),
    (bpy.app.handlers.undo_post, on_undo_post),
    (bpy.app.handlers.redo_post, on_undo_post),
]


def register():
    global _draw_handle
    for handler_list, fn in _handlers:
        if fn not in handler_list:
            handler_list.append(fn)
    subscribe()
    _draw_handle = bpy.types.SpaceNodeEditor.draw_handler_add(on_node_editor_draw, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _draw_handle
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    if _draw_handle is not None:
        bpy.types.SpaceNodeEditor.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if bpy.app.timers.is_registered(sync_canvas):
        bpy.app.timers.unregister(sync_canvas)
    for handler_list, fn in _handlers:
        if fn in handler_list:
            handler_list.remove(fn)
    reset()
