from bpy.types import Node
from bpy.props import BoolProperty
from bpy.utils import register_classes_factory

from .base_layer_node import PaintSystemLayerNode, new_hidden_input
from ...common import blender_icon, icon_kwargs


class PaintSystemFolderLayerNode(PaintSystemLayerNode, Node):
    """A layer whose content is the stack linked into ``Content Color``/``Content Alpha``.

    The content composites over a transparent backdrop, and the result
    blends over the stack below with the folder's own opacity and blend
    mode, like any other layer.
    """
    bl_idname = 'PaintSystemFolderLayerNode'
    bl_label = 'Folder'
    bl_icon = blender_icon('FILE_FOLDER')
    header_color = (0.42, 0.285, 0.155)

    ps_type = 'FOLDER'
    ps_label = "Folder"
    ps_description = "Group layers and blend them as one"
    ps_icon = ('folder',)
    ps_menu_section = 'STRUCTURE'

    is_folder = True

    is_expanded: BoolProperty(name="Expanded", default=True,
                              description="Show the layers inside this folder")

    def init(self, context):
        super().init(context)
        new_hidden_input(self, 'NodeSocketColor', "Content Color", (0, 0, 0, 0))
        new_hidden_input(self, 'NodeSocketFloat', "Content Alpha", 0.0)

    def draw_row_icon(self, layout):
        icon = 'folder_open' if self.is_expanded else 'folder'
        layout.prop(self, "is_expanded", text="", emboss=False, icon_only=True, **icon_kwargs(icon))

    def emit_source(self, ctx):
        return (ctx.input_source(self.inputs['Content Color']),
                ctx.input_source(self.inputs['Content Alpha']))


classes = (
    PaintSystemFolderLayerNode,
)


register, unregister = register_classes_factory(classes)
