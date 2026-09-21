import bpy
from bpy.types import UIList

from .brush_panels import draw_paint_sections
from .common import get_icon, PaintSystemPanel
from ..common import icon_kwargs
from ..compiler.core import artifact_fingerprint
from ..context import get_active_tree, parse_context
from ..selection import session as selection_session


# Add-on icon of each channel type, for ``icon_kwargs``.
SOCKET_ICONS = {
    'COLOR': 'color_socket',
    'FLOAT': 'float_socket',
    'VECTOR': 'vector_socket',
}


class PAINTSYSTEM_UL_channels(UIList):
    bl_idname = "PAINTSYSTEM_UL_channels"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, **icon_kwargs(SOCKET_ICONS.get(item.type, 'NONE')))


def _draw_channel_list(layout, node_tree):
    row = layout.row()
    row.template_list(
        "PAINTSYSTEM_UL_channels", "",
        node_tree, "channels",
        node_tree, "active_channel_index",
        rows=3,
    )
    col = row.column(align=True)
    col.operator("paint_system.add_channel", text="", **icon_kwargs('ADD'))
    col.operator("paint_system.remove_channel", text="", **icon_kwargs('REMOVE'))
    col.separator()
    col.operator("paint_system.move_channel_up", text="", **icon_kwargs('TRIA_UP'))
    col.operator("paint_system.move_channel_down", text="", **icon_kwargs('TRIA_DOWN'))


def _draw_paint_mode_row(layout, context):
    row = layout.row(align=True)
    row.scale_x = 1.7
    row.scale_y = 1.7
    row.operator("paint_system.toggle_paint_mode", text="Toggle Paint Mode",
                 depress=context.mode == 'PAINT_TEXTURE', **icon_kwargs('paintbrush'))
    row.operator("wm.save_mainfile", text="", **icon_kwargs('save'))


def _draw_selection_section(layout, context, tree):
    """Select all, none and invert, and why the selection cannot be used when it cannot."""
    header, body = layout.panel("paint_system_selection", default_closed=False)
    row = header.row()
    row.label(text="Selection", **icon_kwargs('SELECT_SET'))
    state = selection_session.current()
    live = state.tree_uid == tree.session_uid and state.selected
    problem = live and bool(state.reason)
    if problem:
        row.label(text="", **icon_kwargs('ERROR'))
    if body is None:
        return
    row = body.row(align=True)
    row.operator("paint_system.select_all", text="All").action = 'SELECT'
    row.operator("paint_system.select_all", text="None").action = 'DESELECT'
    row.operator("paint_system.select_all", text="Invert").action = 'INVERT'
    row = body.row(align=True)
    row.operator("paint_system.clear_pixels", text="Clear", **icon_kwargs('IMAGE_ALPHA'))
    row.operator("paint_system.fill_pixels", text="Fill", **icon_kwargs('SNAP_FACE'))
    row.operator("paint_system.invert_pixels", text="Invert Colors", **icon_kwargs('MOD_MASK'))
    row.menu("PAINTSYSTEM_MT_invert_channels", text="", **icon_kwargs('DOWNARROW_HLT'))
    row = body.row(align=True)
    # Placeholder icons.
    row.operator("paint_system.blur_pixels", text="Blur", **icon_kwargs('MOD_SMOOTH'))
    row.operator("paint_system.sharpen_pixels", text="Sharpen",
                 **icon_kwargs('MOD_EDGESPLIT'))
    if problem:
        body.label(text=selection_session.label(state), **icon_kwargs('ERROR'))
    elif live and state.empty:
        body.label(text=selection_session.label(state), **icon_kwargs('INFO'))


def _draw_compiled_info(layout, tree):
    header, body = layout.panel("paint_system_compiled_panel", default_closed=True)
    header.label(text="Compiled Shader")
    if body is None:
        return
    box = body.box()
    compiled = tree.compiled
    if compiled is None:
        box.label(text="Not compiled yet", **icon_kwargs('INFO'))
    else:
        box.label(text=compiled.name, **icon_kwargs('NODETREE'))
        box.label(text=f"{len(compiled.nodes)} nodes, {len(compiled.links)} links, "
                       f"fingerprint {artifact_fingerprint(tree)[:8]}")
    box.operator("paint_system.compile_tree", **icon_kwargs('FILE_REFRESH'))


class PAINTSYSTEM_PT_main_3dview(PaintSystemPanel):
    bl_label = "Paint System"
    bl_idname = "PAINTSYSTEM_PT_main_3dview"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Paint System"

    def draw_header(self, context):
        self.layout.label(icon_value=get_icon("sunflower"))

    def draw(self, context):
        layout = self.layout
        obj = context.object
        mat = obj.active_material if obj is not None else None

        if mat is None or mat.paint_system.tree is None:
            layout.operator("paint_system.setup_material", **icon_kwargs('ADD'))
            layout.separator()
            layout.prop(context.scene.paint_system, "active_node_tree", text="Tree")
        else:
            row = layout.row(align=True)
            row.label(text=mat.name, **icon_kwargs('MATERIAL'))
            row.prop(mat.paint_system, "tree", text="")

        tree = get_active_tree(context)
        if tree is None:
            return

        if parse_context(context).ps_object is not None:
            _draw_paint_mode_row(layout, context)
        layout.separator()
        layout.label(text="Channels")
        _draw_channel_list(layout, tree)
        draw_paint_sections(layout, context)
        if context.mode == 'PAINT_TEXTURE':
            _draw_selection_section(layout, context, tree)
        _draw_compiled_info(layout, tree)


class PAINTSYSTEM_PT_main_node_editor(PaintSystemPanel):
    bl_label = "Paint System"
    bl_idname = "PAINTSYSTEM_PT_main_node_editor"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Paint System"

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return (space and space.tree_type == 'PaintSystemNodeTree'
                and space.edit_tree is not None)

    def draw(self, context):
        layout = self.layout
        tree = context.space_data.edit_tree

        layout.label(text="Channels")
        _draw_channel_list(layout, tree)
        _draw_compiled_info(layout, tree)


classes = (
    PAINTSYSTEM_UL_channels,
    PAINTSYSTEM_PT_main_3dview,
    PAINTSYSTEM_PT_main_node_editor,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
