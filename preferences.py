from bpy.types import AddonPreferences
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty
from bpy.utils import register_classes_factory

from .common import ADDON_ID, icon_kwargs


class PaintSystemPreferences(AddonPreferences):
    bl_idname = ADDON_ID

    # Selection overlay (PS-091). Colours are display values; the overlay
    # converts them to linear before drawing.
    show_selection_3d: BoolProperty(
        name="Show Selection in 3D View",
        description="Draw the selection over the painted object in Texture Paint mode",
        default=True
    )
    selection_wash_color: FloatVectorProperty(
        name="Selection Tint",
        description="Color laid over the selected part of the layer",
        subtype='COLOR_GAMMA',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.25, 0.55, 1.0)
    )
    selection_wash_opacity: FloatProperty(
        name="Tint Opacity",
        description="How strongly the tint covers the selected part of the layer",
        subtype='FACTOR',
        min=0.0,
        max=1.0,
        default=0.0
    )
    selection_ant_color_a: FloatVectorProperty(
        name="Outline Dash Color",
        description="Color of the dashes in the selection outline",
        subtype='COLOR_GAMMA',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0)
    )
    show_action_bar: BoolProperty(
        name="Selection Action Bar",
        description="Show a row of action buttons in the 3D view while a selection is live",
        default=True
    )
    selection_ant_color_b: FloatVectorProperty(
        name="Outline Gap Color",
        description="Color between the dashes in the selection outline",
        subtype='COLOR_GAMMA',
        size=3,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0)
    )

    def draw(self, context):
        selection_box = self.layout.box()
        selection_box.label(text="Selection", **icon_kwargs('SELECT_SET'))
        selection_box.use_property_split = True
        selection_box.prop(self, "show_selection_3d")
        selection_box.prop(self, "show_action_bar")
        selection_box.prop(self, "selection_wash_color")
        selection_box.prop(self, "selection_wash_opacity")
        selection_box.prop(self, "selection_ant_color_a")
        selection_box.prop(self, "selection_ant_color_b")


classes = (
    PaintSystemPreferences,
)

register, unregister = register_classes_factory(classes)
