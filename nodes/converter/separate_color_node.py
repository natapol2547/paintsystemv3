"""The Separate Color node of the Paint System tree (PS-098).

A link in the tree carries one RGBA value. This node splits one into
four floats, as the compositor's Separate Color does, in RGB, HSV or HSL
like the shader's. A float link carries its value with an alpha of 1,
which is how Blender turns a float into a colour. So an output can start
another channel's stack, feed a layer's ``Mask`` or feed a Group Output
socket as it is.

The node is not a layer, so its input is not a stack slot
(``stack_ops.is_slot``). A layer that feeds it stays in its own stack,
as a layer that feeds a mask does, and stack edits leave that link
alone. Under another stack, the node is where that stack starts, and
stack edits keep it at the bottom.
"""
from bpy.types import Node
from bpy.props import EnumProperty
from bpy.utils import register_classes_factory

from ..base_node import PaintSystemBaseNode
from ...compiler.core import suspend_compile

MODE_ITEMS = [
    ('RGB', "RGB", "Use RGB (Red, Green, Blue) color processing"),
    ('HSV', "HSV", "Use HSV (Hue, Saturation, Value) color processing"),
    ('HSL', "HSL", "Use HSL (Hue, Saturation, Lightness) color processing"),
]

# The names of the first three outputs in each mode. Their identifiers
# stay Red, Green and Blue, as on Blender's node, so a mode change keeps
# the links.
OUTPUT_NAMES = {
    'RGB': ("Red", "Green", "Blue"),
    'HSV': ("Hue", "Saturation", "Value"),
    'HSL': ("Hue", "Saturation", "Lightness"),
}


def _update_mode(self, context):
    # A socket rename is an edit of its own, so the renames and the new
    # mode compile once.
    with suspend_compile(self.id_data):
        for socket, name in zip(self.outputs, OUTPUT_NAMES[self.mode]):
            socket.name = name


class PaintSystemSeparateColorNode(PaintSystemBaseNode, Node):
    bl_idname = 'PaintSystemSeparateColorNode'
    bl_label = 'Separate Color'
    # Blender's default theme colour for converter nodes.
    header_color = (0.14, 0.38, 0.51)

    mode: EnumProperty(name="Mode", items=MODE_ITEMS, default='RGB', update=_update_mode)

    def init(self, context):
        super().init(context)
        self.inputs.new('NodeSocketColor', "Color").default_value = (0.8, 0.8, 0.8, 1.0)
        for name in (*OUTPUT_NAMES['RGB'], "Alpha"):
            self.outputs.new('NodeSocketFloat', name, identifier=name)

    def draw_buttons(self, context, layout):
        layout.prop(self, "mode", text="")

    def emit(self, ctx):
        color, alpha = ctx.rgba_input(self.inputs['Color'])
        separate = ctx.emit_node(self, 'separate', 'ShaderNodeSeparateColor', properties={'mode': self.mode})
        ctx.link_or_set(color, separate, 'Color')
        # One constant serves as the alpha of all four outputs.
        opaque = (ctx.emit_node(self, 'opaque', 'ShaderNodeValue', outputs={0: {'default_value': 1.0}}), 0)
        for socket in self.outputs[:3]:
            ctx.set_output(socket, (separate, socket.identifier), opaque)
        ctx.set_output(self.outputs['Alpha'], alpha, opaque)


classes = (
    PaintSystemSeparateColorNode,
)


register, unregister = register_classes_factory(classes)
