import bpy
from bpy.types import Operator
from bpy.utils import register_classes_factory

from ..context import parse_context, update_active_image


class PAINTSYSTEM_OT_toggle_paint_mode(Operator):
    bl_idname = "paint_system.toggle_paint_mode"
    bl_label = "Toggle Paint Mode"
    bl_description = "Switch between texture painting on the active layer and object mode"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return parse_context(context).ps_object is not None

    def execute(self, context):
        obj = parse_context(context).ps_object
        context.view_layer.objects.active = obj
        obj.select_set(True)
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
            return {'FINISHED'}
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
        space = context.space_data
        if obj.mode == 'TEXTURE_PAINT' and space is not None and space.type == 'VIEW_3D':
            # Show the layers while painting. Cycles is too slow to paint
            # through, so it gets Material Preview, which renders with EEVEE.
            shading = 'MATERIAL' if context.scene.render.engine == 'CYCLES' else 'RENDERED'
            if space.shading.type != shading:
                space.shading.type = shading
        update_active_image(context)
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_toggle_paint_mode,
)


register, unregister = register_classes_factory(classes)
