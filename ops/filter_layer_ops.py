# SPDX-License-Identifier: GPL-3.0-or-later
"""Update, Cancel Refresh and Clear Result for a filter layer.

Design: docs/tickets/PS-057-filter-layer.md.

- The build itself is the generator `filters.layer_build.steps`. Update
  runs it in a modal operator that does a slice of the work on each
  timer event. So a 4K build shows progress and can be cancelled with
  Escape, instead of freezing the window. The generator writes nothing
  but the layer's Object before its last step, so cancelling leaves the
  previous result exactly as it was, and frees the GPU textures on the
  way out.
- Update and Clear Result have the `UNDO` option. The no-`UNDO` rule in
  `undo.pixels` does not apply here. That rule exists because a memfile
  step on top of an image undo step costs two Ctrl+Z. A filter build
  pushes no image undo step, because it packs the pixels instead. With
  `UNDO` and a packed image, one Ctrl+Z undoes the whole rebuild, pixels
  and stamps together.
- Update opens no dialog. A filter layer has its own `resolution` and
  `uv_map`, shown in the panel just above the button, so asking again
  would only add a second place to get them wrong. Bake Cache
  (`PAINTSYSTEM_OT_bake_cache`) does ask for a resolution, because a
  cached layer has nowhere else to store one.
"""
import logging
import time

import bpy
from bpy.types import Operator
from bpy.utils import register_classes_factory

from ..context import button_layer, get_active_tree
from ..filters import layer_build, layer_job
from ..filters.core import Refused
from ..gpu_passes.core import gpu_known

log = logging.getLogger(__name__)

# Seconds of work per timer event. Long enough that the per-event
# overhead does not dominate, short enough to stay under a frame.
BUDGET = 0.05


def filter_layer(context, tree):
    """The filter layer the buttons act on, or None.

    That is the node that drew the button in the node editor, else the
    tree's active node.
    """
    node = button_layer(context, tree)
    return node if node is not None and node.ps_type == 'FILTER' else None


def _resume_auto_refresh(node):
    """Clear the automatic refresh's message, and turn Auto Refresh back on if the job turned it off.

    Called after a successful Update. Only the job sets `derived_error`.
    With Auto Refresh still on, the message says why the job could not
    build the layer. With it off, the job gave up and turned it off:
    switching Auto Refresh off by hand clears the message, so a message
    never sits next to a choice the user made. Either way, a build that
    just worked means the message is out of date and Auto Refresh
    belongs on.
    """
    if node.derived_error:
        node.derived_error = ""
        node.auto_refresh = True


class FilterLayerAction:
    """Shared poll of Update and Clear Result. It only checks cheap flags."""

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
        layer_job.cancel_all()
        try:
            image = layer_build.build_layer(context, tree, node)
        except Refused as refusal:
            self.report({'WARNING'}, str(refusal))
            return {'CANCELLED'}
        _resume_auto_refresh(node)
        self.report({'INFO'}, f"Built {image.name}")
        return {'FINISHED'}

    def invoke(self, context, event):
        tree = get_active_tree(context)
        node = filter_layer(context, tree)
        # Two builds of one layer would race for the same image. The
        # button wins, because it is the build the user is watching.
        layer_job.cancel_all()
        self._name = node.name
        self._node = node
        self._steps = layer_build.steps(context, tree, node)
        try:
            # The first step only resolves the inputs. So every refusal
            # that can come before any GPU memory is used is raised here,
            # and is reported without starting the modal.
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
                _resume_auto_refresh(self._node)
                self.report({'INFO'}, f"Built {done.value.name}")
                return {'FINISHED'}
            except Refused as refusal:
                self._stop(context)
                self.report({'WARNING'}, str(refusal))
                return {'CANCELLED'}
            except Exception:
                # Without this, an unexpected error mid-build, such as a
                # GPU driver failure, would leave the timer and the status
                # text behind for good.
                self._stop(context)
                log.exception("filter layer '%s' failed to build", self._name)
                self.report({'ERROR'}, f"Building '{self._name}' failed; see the system console")
                return {'CANCELLED'}
            context.window_manager.progress_update(fraction)
            context.workspace.status_text_set(label)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        """Clean up when Blender ends the modal from outside.

        Closing a window or loading a file ends a modal without going
        through `modal`. Without this hook, the timer and the status text
        would stay for the rest of the session.
        """
        self._stop(context)

    def _stop(self, context):
        """Release everything `invoke` set up, however the build ends."""
        if self._steps is None:
            return
        window_manager = context.window_manager
        # Closing the generator raises GeneratorExit at the yield it has
        # reached. That is what frees the GPU textures it holds.
        self._steps.close()
        self._steps = None
        window_manager.event_timer_remove(self._timer)
        window_manager.progress_end()
        context.workspace.status_text_set(None)


class PAINTSYSTEM_OT_cancel_filter_refresh(Operator):
    bl_idname = "paint_system.cancel_filter_refresh"
    bl_label = "Cancel Refresh"
    bl_description = ("Stop the automatic refresh running now. The layer keeps the pixels it "
                      "already has")
    # No UNDO: the job has written nothing to take back.
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return layer_job.running()

    def execute(self, context):
        layer_job.cancel_all()
        return {'FINISHED'}


class PAINTSYSTEM_OT_clear_filter_result(FilterLayerAction, Operator):
    bl_idname = "paint_system.clear_filter_result"
    bl_label = "Clear Result"
    bl_description = ("Drop this layer's filtered image and turn Auto Refresh off. The layer "
                      "stays where it is and passes the layers below through until it is "
                      "built again")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        tree = get_active_tree(context)
        node = filter_layer(context, tree)
        image = node.derived_image
        if image is None:
            return {'CANCELLED'}
        if layer_job.running_on(node):
            layer_job.cancel_all()
        # The job builds any layer with no pixels, so with Auto Refresh
        # left on the result would come straight back.
        node.auto_refresh = False
        node.derived_image = None
        if image.users == 0:
            bpy.data.images.remove(image)
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_rebuild_filter_layer,
    PAINTSYSTEM_OT_cancel_filter_refresh,
    PAINTSYSTEM_OT_clear_filter_result,
)


register, unregister = register_classes_factory(classes)
