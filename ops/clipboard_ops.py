"""Copy and paste layers from the layer list (PS-017). See ``nodes/layers/clipboard.py``."""
from bpy.types import Operator
from bpy.utils import register_classes_factory

from ..context import get_active_tree, parse_context, update_active_image
from ..nodes.layers import clipboard
from ..nodetree.stack_ops import is_layer


class PAINTSYSTEM_OT_copy_layer(Operator):
    bl_idname = "paint_system.copy_layer"
    bl_label = "Copy Layer"
    bl_description = "Copy the active layer, and a folder's content with it"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        return tree is not None and is_layer(tree.nodes.active)

    def execute(self, context):
        node = get_active_tree(context).nodes.active
        clipboard.copy_layers([node])
        self.report({'INFO'}, f"Copied '{node.name}'")
        return {'FINISHED'}


class PAINTSYSTEM_OT_copy_all_layers(Operator):
    bl_idname = "paint_system.copy_all_layers"
    bl_label = "Copy All Layers"
    bl_description = "Copy every layer of the active channel"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        return tree is not None and bool(tree.stack())

    def execute(self, context):
        # Folder content is copied with its folder at paste time.
        nodes = [item.node for item in get_active_tree(context).stack() if item.parent is None]
        clipboard.copy_layers(nodes)
        self.report({'INFO'}, f"Copied {len(nodes)} layers" if len(nodes) != 1 else f"Copied '{nodes[0].name}'")
        return {'FINISHED'}


class PasteLayersOperator:
    """Paste the copied layers above the active layer, as Add Layer places a new one."""
    bl_options = {'REGISTER', 'UNDO'}
    linked = False

    @classmethod
    def poll(cls, context):
        tree = get_active_tree(context)
        if tree is None or tree.active_channel is None:
            return False
        if not clipboard.copied_layers():
            cls.poll_message_set("Nothing is copied, or the copied layers were removed")
            return False
        return True

    def execute(self, context):
        ps = parse_context(context)
        target = ps.layer if ps.stack_item is not None else None
        clipboard.paste_layers(ps.tree, target, linked=self.linked)
        update_active_image(context)
        return {'FINISHED'}


class PAINTSYSTEM_OT_paste_layer(PasteLayersOperator, Operator):
    bl_idname = "paint_system.paste_layer"
    bl_label = "Paste Layers"
    bl_description = ("Paste copies of the copied layers above the active layer. "
                      "Each copy gets its own images, so painting on it leaves the original alone")


class PAINTSYSTEM_OT_paste_linked_layer(PasteLayersOperator, Operator):
    bl_idname = "paint_system.paste_linked_layer"
    bl_label = "Paste Linked Layers"
    bl_description = ("Paste the copied layers above the active layer, linked with the layers they copy. "
                      "Linked layers share their settings and images")
    linked = True

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False
        if not clipboard.can_paste_linked(get_active_tree(context)):
            cls.poll_message_set("Linked layers must be in one tree. Paste the layers unlinked instead")
            return False
        return True


classes = (
    PAINTSYSTEM_OT_copy_layer,
    PAINTSYSTEM_OT_copy_all_layers,
    PAINTSYSTEM_OT_paste_layer,
    PAINTSYSTEM_OT_paste_linked_layer,
)


register, unregister = register_classes_factory(classes)
