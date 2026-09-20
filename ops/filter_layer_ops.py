# SPDX-License-Identifier: GPL-3.0-or-later
"""Update and Clear Result on a filter layer (PS-057).

The work is in `filters.layer_build.steps`; Update wraps it in a modal
that spends a slice of each timer event on it, so a 4K build shows
progress and answers Escape instead of freezing the window. The generator
is what makes cancelling safe: it writes nothing before its last unit, so
stopping leaves the previous result bit-identical and gives the textures
back on the way out.

Both take `UNDO`. That is not the case PS-090's no-`UNDO` rule covers: the
rule exists because a memfile step stacked on an *image* undo step costs
two Ctrl+Z, and a derived image pushes no image step at all -- which is
what `filters.core.ResultImage` is for. With `UNDO` and a packed image,
one Ctrl+Z takes the whole rebuild back, pixels and stamps together.

No dialog. `PAINTSYSTEM_OT_bake_cache` asks for a resolution because a
cached layer has nowhere else to keep one; a filter layer has
`resolution` and `uv_map` of its own, sitting in the panel directly above
the button, so asking again would only be a second place to get them
wrong.
"""
import logging
import time

import bpy
from bpy.types import Operator
from bpy.utils import register_classes_factory

from ..context import get_active_tree
from ..filters import layer_build
from ..filters.core import Refused
from ..gpu_passes.core import gpu_known

log = logging.getLogger(__name__)

# Seconds of work per timer event. Long enough that the per-event
# overhead does not dominate, short enough to stay under a frame.
BUDGET = 0.05


def filter_layer(context, tree):
    """The filter layer the buttons act on: the node editor's, or the active one."""
    node = getattr(context, 'node', None)
    if node is None and tree is not None:
        node = tree.nodes.active
    if node is None or getattr(node, 'ps_type', None) != 'FILTER':
        return None
    return node


class FilterLayerAction:
    """Shared poll of the two buttons, answering from flags only."""

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        if tree is None:
            cls.poll_message_set("No Paint System tree is active")
            return False
        node = filter_layer(context, tree)
        if node is None:
            cls.poll_message_set("No active filter layer")
            return False
        if node.lock_layer:
            cls.poll_message_set(f"Layer '{node.name}' is locked")
            return False
        return True


class PAINTSYSTEM_OT_rebuild_filter_layer(FilterLayerAction, Operator):
    bl_idname = "paint_system.rebuild_filter_layer"
    bl_label = "Update Filter"
    bl_description = "Rebuild this layer's filtered image from the layers below it"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if gpu_known() is False:
            cls.poll_message_set("This Blender has no GPU context to run a filter on")
            return False
        return super().poll(context)

    def execute(self, context):
        """Build in one go, for a script or a keystroke with no window."""
        tree = get_active_tree(context)
        node = filter_layer(context, tree)
        try:
            image = layer_build.build_layer(context, tree, node)
        except Refused as refusal:
            self.report({'WARNING'}, str(refusal))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Built {image.name}")
        return {'FINISHED'}

    def invoke(self, context, event):
        tree = get_active_tree(context)
        node = filter_layer(context, tree)
        self._name = node.name
        self._steps = layer_build.steps(context, tree, node)
        try:
            # The first unit only resolves, so every refusal this build
            # can raise before a byte of video memory is spent comes out
            # here, where it is reported without going modal at all.
            next(self._steps)
        except Refused as refusal:
            self.report({'WARNING'}, str(refusal))
            return {'CANCELLED'}
        window_manager = context.window_manager
        window_manager.progress_begin(0.0, 1.0)
        self._timer = window_manager.event_timer_add(0.01, window=context.window)
        window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self._stop(context)
            self.report({'INFO'}, f"Update of '{self._name}' cancelled")
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'RUNNING_MODAL'}

        deadline = time.perf_counter() + BUDGET
        while time.perf_counter() < deadline:
            try:
                label, fraction = next(self._steps)
            except StopIteration as done:
                self._stop(context)
                self.report({'INFO'}, f"Built {done.value.name}")
                return {'FINISHED'}
            except Refused as refusal:
                self._stop(context)
                self.report({'WARNING'}, str(refusal))
                return {'CANCELLED'}
            except Exception:
                # A driver that gives up mid-build would otherwise leave
                # the timer and the status text behind for good.
                self._stop(context)
                log.exception("filter layer '%s' failed to build", self._name)
                self.report({'ERROR'}, f"Building '{self._name}' failed; see the system console")
                return {'CANCELLED'}
            context.window_manager.progress_update(fraction)
            context.workspace.status_text_set(label)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        """Blender's own hook, for a modal ended from outside.

        Closing a window or loading a file ends a modal without going
        through `modal`, which would otherwise leave the timer and the
        status text behind for the rest of the session.
        """
        self._stop(context)

    def _stop(self, context):
        """Give back everything `invoke` took, whichever way the build ends."""
        if self._steps is None:
            return
        window_manager = context.window_manager
        # Closing the generator raises GeneratorExit at whichever yield it
        # reached, which is what releases the textures it was holding.
        self._steps.close()
        self._steps = None
        window_manager.event_timer_remove(self._timer)
        window_manager.progress_end()
        context.workspace.status_text_set(None)


class PAINTSYSTEM_OT_clear_filter_result(FilterLayerAction, Operator):
    bl_idname = "paint_system.clear_filter_result"
    bl_label = "Clear Result"
    bl_description = ("Drop this layer's filtered image. The layer stays where it is and "
                      "passes the layers below through until it is built again")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        tree = get_active_tree(context)
        node = filter_layer(context, tree)
        image = node.derived_image
        if image is None:
            return {'CANCELLED'}
        node.derived_image = None
        if image.users == 0:
            bpy.data.images.remove(image)
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_rebuild_filter_layer,
    PAINTSYSTEM_OT_clear_filter_result,
)


register, unregister = register_classes_factory(classes)
