import bpy
from bpy.types import UIList

from .common import get_icon, PaintSystemPanel
from ..context import get_active_tree


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
    col.operator("paint_system.move_channel_up", icon='TRIA_UP', text="")
    col.operator("paint_system.move_channel_down", icon='TRIA_DOWN', text="")


def _draw_layer_stack(layout, tree):
    """Linear stack feeding the active channel, top-most first."""
    row = layout.row(align=True)
    op = row.operator("paint_system.add_layer", text="Image", icon='IMAGE_DATA')
    op.layer_type = 'IMAGE'
    op = row.operator("paint_system.add_layer", text="Solid", icon='COLOR')
    op.layer_type = 'SOLID'
    row.operator("paint_system.remove_layer", text="", icon='REMOVE')

    chain = tree.layer_chain()
    if not chain:
        layout.label(text="No layers on this channel", icon='INFO')
        return
    active = tree.nodes.active
    col = layout.column(align=True)
    for node in chain:
        row = col.row(align=True)
        row.prop(node, "enabled", text="")
        icon = 'IMAGE_DATA' if node.bl_idname == 'PaintSystemImageLayerNode' else 'COLOR'
        op = row.operator("paint_system.set_active_layer", text=node.name,
                          icon=icon, depress=(node == active))
        op.node_name = node.name
        row.prop(node, "opacity", text="", slider=True)
        if node.cache_enabled and node.cache_image is not None:
            row.label(text="", icon='ERROR' if node.cache_stale else 'CHECKMARK')


def _draw_compiled_info(layout, tree):
    box = layout.box()
    compiled = tree.compiled
    if compiled is None:
        box.label(text="Not compiled yet", icon='INFO')
    else:
        box.label(text=compiled.name, icon='NODETREE')
        box.label(text=f"{len(compiled.nodes)} nodes, {len(compiled.links)} links, "
                       f"fingerprint {tree.compiled_hash[:8]}")
    box.operator("paint_system.compile_tree", icon='FILE_REFRESH')


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
            layout.operator("paint_system.setup_material", icon='ADD')
            layout.separator()
            layout.prop(context.scene.paint_system, "active_node_tree", text="Tree")
        else:
            row = layout.row(align=True)
            row.label(text=mat.name, icon='MATERIAL')
            row.prop(mat.paint_system, "tree", text="")

        tree = get_active_tree(context)
        if tree is None:
            return

        layout.separator()
        layout.label(text="Channels")
        _draw_channel_list(layout, tree)

        layout.separator()
        layout.label(text="Layers")
        _draw_layer_stack(layout, tree)

        layout.separator()
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

        layout.separator()
        layout.label(text="Layers")
        _draw_layer_stack(layout, tree)

        layout.separator()
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
