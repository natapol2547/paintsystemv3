"""Brush and Color sections of the main panel in texture paint mode (PS-033).

Both draw with Blender's own paint panel helpers, which follow the brush
system of the running version: brushes are local data up to 4.2 and
assets from 4.3, and the paint settings come from the active tool from 5.3.
"""
from bl_ui import properties_paint_common
from bl_ui.properties_paint_common import UnifiedPaintPanel, brush_settings, draw_color_settings

from ..common import icon_kwargs
from ..context import paint_settings


def texture_paint_settings(context):
    """The paint settings while texture painting in this context, else None."""
    if UnifiedPaintPanel.get_brush_mode(context) != 'PAINT_TEXTURE':
        return None
    return paint_settings(context)


def draw_brush_selector(layout, context, settings):
    shelf = getattr(properties_paint_common, 'BrushAssetShelf', None)
    if hasattr(shelf, 'draw_popup_selector'):
        # Brush assets: the asset shelf popover the tool settings header uses.
        shelf.draw_popup_selector(layout, context, settings.brush)
    else:
        layout.template_ID_preview(settings, "brush", new="brush.add", rows=3, cols=8, hide_buttons=False)


def draw_brush_settings(layout, context, settings):
    layout.use_property_split = False
    layout.use_property_decorate = False
    draw_brush_selector(layout, context, settings)
    brush = settings.brush
    if brush is None:
        return
    col = layout.box().column(align=True)
    brush_settings(col, context, brush, popover=False)

    header, body = col.panel("paint_system_brush_advanced", default_closed=True)
    header.label(text="Advanced Settings")
    if body is None:
        return
    image_paint = context.tool_settings.image_paint
    body.prop(image_paint, "use_occlude", text="Occlude Faces")
    body.prop(image_paint, "use_backface_culling", text="Backface Culling")
    body.prop(image_paint, "use_normal_falloff", text="Normal Falloff")
    angle = body.column(align=True)
    angle.use_property_split = True
    angle.active = image_paint.use_normal_falloff
    angle.prop(image_paint, "normal_angle", text="Angle")


def draw_color_header(layout, context, brush):
    """The brush colours and the flip button, for the header of the closed section."""
    row = layout.row(align=True)
    row.alignment = 'RIGHT'
    split = row.split(factor=0.5, align=True)
    UnifiedPaintPanel.prop_unified_color(split, context, brush, "color", text="")
    UnifiedPaintPanel.prop_unified_color(split, context, brush, "secondary_color", text="")
    row.operator("paint.brush_colors_flip", text="", **icon_kwargs('FILE_REFRESH'))


def draw_color_body(layout, context, settings):
    """Colour or gradient picker, colour swatches and jitter, then the palette."""
    col = layout.column()
    draw_color_settings(context, col, settings.brush, color_type=True)
    header, body = col.panel("paint_system_color_palette", default_closed=True)
    header.label(text="Color Palette")
    if body is None:
        return
    body.template_ID(settings, "palette", new="palette.new")
    if settings.palette is not None:
        body.template_palette(settings, "palette", color=True)


def draw_paint_sections(layout, context):
    """The Brush and Color sections, drawn only while texture painting."""
    settings = texture_paint_settings(context)
    if settings is None:
        return
    header, body = layout.panel("MAT_PT_Brush", default_closed=True)
    header.label(text="Brush", **icon_kwargs('brush'))
    if body is not None:
        draw_brush_settings(body, context, settings)

    brush = settings.brush
    if brush is None or not brush.image_paint_capabilities.has_color:
        return
    header, body = layout.panel("MAT_PT_BrushColor", default_closed=True)
    header.label(text="Color", **icon_kwargs('color'))
    if body is None:
        draw_color_header(header, context, brush)
    else:
        draw_color_body(body, context, settings)
