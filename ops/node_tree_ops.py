import bpy
from bpy.types import Operator
from bpy.utils import register_classes_factory

from ..nodes.builder import Flexible, NodeTreeBuilder


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
        ps_tree.update_shader_node_tree(context)
        shader_tree = ps_tree.shader_node_tree

        builder = NodeTreeBuilder(shader_tree)

        # Nodes
        builder.add_node("principled", "ShaderNodeBsdfPrincipled")
        builder.add_node("rgb", "ShaderNodeRGB")
        builder.add_node("color_ramp", "ShaderNodeValToRGB")
        builder.add_node("mix", "ShaderNodeMix",
                         properties={"data_type": "RGBA"},
                         inputs={0: {"default_value": Flexible(0.1)}})

        # Socket values
        builder.set_node_input(
            "principled", "Base Color",
            default_value=[0.8, 0.1, 0.1, 1.0],
        )
        builder.set_node_output(
            "rgb", 0,
            default_value=Flexible([0.1, 0.5, 0.8, 1.0]),
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


def _poll_paint_system_node_editor(context):
    return (
        context.space_data
        and context.space_data.type == 'NODE_EDITOR'
        and getattr(context.space_data, 'edit_tree', None)
        and context.space_data.edit_tree.bl_idname == 'PaintSystemNodeTree'
    )


class PAINTSYSTEM_OT_test_clear_shader_tree(Operator):
    bl_idname = "paint_system.test_clear_shader_tree"
    bl_label = "Clear Shader Tree"
    bl_description = "Remove every node from the companion shader tree (resets state for arrange tests)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_paint_system_node_editor(context)

    def execute(self, context):
        ps_tree = context.space_data.edit_tree
        ps_tree.update_shader_node_tree(context)
        shader_tree = ps_tree.shader_node_tree
        for node in list(shader_tree.nodes):
            shader_tree.nodes.remove(node)
        self.report({'INFO'}, f"Cleared shader tree: {shader_tree.name}")
        return {'FINISHED'}


class PAINTSYSTEM_OT_test_insert_single_node(Operator):
    bl_idname = "paint_system.test_insert_single_node"
    bl_label = "Insert Single Node"
    bl_description = (
        "Build rgb -> invert -> color_ramp -> mix -> principled. Run AFTER the base build "
        "to verify 'invert' is incrementally slotted between rgb and color_ramp"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_paint_system_node_editor(context)

    def execute(self, context):
        ps_tree = context.space_data.edit_tree
        ps_tree.update_shader_node_tree(context)
        shader_tree = ps_tree.shader_node_tree

        builder = NodeTreeBuilder(shader_tree)
        builder.add_node("principled", "ShaderNodeBsdfPrincipled")
        builder.add_node("rgb", "ShaderNodeRGB")
        builder.add_node("invert", "ShaderNodeInvert")
        builder.add_node("color_ramp", "ShaderNodeValToRGB")
        builder.add_node("mix", "ShaderNodeMix", properties={"data_type": "RGBA"})

        builder.link_nodes("rgb", "invert", from_socket=0, to_socket="Color")
        builder.link_nodes("invert", "color_ramp",
                           from_socket="Color", to_socket="Factor")
        builder.link_nodes("color_ramp", "mix", from_socket=0, to_socket=6)
        builder.link_nodes("mix", "principled",
                           from_socket=2, to_socket="Base Color")

        builder.build()
        self.report({'INFO'}, f"Insert test: {len(shader_tree.nodes)} nodes")
        return {'FINISHED'}


class PAINTSYSTEM_OT_test_fanout_fanin(Operator):
    bl_idname = "paint_system.test_fanout_fanin"
    bl_label = "Fan-Out / Fan-In"
    bl_description = (
        "Add hue_sat and gamma between mix and principled. Both new nodes share "
        "(up=mix, down=principled) and should land in one column, stacked vertically"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_paint_system_node_editor(context)

    def execute(self, context):
        ps_tree = context.space_data.edit_tree
        ps_tree.update_shader_node_tree(context)
        shader_tree = ps_tree.shader_node_tree

        builder = NodeTreeBuilder(shader_tree)
        builder.add_node("principled", "ShaderNodeBsdfPrincipled")
        builder.add_node("rgb", "ShaderNodeRGB")
        builder.add_node("color_ramp", "ShaderNodeValToRGB")
        builder.add_node("mix", "ShaderNodeMix", properties={"data_type": "RGBA"})
        builder.add_node("hue_sat", "ShaderNodeHueSaturation")
        builder.add_node("gamma", "ShaderNodeGamma")

        builder.link_nodes("rgb", "color_ramp",
                           from_socket=0, to_socket="Factor")
        builder.link_nodes("color_ramp", "mix", from_socket=0, to_socket=6)
        builder.link_nodes("mix", "hue_sat",
                           from_socket=2, to_socket="Color")
        builder.link_nodes("mix", "gamma",
                           from_socket=2, to_socket="Color")
        builder.link_nodes("hue_sat", "principled",
                           from_socket=0, to_socket="Base Color")
        builder.link_nodes("gamma", "principled",
                           from_socket=0, to_socket="Emission Color")

        builder.build()
        self.report({'INFO'}, f"Fan-out test: {len(shader_tree.nodes)} nodes")
        return {'FINISHED'}


class PAINTSYSTEM_OT_test_build_no_arrange(Operator):
    bl_idname = "paint_system.test_build_no_arrange"
    bl_label = "Build Without Arrange"
    bl_description = (
        "Build the base chain with arrange=False — all nodes should pile up at (0,0). "
        "Verifies the opt-out path"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_paint_system_node_editor(context)

    def execute(self, context):
        ps_tree = context.space_data.edit_tree
        ps_tree.update_shader_node_tree(context)
        shader_tree = ps_tree.shader_node_tree

        builder = NodeTreeBuilder(shader_tree)
        builder.add_node("principled", "ShaderNodeBsdfPrincipled")
        builder.add_node("rgb", "ShaderNodeRGB")
        builder.add_node("color_ramp", "ShaderNodeValToRGB")
        builder.add_node("mix", "ShaderNodeMix", properties={"data_type": "RGBA"})

        builder.link_nodes("rgb", "color_ramp",
                           from_socket=0, to_socket="Factor")
        builder.link_nodes("color_ramp", "mix", from_socket=0, to_socket=6)
        builder.link_nodes("mix", "principled",
                           from_socket=2, to_socket="Base Color")

        builder.build(arrange=False)
        self.report({'INFO'}, "Built with arrange=False")
        return {'FINISHED'}


class PAINTSYSTEM_OT_test_chain_insert(Operator):
    bl_idname = "paint_system.test_chain_insert"
    bl_label = "Chain Insert (regression)"
    bl_description = (
        "Regression test for the chain bug: starts from a tree containing only "
        "'principled', then builds rgb -> mix -> principled so rgb AND mix are both "
        "new with the same (None, principled) anchor pair. They should land in "
        "SEPARATE columns left of principled, not piled in one column"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_paint_system_node_editor(context)

    def execute(self, context):
        ps_tree = context.space_data.edit_tree
        ps_tree.update_shader_node_tree(context)
        shader_tree = ps_tree.shader_node_tree

        # Step 1: reset to a tree containing only `principled`, arranged.
        for node in list(shader_tree.nodes):
            shader_tree.nodes.remove(node)
        b0 = NodeTreeBuilder(shader_tree)
        b0.add_node("principled", "ShaderNodeBsdfPrincipled")
        b0.build()

        # Step 2: rebuild with rgb -> mix -> principled. Both rgb and mix are
        # new; their (up_anchor, down_anchor) is (None, principled). The bug
        # piled them at the same x.
        b1 = NodeTreeBuilder(shader_tree)
        b1.add_node("principled", "ShaderNodeBsdfPrincipled")
        b1.add_node("rgb", "ShaderNodeRGB")
        b1.add_node("mix", "ShaderNodeMix", properties={"data_type": "RGBA"})

        b1.link_nodes("rgb", "mix", from_socket=0, to_socket=6)
        b1.link_nodes("mix", "principled",
                      from_socket=2, to_socket="Base Color")

        b1.build()
        self.report({'INFO'}, f"Chain insert test: {len(shader_tree.nodes)} nodes")
        return {'FINISHED'}


class PAINTSYSTEM_OT_test_orphan_node(Operator):
    bl_idname = "paint_system.test_orphan_node"
    bl_label = "Add Orphan Node"
    bl_description = (
        "Add an unlinked ShaderNodeValue to the existing tree. It should land in an "
        "orphan column to the right of everything else, not piled at (0,0)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_paint_system_node_editor(context)

    def execute(self, context):
        ps_tree = context.space_data.edit_tree
        ps_tree.update_shader_node_tree(context)
        shader_tree = ps_tree.shader_node_tree

        builder = NodeTreeBuilder(shader_tree)
        # Re-declare existing chain so the builder keeps it; add orphan on top.
        builder.add_node("principled", "ShaderNodeBsdfPrincipled")
        builder.add_node("rgb", "ShaderNodeRGB")
        builder.add_node("color_ramp", "ShaderNodeValToRGB")
        builder.add_node("mix", "ShaderNodeMix", properties={"data_type": "RGBA"})
        builder.add_node("orphan_value", "ShaderNodeValue")

        builder.link_nodes("rgb", "color_ramp",
                           from_socket=0, to_socket="Factor")
        builder.link_nodes("color_ramp", "mix", from_socket=0, to_socket=6)
        builder.link_nodes("mix", "principled",
                           from_socket=2, to_socket="Base Color")
        # orphan_value has no links — should land in the orphan column

        builder.build()
        self.report({'INFO'}, f"Orphan test: {len(shader_tree.nodes)} nodes")
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_create_tree,
    PAINTSYSTEM_OT_test_build_shader_tree,
    PAINTSYSTEM_OT_test_insert_single_node,
    PAINTSYSTEM_OT_test_fanout_fanin,
    PAINTSYSTEM_OT_test_chain_insert,
    PAINTSYSTEM_OT_test_orphan_node,
    PAINTSYSTEM_OT_test_build_no_arrange,
    PAINTSYSTEM_OT_test_clear_shader_tree,
)


register, unregister = register_classes_factory(classes)
