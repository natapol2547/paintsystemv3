import bpy
from bpy.props import FloatProperty, EnumProperty, PointerProperty, IntProperty, StringProperty

from ..base_node import PaintSystemBaseNode, _set_name_transform
from ..builder import NodeTreeBuilder


BLEND_MODE_ITEMS = []
for blend_mode in bpy.types.ShaderNodeMixRGB.bl_rna.properties['blend_type'].enum_items:
    BLEND_MODE_ITEMS.append(
        (blend_mode.identifier, blend_mode.name, blend_mode.description))
    if blend_mode.identifier in ["MIX", "COLOR_BURN", "ADD", "LINEAR_LIGHT", "DIVIDE"]:
        BLEND_MODE_ITEMS.append(None)


def _update_group_node_tree(self, context):
    self.update_shader_node_tree(context)


class PaintSystemLayerNode(PaintSystemBaseNode):
    bl_width_default = 200
    latest_version = 1

    # Properties
    name: StringProperty(name="Name", default="Layer",
                         set_transform=_set_name_transform,
                         update=_update_group_node_tree)
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
        if not self.shader_node_tree:
            target_name = self._get_shader_node_tree_name()
            self.shader_node_tree = bpy.data.node_groups.new(
                target_name, 'ShaderNodeTree')

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
