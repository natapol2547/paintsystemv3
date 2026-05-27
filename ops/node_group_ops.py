import uuid

import bpy
from bpy.types import Operator
from bpy.props import StringProperty
from bpy.utils import register_classes_factory

from ..nodetree.tree import PaintSystemNodeTree
from .channel_ops import _get_active_tree


_GROUP_NODE_ID = 'PaintSystemGroupLayerNode'
_GROUP_IO_IDS = ('PaintSystemGroupInputNode', 'PaintSystemGroupOutputNode')


def _socket_type_to_channel_type(bl_idname: str) -> str:
    return {
        'NodeSocketColor': 'COLOR',
        'NodeSocketFloat': 'FLOAT',
        'NodeSocketVector': 'VECTOR',
    }.get(bl_idname, 'COLOR')


def _target_node(context):
    """The group node a button/keymap acted on (draw_buttons sets context.node)."""
    node = getattr(context, 'node', None)
    if node is None:
        node = getattr(context, 'active_node', None)
    return node


def _in_node_editor(context) -> bool:
    space = context.space_data
    return (
        space is not None
        and space.type == 'NODE_EDITOR'
        and getattr(space, 'edit_tree', None) is not None
        and space.edit_tree.bl_idname == 'PaintSystemNodeTree'
    )


def _enter_group(context, node) -> bool:
    if not node or not getattr(node, 'node_tree', None):
        return False
    context.space_data.path.append(node.node_tree, node=node)
    node.node_tree.group_node_name = node.name
    return True


class PAINTSYSTEM_OT_edit_node_group(Operator):
    bl_idname = "paint_system.edit_node_group"
    bl_label = "Edit Node Group"
    bl_description = "Open the wrapped tree for editing"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _in_node_editor(context)

    def execute(self, context):
        node = _target_node(context)
        if not _enter_group(context, node):
            self.report({'WARNING'}, "No group tree to edit")
            return {'CANCELLED'}
        return {'FINISHED'}


class PAINTSYSTEM_OT_exit_node_group(Operator):
    bl_idname = "paint_system.exit_node_group"
    bl_label = "Exit Node Group"
    bl_description = "Step back out of the current group tree"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return (space is not None and space.type == 'NODE_EDITOR'
                and len(space.path) > 1)

    def execute(self, context):
        context.space_data.path.pop()
        return {'FINISHED'}


class PAINTSYSTEM_OT_enter_exit_node_group(Operator):
    bl_idname = "paint_system.enter_exit_node_group"
    bl_label = "Enter/Exit Node Group"
    bl_description = "Enter the active group node, or step out if none is active (TAB)"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _in_node_editor(context)

    def execute(self, context):
        node = getattr(context, 'active_node', None)
        if node and node.bl_idname == _GROUP_NODE_ID and node.node_tree:
            _enter_group(context, node)
        elif len(context.space_data.path) > 1:
            context.space_data.path.pop()
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_edit_node_group,
    PAINTSYSTEM_OT_exit_node_group,
    PAINTSYSTEM_OT_enter_exit_node_group
)


register, unregister = register_classes_factory(classes)
