import bpy
from bpy.types import Operator
from bpy.utils import register_classes_factory


class PAINTSYSTEM_OT_create_tree(Operator):
    bl_idname = "paint_system.create_tree"
    bl_label = "New Paint System"
    bl_description = "Create a new Paint System node tree with a companion shader node group"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        ps_tree = bpy.data.node_groups.new(
            "Paint System", 'PaintSystemNodeTree')

        shader_group = bpy.data.node_groups.new(
            "PS_" + ps_tree.name, 'ShaderNodeTree')
        ps_tree.shader_node_group = shader_group

        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_create_tree,
)


register, unregister = register_classes_factory(classes)
