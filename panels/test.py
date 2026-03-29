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

        layout.label(text="Node Tree Builder")
        col = layout.column(align=True)
        col.operator(
            "paint_system.test_build_shader_tree",
            text="Build Test Shader Tree",
            icon='NODETREE',
        )

        ps_tree = context.space_data.edit_tree
        shader_tree = ps_tree.shader_node_tree
        if shader_tree:
            box = layout.box()
            box.label(text=f"Shader Tree: {shader_tree.name}")
            box.label(text=f"Nodes: {len(shader_tree.nodes)}")
            box.label(text=f"Links: {len(shader_tree.links)}")


classes = (
    PAINTSYSTEM_PT_test,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
