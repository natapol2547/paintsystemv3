import bpy
from bpy.types import Operator
from bpy.utils import register_classes_factory

from ..compiler.core import ps_trees
from ..context import (PREVIEW_RESTORE_KEY, PREVIEW_TREE_KEY, find_material_group_node, get_active_tree,
                       get_ps_object, update_active_image)
from ..props.channel import PREVIEW_OUTPUT
from ..props.preview import restore_preview_displays


def _material_shading(context) -> str:
    """The 3D view shading that shows the material.

    Cycles is too slow to paint through, so it gets Material Preview, which
    renders with EEVEE.
    """
    return 'MATERIAL' if context.scene.render.engine == 'CYCLES' else 'RENDERED'


class PAINTSYSTEM_OT_toggle_paint_mode(Operator):
    bl_idname = "paint_system.toggle_paint_mode"
    bl_label = "Toggle Paint Mode"
    bl_description = "Switch between texture painting on the active layer and object mode"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_ps_object(getattr(context, 'object', None)) is not None

    def execute(self, context):
        obj = get_ps_object(context.object)
        context.view_layer.objects.active = obj
        obj.select_set(True)
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
            return {'FINISHED'}
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
        space = context.space_data
        if obj.mode == 'TEXTURE_PAINT' and space is not None and space.type == 'VIEW_3D':
            # Show the layers while painting.
            shading = _material_shading(context)
            if space.shading.type != shading:
                space.shading.type = shading
        update_active_image(context)
        return {'FINISHED'}


def add_preview_outputs(tree) -> None:
    """Show *tree*'s Preview output in every material that runs the tree.

    Each material gets a Material Output of its own for it, which becomes
    the active output. The material's own nodes and links stay as they are.
    """
    for material in bpy.data.materials:
        group = find_material_group_node(material, tree)
        # A group node can run another artifact that has no Preview output,
        # such as the one of an appended copy of the material.
        if group is None or PREVIEW_OUTPUT not in group.outputs:
            continue
        nt = material.node_tree
        previous = next((node for node in nt.nodes
                         if node.bl_idname == 'ShaderNodeOutputMaterial' and node.is_active_output), None)
        output = nt.nodes.new('ShaderNodeOutputMaterial')
        output[PREVIEW_TREE_KEY] = tree.uuid
        if previous is not None:
            output[PREVIEW_RESTORE_KEY] = previous.name
        output.label = "Preview Channel"
        # A location inside a frame is relative to the frame, so the output
        # joins the group's frame to land next to the group.
        output.parent = group.parent
        output.location = (group.location.x + 250, group.location.y + 250)
        # Blender deactivates the material's other outputs.
        output.is_active_output = True
        nt.links.new(group.outputs[PREVIEW_OUTPUT], output.inputs['Surface'])


def remove_preview_outputs(tree) -> None:
    """Remove the outputs ``add_preview_outputs`` gave the materials for *tree*.

    Blender then activates a material's first output, which need not be the
    one that was active before, so that one is activated again by name. A
    preview output that is no longer active leaves the active one alone.
    """
    for material in bpy.data.materials:
        nt = material.node_tree
        if nt is None:
            continue
        for node in [node for node in nt.nodes if node.get(PREVIEW_TREE_KEY) == tree.uuid]:
            restore = node.get(PREVIEW_RESTORE_KEY, "")
            # A preview started after this one recorded this output as the
            # one to activate again. It gets this output's record instead,
            # so the material's own output still comes back at the end.
            for later in nt.nodes:
                if later.get(PREVIEW_RESTORE_KEY) == node.name:
                    later[PREVIEW_RESTORE_KEY] = restore
            previous = nt.nodes.get(restore) if node.is_active_output else None
            nt.nodes.remove(node)
            if previous is not None and previous.bl_idname == 'ShaderNodeOutputMaterial':
                previous.is_active_output = True


def _other_preview_running() -> bool:
    """Whether a tree still previews in some material.

    The flag alone is not enough: a copy of a previewing tree has the flag
    but no preview output, and must not keep the scene's display from
    coming back.
    """
    shown = {node.get(PREVIEW_TREE_KEY) for material in bpy.data.materials if material.node_tree
             for node in material.node_tree.nodes}
    return any(tree.preview_channel and tree.uuid in shown for tree in ps_trees())


class PAINTSYSTEM_OT_preview_channel(Operator):
    bl_idname = "paint_system.preview_channel"
    bl_label = "Preview Channel"
    bl_description = "Show the active channel's values on the object, or show the material again"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        if tree is None:
            return False
        # A linked tree and its materials are read again from the library
        # when the file opens, so the preview would not last.
        if not tree.is_editable:
            cls.poll_message_set("A linked tree cannot be previewed")
            return False
        if tree.preview_channel:
            return True
        if tree.active_channel is None:
            cls.poll_message_set("The tree has no channel to preview")
            return False
        obj = get_ps_object(getattr(context, 'object', None))
        if find_material_group_node(obj.active_material if obj is not None else None, tree) is None:
            cls.poll_message_set("The active material does not use this tree")
            return False
        return True

    def execute(self, context):
        tree = get_active_tree(context)
        # Stopping removes the outputs. Starting first removes any left
        # over, such as one in a material appended from a file saved while
        # previewing.
        remove_preview_outputs(tree)
        if tree.preview_channel:
            tree.preview_channel = False
            if not _other_preview_running():
                restore_preview_displays()
            return {'FINISHED'}
        # This compiles the Preview output that the materials link to.
        tree.preview_channel = True
        add_preview_outputs(tree)
        context.scene.paint_system.preview_display.show(context.scene.view_settings, tree.active_channel)
        space = context.space_data
        if space is not None and space.type == 'VIEW_3D' and space.shading.type in {'SOLID', 'WIREFRAME'}:
            space.shading.type = _material_shading(context)
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_toggle_paint_mode,
    PAINTSYSTEM_OT_preview_channel,
)


register, unregister = register_classes_factory(classes)
