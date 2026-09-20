# SPDX-License-Identifier: GPL-3.0-or-later
"""Clear, Fill, Invert, Blur and Sharpen as operators (PS-052, PS-051).

The work is in `filters.actions`; these wrap it in Blender's report and
progress. None of them takes the `UNDO` option: the pixel write pushes an
image undo step of its own, and a memfile step on top of it would cost a
second Ctrl+Z (`undo.pixels`).

Blur and Sharpen open their dialog first, because a radius is not
something to guess at and running them again is a second full pass over
the layer rather than a redo of a cheap one.
"""
from bpy.props import BoolProperty, FloatProperty
from bpy.types import Operator
from bpy.utils import register_classes_factory

from ..context import get_active_tree
from ..filters import actions
from ..filters.core import Refused
from ..filters.registry import BLUR_MAX_EFFECTIVE_SIGMA
from ..gpu_passes.core import gpu_known


class PixelAction:
    """Shared poll, report and progress of the pixel actions.

    `poll` answers from flags only, and reads the active node itself
    rather than through `parse_context`, which walks the stack. Whether
    the image has pixels, what the selection covers and what the layer is
    baked into cost a read or a GPU pass, so they are `execute`'s
    refusals rather than a greyed out button with no explanation.
    """

    bl_options = {'REGISTER'}
    action = ''

    @classmethod
    def poll(cls, context):
        if gpu_known() is False:
            cls.poll_message_set("This Blender has no GPU context to run a filter on")
            return False
        tree = get_active_tree(context)
        if tree is None:
            cls.poll_message_set("No Paint System tree is active")
            return False
        layer = tree.nodes.active
        if layer is None or not getattr(layer, 'is_layer_node', False):
            cls.poll_message_set("No active layer")
            return False
        if layer.lock_layer:
            cls.poll_message_set(f"Layer '{layer.name}' is locked")
            return False
        if getattr(layer, 'paint_image', None) is None:
            cls.poll_message_set(f"Layer '{layer.name}' has no image to edit")
            return False
        if cls.action == actions.CLEAR and layer.lock_alpha:
            cls.poll_message_set("Clear changes transparency, and this layer has Lock Alpha on")
            return False
        return True

    def action_params(self, context) -> dict:
        """Keyword arguments for `run_action`, from this operator's properties.

        Not called `options`: `Operator.options` is Blender's own.
        """
        return {}

    def execute(self, context):
        window_manager = context.window_manager
        # A 4K layer takes about a second on 5.2 and up to five on 4.2,
        # with no redraw in between, so the cursor carries the progress.
        window_manager.progress_begin(0.0, 1.0)
        try:
            registered = actions.run_action(context, self.action, **self.action_params(context))
        except Refused as refusal:
            self.report({'WARNING'}, str(refusal))
            return {'CANCELLED'}
        finally:
            window_manager.progress_end()
        if not registered:
            self.report({'WARNING'},
                        "The edit was made but could not be added to the undo history")
        for area in context.screen.areas if context.screen else ():
            if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                area.tag_redraw()
        return {'FINISHED'}


class PAINTSYSTEM_OT_clear_pixels(PixelAction, Operator):
    bl_idname = "paint_system.clear_pixels"
    bl_label = "Clear"
    bl_description = "Erase the selected part of the active layer, or all of it when nothing is selected"
    action = actions.CLEAR


class PAINTSYSTEM_OT_fill_pixels(PixelAction, Operator):
    bl_idname = "paint_system.fill_pixels"
    bl_label = "Fill"
    bl_description = ("Fill the selected part of the active layer with the brush colour, "
                      "or all of it when nothing is selected")
    action = actions.FILL


class PAINTSYSTEM_OT_invert_pixels(PixelAction, Operator):
    bl_idname = "paint_system.invert_pixels"
    bl_label = "Invert Colors"
    bl_description = ("Invert the colours of the selected part of the active layer, "
                      "or all of it when nothing is selected")
    action = actions.INVERT

    invert_r: BoolProperty(name="Red", default=True)
    invert_g: BoolProperty(name="Green", default=True)
    invert_b: BoolProperty(name="Blue", default=True)
    invert_a: BoolProperty(name="Alpha", default=False,
                           description="Invert transparency as well as colour")

    def action_params(self, context):
        return {"channels": (self.invert_r, self.invert_g, self.invert_b, self.invert_a)}


class PAINTSYSTEM_OT_blur_pixels(PixelAction, Operator):
    bl_idname = "paint_system.blur_pixels"
    bl_label = "Blur"
    bl_description = ("Blur the selected part of the active layer, "
                      "or all of it when nothing is selected")
    action = actions.BLUR

    radius: FloatProperty(
        name="Radius", default=4.0, min=0.0, max=BLUR_MAX_EFFECTIVE_SIGMA,
        subtype='PIXEL', description="Width of the blur, in pixels of this layer's image")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def action_params(self, context):
        return {"sigma": self.radius}


class PAINTSYSTEM_OT_sharpen_pixels(PixelAction, Operator):
    bl_idname = "paint_system.sharpen_pixels"
    bl_label = "Sharpen"
    bl_description = ("Bring out the detail of the selected part of the active layer, "
                      "or all of it when nothing is selected")
    action = actions.SHARPEN

    radius: FloatProperty(
        name="Radius", default=1.0, min=0.0, max=16.0, subtype='PIXEL',
        description="How far from an edge the detail to bring out is, "
                    "in pixels of this layer's image")
    strength: FloatProperty(
        name="Strength", default=1.0, min=0.0, soft_max=3.0, max=10.0,
        description="How much of that detail to add back")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def action_params(self, context):
        return {"sigma": self.radius, "strength": self.strength}


classes = (
    PAINTSYSTEM_OT_clear_pixels,
    PAINTSYSTEM_OT_fill_pixels,
    PAINTSYSTEM_OT_invert_pixels,
    PAINTSYSTEM_OT_blur_pixels,
    PAINTSYSTEM_OT_sharpen_pixels,
)

register, unregister = register_classes_factory(classes)
