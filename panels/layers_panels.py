from dataclasses import dataclass

import bpy
from bpy.types import Menu, Panel, UIList

from ..common import icon_kwargs
from ..context import get_active_tree, node_editor_tree, parse_context
from ..nodes.layers.registry import layer_types
from ..nodetree.stack_ops import is_layer


@dataclass(frozen=True)
class LayerRow:
    """How the layer list shows one layer of the stack."""
    order: int
    level: int
    # No folder around the layer is collapsed.
    visible: bool
    # Every folder around the layer is enabled.
    parent_enabled: bool


def layer_rows(tree) -> dict[str, LayerRow]:
    """Rows of the active channel's stack by node name, top first."""
    rows: dict[str, LayerRow] = {}
    for order, item in enumerate(tree.stack()):
        visible = parent_enabled = True
        if item.parent is not None:
            folder = item.parent.node
            parent = rows[folder.name]
            visible = parent.visible and folder.is_expanded
            parent_enabled = parent.parent_enabled and folder.enabled
        rows[item.node.name] = LayerRow(order, item.level, visible, parent_enabled)
    return rows


# ``filter_items`` stores the rows it computed for ``draw_item``, which
# Blender calls right after it for every shown row of the same list.
_rows_by_tree: dict[int, dict[str, LayerRow]] = {}


class PAINTSYSTEM_UL_layers(UIList):
    """The layer nodes of a tree in stack order, folder content indented under its folder."""
    bl_idname = "PAINTSYSTEM_UL_layers"

    def filter_items(self, context, data, propname):
        rows = layer_rows(data)
        _rows_by_tree[data.as_pointer()] = rows
        flags = []
        order = []
        # Nodes outside the stack are hidden and sorted after it.
        after = len(rows)
        for node in getattr(data, propname):
            row = rows.get(node.name)
            if row is None:
                flags.append(0)
                order.append(after)
                after += 1
            else:
                flags.append(self.bitflag_filter_item if row.visible else 0)
                order.append(row.order)
        return flags, order

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row_state = _rows_by_tree.get(data.as_pointer(), {}).get(item.name)
        if row_state is None or not is_layer(item):
            return
        main_row = layout.row(align=True)
        # ``active`` greys a row out and, unlike ``enabled``, keeps it usable:
        # a disabled folder still expands, and the layers in it still rename.
        main_row.active = row_state.parent_enabled

        row = main_row.row(align=True)
        for level in range(row_state.level):
            if level == row_state.level - 1:
                row.label(text="", **icon_kwargs('folder_indent'))
            else:
                row.label(text="", **icon_kwargs('BLANK1'))
        row.active = item.enabled and item.opacity > 0
        if item.is_clip:
            clip = row.row()
            clip.scale_x = 0.7
            clip.label(text="", **icon_kwargs('clipping'))
        item.draw_row_icon(row)
        main_row.separator()
        main_row.prop(item, "name", text="", emboss=False)

        row = main_row.row(align=True)
        row.alignment = 'RIGHT'
        item.draw_row_state(row)
        if item.lock_layer:
            row.label(text="", **icon_kwargs('VIEW_LOCKED', 'LOCKED'))
        row.prop(item, "enabled", text="", emboss=False,
                 **icon_kwargs('HIDE_OFF' if item.enabled else 'HIDE_ON'))


class PAINTSYSTEM_MT_add_layer(Menu):
    bl_idname = "PAINTSYSTEM_MT_add_layer"
    bl_label = "Add Layer"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = 'INVOKE_REGION_WIN'
        section = None
        for node_class in layer_types():
            if section is not None and node_class.ps_menu_section != section:
                layout.separator()
            section = node_class.ps_menu_section
            op = layout.operator("paint_system.add_layer", text=node_class.ps_label,
                                 **icon_kwargs(*node_class.ps_icon))
            op.layer_type = node_class.ps_type


def draw_layer_properties(layout, context, node):
    """Clip, lock alpha, lock, blend mode and opacity of the active layer, on one row when the sidebar is wide enough."""
    ui_scale = context.preferences.view.ui_scale
    region = getattr(context, 'region', None)
    wide = region is not None and region.width - 70 * ui_scale > 170 * ui_scale
    split = layout.split(factor=0.7) if wide else layout.column(align=True)
    split.scale_x = 1.3
    split.scale_y = 1.3
    row = split.row(align=True)
    clip = row.row(align=True)
    clip.enabled = not node.lock_layer
    clip.prop(node, "is_clip", text="", **icon_kwargs('SELECT_INTERSECT'))
    if node.paint_image is not None:
        clip.prop(node, "lock_alpha", text="", **icon_kwargs('TEXTURE'))
    row.prop(node, "lock_layer", text="", **icon_kwargs('VIEW_LOCKED', 'LOCKED'))
    if node.ps_shows_blend_mode:
        blend = row.row(align=True)
        blend.enabled = not node.lock_layer
        blend.prop(node, "blend_mode", text="")
    opacity = split.row(align=True)
    opacity.enabled = not node.lock_layer
    if not wide:
        opacity.scale_y = 0.8
    opacity.prop(node, "opacity", text="" if wide else node.ps_opacity_label, slider=True)


def draw_layer_sidebar(col):
    col.scale_x = 1.2
    col.operator("wm.call_menu", text="", **icon_kwargs('layer_add')).name = PAINTSYSTEM_MT_add_layer.bl_idname
    op = col.operator("paint_system.add_layer", text="", **icon_kwargs('folder'))
    op.layer_type = 'FOLDER'
    col.separator(type='LINE')
    col.operator("paint_system.remove_layer", text="", **icon_kwargs('trash'))
    col.separator(type='LINE')
    col.operator("paint_system.move_layer_up", text="", **icon_kwargs('TRIA_UP'))
    col.operator("paint_system.move_layer_down", text="", **icon_kwargs('TRIA_DOWN'))


class LayersPanel:
    bl_label = "Layers"
    bl_region_type = 'UI'
    bl_category = "Paint System"

    def draw_header(self, context):
        self.layout.label(text="", **icon_kwargs('layers'))

    def draw(self, context):
        layout = self.layout
        ps = parse_context(context)
        tree, node = ps.tree, ps.layer

        box = layout.box()
        if node is not None:
            draw_layer_properties(box.box(), context, node)
        row = box.row()
        row.scale_y = 1.5
        row.template_list(PAINTSYSTEM_UL_layers.bl_idname, "", tree, "nodes", tree, "active_layer_index",
                          rows=min(max(6, len(tree.stack())), 7))
        draw_layer_sidebar(row.column(align=True))

        if node is None:
            return
        header, body = layout.panel("layer_settings_panel")
        header.label(text="Layer Settings")
        if body is not None:
            body.enabled = not node.lock_layer
            node.draw_source_settings(context, body)


class PAINTSYSTEM_PT_layers_3dview(LayersPanel, Panel):
    bl_idname = "PAINTSYSTEM_PT_layers_3dview"
    bl_space_type = 'VIEW_3D'

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        return tree is not None and tree.active_channel is not None


class PAINTSYSTEM_PT_layers_node_editor(LayersPanel, Panel):
    bl_idname = "PAINTSYSTEM_PT_layers_node_editor"
    bl_space_type = 'NODE_EDITOR'

    @classmethod
    def poll(cls, context):
        tree = node_editor_tree(context)
        return tree is not None and tree.active_channel is not None


classes = (
    PAINTSYSTEM_UL_layers,
    PAINTSYSTEM_MT_add_layer,
    PAINTSYSTEM_PT_layers_3dview,
    PAINTSYSTEM_PT_layers_node_editor,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    _rows_by_tree.clear()
