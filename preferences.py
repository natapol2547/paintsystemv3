from bpy.types import AddonPreferences
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, EnumProperty
from bpy.utils import register_classes_factory

from .common import ADDON_ID


class PaintSystemPreferences(AddonPreferences):
    bl_idname = ADDON_ID

    show_tooltips: BoolProperty(
        name="Show Tooltips",
        description="Show tooltips in the UI",
        default=True
    )
    show_hex_color: BoolProperty(
        name="Show Hex Color",
        description="Show hex color in the color picker settings",
        default=False
    )
    show_more_color_picker_settings: BoolProperty(
        name="Show More Color Picker Settings",
        description="Show more color picker settings",
        default=False
    )

    show_opacity_in_layer_list: BoolProperty(
        name="Show Opacity in Layer List",
        description="Show the opacity in the layer list",
        default=True
    )

    use_compact_design: BoolProperty(
        name="Use Compact Design",
        description="Use a more compact design for the UI",
        default=False
    )

    color_picker_scale: FloatProperty(
        name="Color Picker Scale",
        description="Scale the color picker",
        default=1.0,
        min=0.5,
        max=3.0
    )

    preferred_coord_type: EnumProperty(
        name="Preferred Coordinate Type",
        description="Preferred coordinate type",
        items=(
            ('AUTO', 'Auto UV', ''),
            ('UV', 'UV', ''),
            ('UNDETECTED', 'Undetected', ''),
        ),
        default='UNDETECTED',
    )

    color_picker_scale_popover: FloatProperty(
        name="Color Wheel Scale",
        description="Scale the color wheel in the color popover",
        default=1.2,
        min=0.5,
        max=3.0
    )

    # Tips
    hide_norm_paint_tips: BoolProperty(
        name="Hide Normal Painting Tips",
        description="Hide the normal painting tips",
        default=False
    )
    hide_color_attr_tips: BoolProperty(
        name="Hide Color Attribute Tips",
        description="Hide the color attribute tips",
        default=False
    )

    use_panel_quick_access: BoolProperty(
        name="Use Panel Quick Access",
        description="Use the panel quick access",
        default=False
    )

    developer_mode: BoolProperty(
        name="Developer Mode",
        description="Enable developer mode for verbose logging",
        default=False
    )

    # Color popover options
    show_hsv_sliders_popover: BoolProperty(
        name="Show Hue/Saturation/Value Sliders",
        description="Show HSV sliders under the color wheel in the color popover",
        default=False
    )
    show_active_palette_popover: BoolProperty(
        name="Show Active Palette",
        description="Show the active palette swatches in the color popover",
        default=True
    )
    show_brush_settings_popover: BoolProperty(
        name="Show Brush Controls",
        description="Show brush radius and strength controls in the color popover",
        default=True
    )

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
        default=0.2
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
        layout = self.layout

        layout.prop(self, "show_tooltips", text="Show Tooltips")
        layout.prop(self, "use_compact_design", text="Use Compact Design")
        layout.prop(self, "show_opacity_in_layer_list",
                    text="Show Opacity in Layer List")
        layout.prop(self, "use_panel_quick_access",
                    text="Use Panel Quick Access")

        dev_box = layout.box()
        dev_box.label(text="Advanced", icon='PREFERENCES')
        dev_box.prop(self, "developer_mode", text="Developer Mode")

        selection_box = layout.box()
        selection_box.label(text="Selection", icon='SELECT_SET')
        selection_box.use_property_split = True
        selection_box.prop(self, "show_selection_3d")
        selection_box.prop(self, "selection_wash_color")
        selection_box.prop(self, "selection_wash_opacity")
        selection_box.prop(self, "selection_ant_color_a")
        selection_box.prop(self, "selection_ant_color_b")

        # --- Color popover ---
        popover_box = layout.box()
        popover_box.label(text="Color Popover", icon='COLOR')
        popover_box.prop(self, "color_picker_scale_popover",
                         text="Color Wheel Scale")
        popover_box.prop(self, "show_hsv_sliders_popover",
                         text="Show HSV Sliders")
        popover_box.prop(self, "show_active_palette_popover",
                         text="Show Active Palette")
        popover_box.prop(self, "show_brush_settings_popover",
                         text="Show Brush Controls")


classes = (
    PaintSystemPreferences,
)

register, unregister = register_classes_factory(classes)
