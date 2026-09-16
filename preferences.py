from bpy.types import AddonPreferences
from bpy.props import BoolProperty, FloatProperty, EnumProperty
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
