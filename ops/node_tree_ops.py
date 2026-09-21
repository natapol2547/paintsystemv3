import bpy
from bpy.types import Operator
from bpy.utils import register_classes_factory

from ..context import get_active_tree
from ..compiler.core import compile_tree, flush_now, ensure_artifact


MATERIAL_GROUP_KEY = "ps_tree_uuid"

RESOLUTION_ITEMS = [
    ('1024', "1024", ""),
    ('2048', "2048", ""),
    ('4096', "4096", ""),
]


def _find_material_group_node(material, tree):
    for node in material.node_tree.nodes:
        if node.bl_idname == 'ShaderNodeGroup' and node.get(MATERIAL_GROUP_KEY) == tree.uuid:
            return node
    return None


def link_tree_to_material(material, tree):
    """Instance the compiled group in *material* and feed Base Color if free."""
    material.use_nodes = True
    material.paint_system.tree = tree
    compile_tree(tree)
    nt = material.node_tree
    bsdf = next((n for n in nt.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled'), None)
    group = _find_material_group_node(material, tree)
    if group is None:
        group = nt.nodes.new('ShaderNodeGroup')
        group[MATERIAL_GROUP_KEY] = tree.uuid
        group.label = tree.name
        if bsdf is not None:
            group.location = (bsdf.location.x - 300, bsdf.location.y)
    group.node_tree = ensure_artifact(tree)
    if bsdf is not None and len(tree.channels):
        base = bsdf.inputs.get('Base Color')
        first = tree.channels[0].name
        if base is not None and not base.is_linked and first in group.outputs:
            nt.links.new(group.outputs[first], base)
    return group


class PAINTSYSTEM_OT_setup_material(Operator):
    bl_idname = "paint_system.setup_material"
    bl_label = "Setup Paint System"
    bl_description = "Create a Paint System tree for the active material and wire its compiled group into the material"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.object
        mat = obj.active_material
        if mat is None:
            mat = bpy.data.materials.new(f"{obj.name} Material")
            mat.use_nodes = True
            if len(obj.material_slots) == 0:
                obj.data.materials.append(mat)
            else:
                obj.material_slots[obj.active_material_index].material = mat
        tree = mat.paint_system.tree
        if tree is None:
            tree = bpy.data.node_groups.new(mat.name, 'PaintSystemNodeTree')
            tree.initialize()
        link_tree_to_material(mat, tree)
        context.scene.paint_system.active_node_tree = tree
        return {'FINISHED'}


class PAINTSYSTEM_OT_compile_tree(Operator):
    bl_idname = "paint_system.compile_tree"
    bl_label = "Recompile"
    bl_description = "Force a full rebuild of the compiled shader group"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return get_active_tree(context) is not None

    def execute(self, context):
        tree = get_active_tree(context)
        fingerprint = compile_tree(tree, force=True)
        flush_now()
        self.report({'INFO'}, f"Compiled {tree.compiled.name} ({fingerprint[:8]})")
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_setup_material,
    PAINTSYSTEM_OT_compile_tree,
)


register, unregister = register_classes_factory(classes)
