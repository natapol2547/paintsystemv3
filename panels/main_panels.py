from bpy.types import Menu, Panel, UIList
from bpy.utils import register_classes_factory

from .brush_panels import draw_paint_sections
from ..common import icon_kwargs
from ..compiler.core import artifact_fingerprint
from ..context import find_material_group_node, get_active_tree, get_ps_object, material_input, node_editor_tree
from ..nodes.layers.base_layer_node import draw_uv_map
from ..props.channel import SOCKET_ICONS, channel_alpha_name
from ..selection import session as selection_session
from ..templates import CHANNEL_TEMPLATES


class PAINTSYSTEM_UL_channels(UIList):
    """The channels of a tree, each with the value its stack starts from."""
    bl_idname = "PAINTSYSTEM_UL_channels"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        # The base value is the material's input, so a shared tree can
        # start from a different value in each material. A vector does not
        # fit in the row (PS-006).
        base = material_input(context, data, item.name) if item.type != 'VECTOR' else None
        if base is not None:
            row = row.split(factor=0.7, align=True)
        row.prop(item, "name", text="", emboss=False, **icon_kwargs(SOCKET_ICONS.get(item.type, 'NONE')))
        if base is not None:
            row.prop(base, "default_value", text="")


class PAINTSYSTEM_MT_add_channel(Menu):
    """The channel templates the tree does not have yet, then a custom channel."""
    bl_idname = "PAINTSYSTEM_MT_add_channel"
    bl_label = "Add Channel"

    def draw(self, context):
        layout = self.layout
        tree = get_active_tree(context)
        names = {channel.name for channel in tree.channels} if tree is not None else set()
        missing = [(key, template) for key, template in CHANNEL_TEMPLATES.items() if template.name not in names]
        for key, template in missing:
            layout.operator("paint_system.add_channel", text=template.name,
                            **icon_kwargs(SOCKET_ICONS[template.type])).template = key
        if missing:
            layout.separator()
        layout.operator("paint_system.add_channel", text="Custom...", **icon_kwargs('ADD')).template = 'CUSTOM'


def _draw_channel_settings(layout, context, tree):
    """Draw the active channel's options in a section that starts closed."""
    channel = tree.active_channel
    if channel is None:
        return
    header, body = layout.panel("paint_system_channel_settings", default_closed=True)
    header.label(text="Channel Settings")
    if body is None:
        return
    col = body.column()
    col.use_property_split = True
    col.use_property_decorate = False
    col.prop(channel, "type")
    col.prop(channel, "color_space")
    col.prop(channel, "use_alpha")
    if channel.use_alpha:
        alpha = material_input(context, tree, channel_alpha_name(channel.name))
        if alpha is not None:
            col.prop(alpha, "default_value", text="Base Alpha")
    if channel.type == 'FLOAT':
        col.prop(channel, "use_range")
        sub = col.column(align=True)
        sub.active = channel.use_range
        sub.prop(channel, "range_min")
        sub.prop(channel, "range_max")
    elif channel.type == 'VECTOR':
        col.row().prop(channel, "vector_kind", expand=True)
        col.prop(channel, "paint_space")
        if channel.paint_space == 'TANGENT':
            draw_uv_map(context, col, channel, get_ps_object(context.object), prop="tangent_uv_map")


def _draw_channels_section(layout, context, tree):
    """Draw the channel list and the channel settings in a section that can be collapsed.

    While the section is closed, its header names the active channel, so
    the channel being painted stays in view.
    """
    header, body = layout.panel("paint_system_channels", default_closed=False)
    row = header.row()
    row.label(text="Channels")
    if body is None:
        channel = tree.active_channel
        if channel is not None:
            row.label(text=channel.name, **icon_kwargs(SOCKET_ICONS.get(channel.type, 'NONE')))
        return
    row = body.row()
    row.template_list(
        "PAINTSYSTEM_UL_channels", "",
        tree, "channels",
        tree, "active_channel_index",
        rows=3,
    )
    col = row.column(align=True)
    col.menu("PAINTSYSTEM_MT_add_channel", text="", **icon_kwargs('ADD'))
    col.operator("paint_system.remove_channel", text="", **icon_kwargs('REMOVE'))
    col.separator()
    col.operator("paint_system.move_channel_up", text="", **icon_kwargs('TRIA_UP'))
    col.operator("paint_system.move_channel_down", text="", **icon_kwargs('TRIA_DOWN'))
    _draw_channel_settings(body, context, tree)


