import bpy
from bpy.types import Operator
from bpy.utils import register_classes_factory

from ..nodes.node_tree_builder import NodeTreeBuilder


class PAINTSYSTEM_OT_create_tree(Operator):
    bl_idname = "paint_system.create_tree"
    bl_label = "New Paint System"
    bl_description = "Create a new Paint System node tree with a companion shader node group"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        bpy.data.node_groups.new(
            "Paint System", 'PaintSystemNodeTree')
        return {'FINISHED'}


class PAINTSYSTEM_OT_test_build_shader_tree(Operator):
    bl_idname = "paint_system.test_build_shader_tree"
    bl_label = "Test Build Shader Tree"
    bl_description = "Build a sample shader node tree via NodeTreeBuilder to verify the diff-based build pipeline"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.space_data
            and context.space_data.type == 'NODE_EDITOR'
            and getattr(context.space_data, 'edit_tree', None)
            and context.space_data.edit_tree.bl_idname == 'PaintSystemNodeTree'
        )

    def execute(self, context):
        ps_tree = context.space_data.edit_tree
        ps_tree.update_shader_node_tree()
        shader_tree = ps_tree.shader_node_tree

        builder = NodeTreeBuilder(shader_tree, version=2)

        # Nodes
        builder.add_node("principled", "ShaderNodeBsdfPrincipled")
        builder.add_node("rgb", "ShaderNodeRGB")
        builder.add_node("color_ramp", "ShaderNodeValToRGB")
        builder.add_node("mix", "ShaderNodeMix",
                         properties={"data_type": "RGBA"})

        # Socket values
        builder.set_node_input(
            "principled", "Base Color",
            default_value=[0.8, 0.1, 0.1, 1.0],
        )
        builder.set_node_output(
            "rgb", 0,
            default_value=[0.1, 0.5, 0.8, 1.0],
        )

        # Sub-object properties (color_ramp)
        builder.set_node_special("color_ramp", "color_ramp", {
            "color_mode": "RGB",
            "interpolation": "LINEAR",
            "elements": [
                {"position": 0.0, "color": [0.0, 0.0, 0.0, 1.0]},
                {"position": 0.5, "color": [0.5, 0.1, 0.1, 1.0]},
                {"position": 1.0, "color": [1.0, 1.0, 1.0, 1.0]},
            ],
        })

        # Links
        builder.link_nodes("rgb", "color_ramp",
                           from_socket=0, to_socket="Factor")
        builder.link_nodes("color_ramp", "mix", from_socket=0, to_socket=6)
        builder.link_nodes("mix", "principled",
                           from_socket=2, to_socket="Base Color")

        builder.build()

        self.report(
            {'INFO'},
            f"Built shader tree: {len(shader_tree.nodes)} nodes, "
            f"{len(shader_tree.links)} links",
        )
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_create_tree,
    PAINTSYSTEM_OT_test_build_shader_tree,
)


register, unregister = register_classes_factory(classes)
