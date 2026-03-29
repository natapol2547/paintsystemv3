import bpy
import uuid

from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Node, PropertyGroup, UILayout

from .builder import NodeTreeBuilder

from ..common import get_next_unique_name


BLEND_MODE_ITEMS = []
for blend_mode in bpy.types.ShaderNodeMixRGB.bl_rna.properties['blend_type'].enum_items:
    BLEND_MODE_ITEMS.append(
        (blend_mode.identifier, blend_mode.name, blend_mode.description))
    if blend_mode.identifier in ["MIX", "COLOR_BURN", "ADD", "LINEAR_LIGHT", "DIVIDE"]:
        BLEND_MODE_ITEMS.append(None)


def set_name_transform(self, new_value, curr_value, is_set):
    node_tree = self.id_data
    if node_tree and node_tree.bl_idname == 'PaintSystemNodeTree':
        return get_next_unique_name(
            new_value, [node.name for node in node_tree.nodes if node != self])
    return new_value


class PaintSystemNodePanel(PropertyGroup):
    idname: StringProperty(name="ID Name", default="")
    label: StringProperty(name="Label", default="")
    expanded: BoolProperty(name="Expanded", default=True)


class PaintSystemNode(Node):
    name: StringProperty(name="Name", default="",
                         set_transform=set_name_transform)
    panels: CollectionProperty(type=PaintSystemNodePanel)
    uuid: StringProperty(name="UUID")

    def init(self, context):
        self.name = self.bl_label
        self.uuid = str(uuid.uuid4())

    def copy(self, node):
        self.name = node.name
        self.uuid = str(uuid.uuid4())

    def free(self):
        pass

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == 'PaintSystemNodeTree'

    def create_panel(self, idname, label, default_closed=True):
        panel = self.panels.add()
        panel.idname = idname
        panel.expanded = default_closed
        panel.label = label
        return panel

    def draw_panel(self, layout: UILayout, idname):
        panel_data = next(
            (panel for panel in self.panels if panel.idname == idname), None)
        if not panel_data:
            raise ValueError(f"Panel with idname {idname} not found")
        panel = None
        col = layout.column()
        header = col.row(align=True)
        header.alignment = 'LEFT'
        header.prop(panel_data, "expanded",
                    text=panel_data.label, emboss=False, icon='DOWNARROW_HLT' if panel_data.expanded else 'RIGHTARROW')
        if panel_data.expanded:
            panel = col
        return header, panel


class PaintSystemLayerNode(PaintSystemNode):
    bl_width_default = 200
    latest_version = 1

    opacity: FloatProperty(name="Opacity", default=1.0,
                           min=0.0, max=1.0, subtype='FACTOR')
    blend_mode: EnumProperty(
        name="Blend Mode", items=BLEND_MODE_ITEMS, default='MIX')
    shader_node_tree: PointerProperty(
        type=bpy.types.NodeTree, name="Shader Node Tree", description="Shader Node Tree for this layer")
    version: IntProperty(name="Version", default=latest_version)

    def init(self, context):
        super().init(context)
        self.inputs.new('NodeSocketColor',
                        "Color").default_value = (0, 0, 0, 0)
        mask_socket = self.inputs.new('NodeSocketFloat', "Mask")
        mask_socket.hide_value = True
        mask_socket.default_value = 1.0
        self.outputs.new('NodeSocketColor',
                         "Color").default_value = (0, 0, 0, 0)
        self.update_shader_node_tree(context)

    def copy(self, node):
        super().copy(node)
        self.shader_node_tree = node.shader_node_tree.copy()
        self.update_shader_node_tree(bpy.context)

    def free(self):
        super().free()
        if self.shader_node_tree:
            bpy.data.node_groups.remove(self.shader_node_tree)
            self.shader_node_tree = None

    def draw_layer_settings(self, context, layout):
        layout.prop(self, "opacity")
        layout.prop(self, "blend_mode", text="")

    def update_shader_node_tree(self, context):
        target_name = self._get_shader_node_tree_name()
        if not self.shader_node_tree:
            self.shader_node_tree = bpy.data.node_groups.new(
                target_name, 'ShaderNodeTree')
        elif self.shader_node_tree.name != target_name:
            self.shader_node_tree.name = target_name

        builder = NodeTreeBuilder(self.shader_node_tree)
        builder.add_socket('INPUT', 'NodeSocketColor', 'Color')
        builder.add_socket('INPUT', 'NodeSocketFloat', 'Alpha', subtype='FACTOR',
                           min_value=0.0, max_value=1.0, default_value=1.0)
        builder.add_socket('INPUT', 'NodeSocketFloat', 'Mask', hide_value=True,
                           min_value=0.0, max_value=1.0, default_value=1.0)
        builder.add_socket('OUTPUT', 'NodeSocketColor', 'Color')
        builder.add_socket('OUTPUT', 'NodeSocketFloat', 'Alpha', subtype='FACTOR',
                           min_value=0.0, max_value=1.0, default_value=1.0)
        builder.build()

        self.version = self.latest_version

    def _get_shader_node_tree_name(self):
        return f".PS {self.name} ({self.uuid[:4]})"


def register():
    bpy.utils.register_class(PaintSystemNodePanel)


def unregister():
    bpy.utils.unregister_class(PaintSystemNodePanel)