def _draw_paint_mode_row(layout, context, tree):
    row = layout.row(align=True)
    row.scale_x = 1.7
    row.scale_y = 1.7
    row.operator("paint_system.toggle_paint_mode", text="Toggle Paint Mode",
                 depress=context.mode == 'PAINT_TEXTURE', **icon_kwargs('paintbrush'))
    channel = tree.active_channel
    icon = SOCKET_ICONS.get(channel.type, 'HIDE_OFF') if channel is not None else 'HIDE_OFF'
    row.operator("paint_system.preview_channel", text="", depress=tree.preview_channel, **icon_kwargs(icon))
    row.operator("wm.save_mainfile", text="", **icon_kwargs('save'))


def _draw_selection_section(layout, context, tree):
    """Draw the Selection section with its select and pixel action buttons.

    When the selection cannot be used, the section also says why. It
    starts closed: it only matters once something is selected, and the
    error icon on its header still shows while it is closed.
    """
    header, body = layout.panel("paint_system_selection", default_closed=True)
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


class PAINTSYSTEM_PT_main_3dview(Panel):
    bl_label = "Paint System"
    bl_idname = "PAINTSYSTEM_PT_main_3dview"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Paint System"

    def draw_header(self, context):
        self.layout.label(text="", **icon_kwargs('sunflower'))

    def draw(self, context):
        layout = self.layout
        # An empty parented to a mesh paints that mesh, so its material shows here.
        obj = get_ps_object(context.object)
        mat = obj.active_material if obj is not None else None

        if mat is None or mat.paint_system.tree is None:
            layout.operator("paint_system.setup_material", **icon_kwargs('ADD'))
            layout.separator()
            layout.prop(context.scene.paint_system, "active_node_tree", text="Tree")
        else:
            row = layout.row(align=True)
            row.label(text=mat.name, **icon_kwargs('MATERIAL'))
            row.prop(mat.paint_system, "tree", text="")
            # The tree's group node was deleted, or the tree field now names
            # another tree, so the material does not show the tree.
            if find_material_group_node(mat, mat.paint_system.tree) is None:
                layout.operator("paint_system.setup_material", text="Connect to the Material",
                                **icon_kwargs('LINKED'))

        tree = get_active_tree(context)
        if tree is None:
            return

        if obj is not None:
            _draw_paint_mode_row(layout, context, tree)
        layout.separator()
        _draw_channels_section(layout, context, tree)
        draw_paint_sections(layout, context)
        if context.mode == 'PAINT_TEXTURE':
            _draw_selection_section(layout, context, tree)
        _draw_compiled_info(layout, tree)


class PAINTSYSTEM_PT_main_node_editor(Panel):
    bl_label = "Paint System"
    bl_idname = "PAINTSYSTEM_PT_main_node_editor"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Paint System"

    @classmethod
    def poll(cls, context):
        return node_editor_tree(context) is not None

    def draw(self, context):
        layout = self.layout
        tree = context.space_data.edit_tree

        _draw_channels_section(layout, context, tree)
        _draw_compiled_info(layout, tree)


classes = (
    PAINTSYSTEM_UL_channels,
    PAINTSYSTEM_MT_add_channel,
    PAINTSYSTEM_PT_main_3dview,
    PAINTSYSTEM_PT_main_node_editor,
)


register, unregister = register_classes_factory(classes)
