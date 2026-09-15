import bpy
from bpy.types import Operator
from bpy.props import EnumProperty, StringProperty
from bpy.utils import register_classes_factory

from ..context import get_active_tree, parse_context
from ..compiler.core import compile_tree, flush_now, ensure_artifact, suspend_compile
from ..compiler.bake import create_managed_image
from ..nodetree.stack_ops import descendants


MATERIAL_GROUP_KEY = "ps_tree_uuid"

RESOLUTION_ITEMS = [
    ('1024', "1024", ""),
    ('2048', "2048", ""),
    ('4096', "4096", ""),
]


def _new_tree(name: str):
    tree = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    tree.initialize()
    return tree


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


class PAINTSYSTEM_OT_create_tree(Operator):
    bl_idname = "paint_system.create_tree"
    bl_label = "New Paint System Tree"
    bl_description = "Create a new Paint System node tree"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name="Name", default="Paint System")

    def execute(self, context):
        tree = _new_tree(self.name)
        context.scene.paint_system.active_node_tree = tree
        return {'FINISHED'}


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
            tree = _new_tree(mat.name)
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


class PAINTSYSTEM_OT_add_layer(Operator):
    bl_idname = "paint_system.add_layer"
    bl_label = "Add Layer"
    bl_description = ("Add a layer above the active layer, inside it when it is a folder, "
                      "or on top of the stack when no layer is active")
    bl_options = {'REGISTER', 'UNDO'}

    layer_type: EnumProperty(name="Type", items=[
        ('IMAGE', "Image", "Paintable image layer"),
        ('SOLID', "Solid Color", "Flat color layer"),
        ('FOLDER', "Folder", "Group layers and blend them as one"),
        ('GROUP', "Group", "Nested Paint System tree"),
    ], default='IMAGE')
    resolution: EnumProperty(name="Resolution", items=RESOLUTION_ITEMS, default='2048')

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        return tree is not None and len(tree.channels) > 0

    def execute(self, context):
        ps = parse_context(context)
        tree = ps.tree
        target = ps.layer if ps.stack_item is not None else None
        with suspend_compile(tree):
            if self.layer_type == 'IMAGE':
                node = tree.insert_layer_node('PaintSystemImageLayerNode', target=target)
                size = int(self.resolution)
                image = create_managed_image(f"{tree.name} {node.name}", size, size)
                node.image = image
                _set_paint_canvas(context, image)
            elif self.layer_type == 'SOLID':
                node = tree.insert_layer_node('PaintSystemSolidColorLayerNode', target=target)
            elif self.layer_type == 'FOLDER':
                tree.insert_layer_node('PaintSystemFolderLayerNode', target=target)
            else:
                node = tree.nodes.new('PaintSystemGroupLayerNode')
                node.node_tree = _new_tree(f"{tree.name} Group")
        return {'FINISHED'}

    def invoke(self, context, event):
        if self.layer_type == 'IMAGE':
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)


def _set_paint_canvas(context, image):
    settings = context.scene.tool_settings.image_paint
    settings.mode = 'IMAGE'
    settings.canvas = image


class PAINTSYSTEM_OT_set_active_layer(Operator):
    bl_idname = "paint_system.set_active_layer"
    bl_label = "Select Layer"
    bl_description = "Make this layer active and paint on its image"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: StringProperty()

    def execute(self, context):
        tree = get_active_tree(context)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None:
            return {'CANCELLED'}
        for n in tree.nodes:
            n.select = n == node
        tree.nodes.active = node
        image = getattr(node, 'image', None)
        if image is not None:
            _set_paint_canvas(context, image)
        return {'FINISHED'}


class PAINTSYSTEM_OT_remove_layer(Operator):
    bl_idname = "paint_system.remove_layer"
    bl_label = "Remove Layer"
    bl_description = "Remove the active layer, and a folder's content with it, and close the gap"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        node = tree.nodes.active if tree else None
        return node is not None and getattr(node, 'is_layer_node', False)

    def execute(self, context):
        ps = parse_context(context)
        tree, node = ps.tree, ps.layer
        # The row that moves up into the removed row stays active, or the
        # last row when the removed one was at the bottom.
        rows = [item.node.name for item in tree.stack()]
        gone = {node.name, *(child.name for child in descendants(node))} if node.is_folder else {node.name}
        position = rows.index(node.name) if node.name in rows else len(rows)
        after = [name for name in rows[position:] if name not in gone]
        before = [name for name in rows[:position] if name not in gone]
        next_active = after[0] if after else (before[-1] if before else None)

        tree.remove_layer_node(node)
        if next_active is not None:
            tree.nodes.active = tree.nodes[next_active]
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_create_tree,
    PAINTSYSTEM_OT_setup_material,
    PAINTSYSTEM_OT_compile_tree,
    PAINTSYSTEM_OT_add_layer,
    PAINTSYSTEM_OT_set_active_layer,
    PAINTSYSTEM_OT_remove_layer,
)


register, unregister = register_classes_factory(classes)
