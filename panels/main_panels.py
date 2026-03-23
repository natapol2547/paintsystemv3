import bpy
from bpy.types import Panel, UIList

from .common import get_icon, PaintSystemPanel


class PAINTSYSTEM_UL_channels(UIList):
    bl_idname = "PAINTSYSTEM_UL_channels"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        socket_icons = {
            'COLOR': get_icon('color_socket'),
            'FLOAT': get_icon('float_socket'),
            'VECTOR': get_icon('vector_socket'),
        }
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "name", text="", emboss=False,
                     icon_value=socket_icons.get(item.type, 'NONE'))
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon=socket_icons.get(item.type, 'NONE'))


def _draw_channel_list(layout, node_tree):
    """Shared UI for the channel UIList and its side buttons."""
    row = layout.row()
    row.template_list(
        "PAINTSYSTEM_UL_channels", "",
        node_tree, "channels",
        node_tree, "active_channel_index",
        rows=3,
    )

    col = row.column(align=True)
    col.operator("paint_system.add_channel", icon='ADD', text="")
    col.operator("paint_system.remove_channel", icon='REMOVE', text="")
    col.separator()
    op = col.operator("paint_system.move_channel_up", icon='TRIA_UP', text="")
    op = col.operator("paint_system.move_channel_down",
                      icon='TRIA_DOWN', text="")


class PAINTSYSTEM_PT_main_3dview(PaintSystemPanel):
    bl_label = "Paint System"
    bl_idname = "PAINTSYSTEM_PT_main_3dview"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Paint System"

    def draw_header_preset(self, context):
        layout = self.layout
        # ps_ctx = self.parse_context(context)
        # row = layout.row(align=True)
        # if ps_ctx.ps_mat_data is None:
        #     return
        # groups = ps_ctx.ps_mat_data.groups
        # if ps_ctx.ps_mat_data and groups:
        #     if len(groups) > 1:
        #         row.popover("MAT_PT_PaintSystemGroups",
        #                     text="", icon="NODETREE")
        #     row.operator("paint_system.new_group", icon='ADD', text="")
        #     row.operator("wm.call_menu", text="",
        #                  icon="REMOVE").name = "MAT_MT_DeleteGroupMenu"
        # else:
        #     row.popover("MAT_PT_Support", icon="FUND", text="Wah!")

    # @classmethod
    # def poll(cls, context):
    #     ps_ctx = cls.parse_context(context)
    #     return ps_ctx.ps_object is not None

    def draw_header(self, context):
        layout = self.layout
        layout.label(icon_value=get_icon("sunflower"))

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        ps = scene.paint_system

        layout.operator("paint_system.create_tree", icon='ADD')

        layout.separator()
        layout.prop(ps, "active_node_tree", text="Tree")

        tree = ps.active_node_tree
        if tree is None or tree.bl_idname != 'PaintSystemNodeTree':
            return

        layout.separator()
        layout.label(text="Channels:")
        _draw_channel_list(layout, tree)


class PAINTSYSTEM_PT_main_node_editor(Panel):
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

        layout.label(text="Channels:")
        _draw_channel_list(layout, tree)


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
