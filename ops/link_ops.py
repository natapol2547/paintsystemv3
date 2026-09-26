"""Link and unlink layers (PS-016). See ``nodes/layers/links.py``."""
from bpy.types import Operator
from bpy.props import EnumProperty
from bpy.utils import register_classes_factory

from ..common import icon_kwargs
from ..context import button_layer, get_active_tree, node_editor_tree, update_active_image
from ..nodes.layers import links
from ..nodetree.stack_ops import is_layer

# The items ``_target_items`` last returned. Blender keeps no reference
# to a dynamic enum's items, so their strings must stay alive here.
_target_item_list = []


def _target_items(self, context):
    global _target_item_list
    layer = button_layer(context, get_active_tree(context))
    candidates = links.link_candidates(layer) if layer is not None else []
    _target_item_list = [(node.name, node.name, "") for node in candidates]
    return _target_item_list


def draw_lost_images(layout, lost) -> None:
    """Warn that linking takes the images in *lost*, from ``links.lost_images``, out of use."""
    if not lost:
        return
    col = layout.column(align=True)
    col.label(text="No layer uses these images once linked:", **icon_kwargs('ERROR'))
    for _node, image in lost:
        col.label(text=image.name, **icon_kwargs('IMAGE_DATA'))
    col.label(text="Unless something else uses them, they are not saved with the file.")


class PAINTSYSTEM_OT_link_selected_layers(Operator):
    bl_idname = "paint_system.link_selected_layers"
    bl_label = "Link Selected Layers"
    bl_description = ("Link the selected layers with the active one. They take its settings, "
                      "and a change to one linked layer changes them all")
    bl_options = {'REGISTER', 'UNDO'}

    @staticmethod
    def targets(context):
        """The active layer, and the selected layers that can take its settings."""
        tree = node_editor_tree(context)
        active = tree.nodes.active if tree is not None else None
        if not is_layer(active):
            return None, []
        return active, [node for node in links.link_candidates(active) if node.select and not node.lock_layer]

    @classmethod
    def poll(cls, context):
        active, targets = cls.targets(context)
        if active is None:
            return False
        if not targets:
            cls.poll_message_set("Select unlocked layers of the active layer's type to link with it")
            return False
        return True

    def invoke(self, context, event):
        active, targets = self.targets(context)
        # The dialog draws on every redraw, so find the images once here.
        self.lost = links.lost_images(targets, active)
        if not self.lost:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, confirm_text="Link")

    def draw(self, context):
        draw_lost_images(self.layout, self.lost)

    def execute(self, context):
        active, targets = self.targets(context)
        linked = links.linked_layers(active)
        skipped = [node for node in active.id_data.nodes
                   if node.select and is_layer(node) and node != active
                   and node not in targets and node not in linked]
        links.link(targets, active)
        if skipped:
            self.report({'WARNING'}, f"{len(skipped)} selected layers were left unlinked: "
                        "only unlocked layers of the active layer's type can link with it")
        update_active_image(context)
        return {'FINISHED'}


class PAINTSYSTEM_OT_link_layer(Operator):
    bl_idname = "paint_system.link_layer"
    bl_label = "Link With"
    bl_description = ("Link the active layer with another layer of its type. It takes that layer's settings, "
                      "and a change to one linked layer changes them all")
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(name="Layer", description="The layer to link with", items=_target_items)

    @classmethod
    def poll(cls, context):
        layer = button_layer(context, get_active_tree(context))
        if layer is None:
            return False
        if layer.lock_layer:
            cls.poll_message_set(f"Layer '{layer.name}' is locked")
            return False
        if not links.link_candidates(layer):
            cls.poll_message_set("No other layer of this type to link with")
            return False
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, confirm_text="Link")

    def draw(self, context):
        layout = self.layout
        layer = button_layer(context, get_active_tree(context))
        layout.prop(self, "target")
        target = layer.id_data.nodes.get(self.target)
        if target is not None:
            draw_lost_images(layout, links.lost_images([layer], target))

    def execute(self, context):
        layer = button_layer(context, get_active_tree(context))
        target = layer.id_data.nodes.get(self.target)
        if target is None or target not in links.link_candidates(layer):
            self.report({'ERROR'}, f"No layer named '{self.target}' can link with '{layer.name}'")
            return {'CANCELLED'}
        links.link([layer], target)
        update_active_image(context)
        return {'FINISHED'}


class PAINTSYSTEM_OT_unlink_layer(Operator):
    bl_idname = "paint_system.unlink_layer"
    bl_label = "Unlink Layer"
    bl_description = ("Stop the active layer sharing its settings with the layers it is linked with. "
                      "It keeps its settings and gets its own copy of their images")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        layer = button_layer(context, get_active_tree(context))
        return layer is not None and bool(links.linked_layers(layer))

    def execute(self, context):
        links.unlink(button_layer(context, get_active_tree(context)))
        update_active_image(context)
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_link_selected_layers,
    PAINTSYSTEM_OT_link_layer,
    PAINTSYSTEM_OT_unlink_layer,
)


register, unregister = register_classes_factory(classes)
