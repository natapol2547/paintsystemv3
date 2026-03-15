import bpy
import uuid

from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Node, PropertyGroup


BLEND_MODE_ITEMS = []
for blend_mode in bpy.types.ShaderNodeMixRGB.bl_rna.properties['blend_type'].enum_items:
    BLEND_MODE_ITEMS.append(
        (blend_mode.identifier, blend_mode.name, blend_mode.description))
    if blend_mode.identifier in ["MIX", "COLOR_BURN", "ADD", "LINEAR_LIGHT", "DIVIDE"]:
        BLEND_MODE_ITEMS.append(None)


class PaintSystemNodePanel(PropertyGroup):
    idname: StringProperty(name="ID Name", default="")
    label: StringProperty(name="Label", default="")
    expanded: BoolProperty(name="Expanded", default=True)


class PaintSystemBaseNode(Node):
    panels: CollectionProperty(type=PaintSystemNodePanel)

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == 'PaintSystemNodeTree'

    def create_panel(self, idname, label, default_closed=True):
        panel = self.panels.add()
        panel.idname = idname
        panel.expanded = default_closed
        panel.label = label
        return panel

    def draw_panel(self, layout, idname):
        panel_data = next(
            (panel for panel in self.panels if panel.idname == idname), None)
        if not panel_data:
            raise ValueError(f"Panel with idname {idname} not found")
        panel = None
        col = layout.column()
        header = col.row(align=True)
        header.alignment = 'LEFT'
        header.prop(panel_data, "expanded", text="", emboss=False,
                    icon='DOWNARROW_HLT' if panel_data.expanded else 'RIGHTARROW')
        header.prop(panel_data, "label", text="", emboss=False)
        if panel_data.expanded:
            panel = col
        return header, panel


class PaintSystemBaseLayerNode(PaintSystemBaseNode):
    bl_width_default = 200

    opacity: FloatProperty(name="Opacity", default=1.0,
                           min=0.0, max=1.0, subtype='FACTOR')
    blend_mode: EnumProperty(
        name="Blend Mode", items=BLEND_MODE_ITEMS, default='MIX')

    def init(self, context):
        self.inputs.new('NodeSocketColor',
                        "Color").default_value = (0, 0, 0, 0)
        mask_socket = self.inputs.new('NodeSocketFloat', "Mask")
        mask_socket.hide_value = True
        mask_socket.default_value = 1.0
        self.outputs.new('NodeSocketColor',
                         "Color").default_value = (0, 0, 0, 0)

    def draw_layer_settings(self, context, layout):
        layout.prop(self, "opacity")
        layout.prop(self, "blend_mode", text="")


def register():
    bpy.utils.register_class(PaintSystemNodePanel)


def unregister():
    bpy.utils.unregister_class(PaintSystemNodePanel)
