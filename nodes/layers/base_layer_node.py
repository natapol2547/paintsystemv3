import bpy
import uuid
from bpy.props import BoolProperty, FloatProperty, EnumProperty, PointerProperty, IntProperty, StringProperty

from ..base_node import PaintSystemBaseNode, _set_name_transform
from ..builder import NodeTreeBuilder
from ...common import ensure_shader_node_tree


BLEND_MODE_ITEMS = []
for blend_mode in bpy.types.ShaderNodeMixRGB.bl_rna.properties['blend_type'].enum_items:
    BLEND_MODE_ITEMS.append(
        (blend_mode.identifier, blend_mode.name, blend_mode.description))
    if blend_mode.identifier in ["MIX", "COLOR_BURN", "ADD", "LINEAR_LIGHT", "DIVIDE"]:
        BLEND_MODE_ITEMS.append(None)


def _update_shader_node_tree(self, context):
    self.update_shader_node_tree(context)


class PaintSystemLayerNode(PaintSystemBaseNode):
    bl_width_default = 200
    latest_version = 1

    # Properties
    name: StringProperty(name="Name", default="Layer",
                         set_transform=_set_name_transform,
                         update=_update_shader_node_tree)
    opacity: FloatProperty(name="Opacity", default=1.0,
                           min=0.0, max=1.0, subtype='FACTOR')
    blend_mode: EnumProperty(
        name="Blend Mode", items=BLEND_MODE_ITEMS, default='MIX')
    shader_node_tree: PointerProperty(
        type=bpy.types.NodeTree, name="Shader Node Tree", description="Shader Node Tree for this layer")
    version: IntProperty(name="Version", default=latest_version)
    suppress_update: BoolProperty(
        name="Suppress Update", default=False, options={'HIDDEN', 'SKIP_SAVE'})

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
        print(f"Copying layer node: {self.name}, {node.name}")
        self.suppress_update = True
        node.suppress_update = True
        print("Suppressed update during copy")
        node.shader_node_tree = None
        super().copy(node)
        print("Finished copying base node properties")
        self.suppress_update = False
        node.suppress_update = False

    def free(self):
        super().free()
        if self.shader_node_tree:
            bpy.data.node_groups.remove(self.shader_node_tree)
            self.shader_node_tree = None

    def draw_layer_settings(self, context, layout):
        layout.prop(self, "opacity")
        layout.prop(self, "blend_mode", text="")

    def update_shader_node_tree(self, context):
        print(f"{self} Suppress update: {self.suppress_update}")
        if self.suppress_update:
            return
        if not self.uuid:
            self.uuid = str(uuid.uuid4())
        self.shader_node_tree = ensure_shader_node_tree(
            self.shader_node_tree, self._get_shader_node_tree_name())
        if not self.shader_node_tree:
            # Ensure can return None
            return

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
