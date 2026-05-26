import bpy
from .common import PaintSystemPanel


class PAINTSYSTEM_PT_test(PaintSystemPanel):
    bl_label = "Test"
    bl_idname = "PAINTSYSTEM_PT_test"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Paint System"

    @classmethod
    def poll(cls, context):
        return (
            context.space_data
            and context.space_data.type == 'NODE_EDITOR'
            and getattr(context.space_data, 'edit_tree', None)
            and context.space_data.edit_tree.bl_idname == 'PaintSystemNodeTree'
        )

    def draw(self, context):
        layout = self.layout

        layout.label(text="Arrange tests (run top to bottom)")
        col = layout.column(align=True)
        col.operator(
            "paint_system.test_build_shader_tree",
            text="1. Build Base Chain",
            icon='NODETREE',
        )
        col.operator(
            "paint_system.test_insert_single_node",
            text="2. Insert Single Node (incremental)",
            icon='ADD',
        )
        col.operator(
            "paint_system.test_fanout_fanin",
            text="3. Fan-Out / Fan-In",
            icon='IMGDISPLAY',
        )
        col.operator(
            "paint_system.test_orphan_node",
            text="4. Add Orphan Node",
            icon='QUESTION',
        )
        col.operator(
            "paint_system.test_chain_insert",
            text="5. Chain Insert (regression)",
            icon='LINKED',
        )

        layout.separator()
        layout.label(text="Standalone checks")
        col2 = layout.column(align=True)
        col2.operator(
            "paint_system.test_build_no_arrange",
            text="Build Without Arrange (opt-out)",
            icon='X',
        )
        col2.operator(
            "paint_system.test_clear_shader_tree",
            text="Clear Shader Tree",
            icon='TRASH',
        )

        ps_tree = context.space_data.edit_tree
        shader_tree = ps_tree.shader_node_tree
        if shader_tree:
            box = layout.box()
            box.label(text=f"Shader Tree: {shader_tree.name}")
            box.label(text=f"Nodes: {len(shader_tree.nodes)}")
            box.label(text=f"Links: {len(shader_tree.links)}")
            for node in shader_tree.nodes:
                ident = getattr(node, 'ps_identifier', '') or node.name
                box.label(
                    text=f"  {ident}: ({node.location.x:.0f}, {node.location.y:.0f})",
                )


classes = (
    PAINTSYSTEM_PT_test,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
