"""The Rectangle, Ellipse and Lasso Selection tools in the 3D view's Texture Paint toolbar (PS-093).

The three tools form one group after Blender's Mask tool, behind a
separator; where the toolbar has no Mask tool (4.2) the group goes at the
end. Each tool's keymap is created by Blender in the add-on keyconfig and
is active only while the tool is, so it shadows default items only then.
It binds exactly five items: a drag for each mode, and a click that clears
the selection. The header shows the settings the tool's operator saves:
feather, anti-alias and through.
"""
import bpy
from bpy.types import WorkSpaceTool

AFTER_MASK = frozenset(('builtin_brush.mask',))
"""Toolbar ids the group is placed after."""


def shape_keymap(idname: str) -> tuple:
    """The tool keymap of the operator *idname*: drags with modifiers pick the mode, a click deselects."""
    drag = {"type": 'LEFTMOUSE', "value": 'CLICK_DRAG'}
    return (
        (idname, drag, {"properties": [("mode", 'REPLACE')]}),
        (idname, {**drag, "shift": True}, {"properties": [("mode", 'ADD')]}),
        (idname, {**drag, "ctrl": True}, {"properties": [("mode", 'SUBTRACT')]}),
        (idname, {**drag, "shift": True, "ctrl": True}, {"properties": [("mode", 'INTERSECT')]}),
        ("paint_system.select_all", {"type": 'LEFTMOUSE', "value": 'CLICK'}, {"properties": [("action", 'DESELECT')]}),
    )


def draw_settings(context, layout, tool) -> None:
    """The tool header: the operator settings a drag with this tool uses."""
    props = tool.operator_properties(tool.idname)
    layout.prop(props, "feather")
    layout.prop(props, "antialias")
    layout.prop(props, "through")


class PAINTSYSTEM_WT_select_box(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'PAINT_TEXTURE'
    bl_idname = "paint_system.select_box"
    bl_label = "Rectangle Selection"
    bl_description = "Select a rectangle of the painted surface"
    bl_icon = "ops.generic.select_box"
    bl_widget = None
    bl_keymap = shape_keymap(bl_idname)
    draw_settings = draw_settings


class PAINTSYSTEM_WT_select_ellipse(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'PAINT_TEXTURE'
    bl_idname = "paint_system.select_ellipse"
    bl_label = "Ellipse Selection"
    bl_description = "Select an ellipse of the painted surface"
    bl_icon = "ops.generic.select_circle"
    bl_widget = None
    bl_keymap = shape_keymap(bl_idname)
    draw_settings = draw_settings


class PAINTSYSTEM_WT_select_lasso(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'PAINT_TEXTURE'
    bl_idname = "paint_system.select_lasso"
    bl_label = "Lasso Selection"
    bl_description = "Select a free-hand outline of the painted surface"
    bl_icon = "ops.generic.select_lasso"
    bl_widget = None
    bl_keymap = shape_keymap(bl_idname)
    draw_settings = draw_settings


TOOLS = (
    PAINTSYSTEM_WT_select_box,
    PAINTSYSTEM_WT_select_ellipse,
    PAINTSYSTEM_WT_select_lasso,
)


def register() -> None:
    first = TOOLS[0]
    bpy.utils.register_tool(first, after=set(AFTER_MASK), separator=True, group=True)
    previous = first
    for tool in TOOLS[1:]:
        bpy.utils.register_tool(tool, after={previous.bl_idname})
        previous = tool


def unregister() -> None:
    for tool in reversed(TOOLS):
        bpy.utils.unregister_tool(tool)
