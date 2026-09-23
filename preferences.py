from bpy.types import AddonPreferences
from bpy.props import BoolProperty, FloatVectorProperty
from bpy.utils import register_classes_factory

from .common import ADDON_ID, icon_kwargs
from .selection.overlay import DEFAULTS as OVERLAY_DEFAULTS


class PaintSystemPreferences(AddonPreferences):
    bl_idname = ADDON_ID

    # Selection overlay. The overlay falls back to the same defaults when
    # the add-on has no preferences entry. The colours are display (sRGB)
    # values with straight alpha. The overlay converts them to linear
    # before drawing.
    show_selection_3d: BoolProperty(
        name="Show Selection in 3D View",
        description="Draw the selection over the painted object in Texture Paint mode",
        default=OVERLAY_DEFAULTS["show_selection_3d"]
    )
    selection_wash_color: FloatVectorProperty(
        name="Selection Tint",
        description="Color laid over the selected part of the layer. Its alpha sets how strongly it covers the layer",
        subtype='COLOR_GAMMA',
        size=4,
        min=0.0,
        max=1.0,
        default=OVERLAY_DEFAULTS["selection_wash_color"]
    )
    selection_ant_color_a: FloatVectorProperty(
        name="Outline Dash Color",
        description="Color of the dashes in the selection outline",
        subtype='COLOR_GAMMA',
        size=4,
        min=0.0,
        max=1.0,
        default=OVERLAY_DEFAULTS["selection_ant_color_a"]
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
        size=4,
        min=0.0,
        max=1.0,
        default=OVERLAY_DEFAULTS["selection_ant_color_b"]
    )

    show_developer_extras: BoolProperty(
        name="Developer Extras",
        description="Show tools for inspecting what Paint System builds, such as the Compiled Shader section "
                    "of the main panel",
        default=False
    )

    def draw(self, context):
        selection_box = self.layout.box()
        selection_box.label(text="Selection", **icon_kwargs('SELECT_SET'))
        selection_box.use_property_split = True
        selection_box.prop(self, "show_selection_3d")
        selection_box.prop(self, "show_action_bar")
        selection_box.prop(self, "selection_wash_color")
        selection_box.prop(self, "selection_ant_color_a")
        selection_box.prop(self, "selection_ant_color_b")

        # Placeholder icon.
        developer_box = self.layout.box()
        developer_box.label(text="Developer", **icon_kwargs('CONSOLE'))
        developer_box.use_property_split = True
        developer_box.prop(self, "show_developer_extras")


classes = (
    PaintSystemPreferences,
)

register, unregister = register_classes_factory(classes)
