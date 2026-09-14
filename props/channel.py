import uuid

import bpy
from bpy.props import StringProperty, EnumProperty

from ..common import transform_unique_name, unique_name_kwargs, ensure_unique_name, is_newer_than


CHANNEL_SOCKET_TYPES = [
    ('COLOR', "Color", "Color (RGBA) channel"),
    ('FLOAT', "Float", "Float (scalar) channel"),
    ('VECTOR', "Vector", "Vector (XYZ) channel"),
]

_CHANNEL_TYPE_TO_SOCKET = {
    'COLOR': 'NodeSocketColor',
    'FLOAT': 'NodeSocketFloat',
    'VECTOR': 'NodeSocketVector',
}


def channel_socket_type(channel_type: str) -> str:
    return _CHANNEL_TYPE_TO_SOCKET.get(channel_type, 'NodeSocketColor')


def channel_alpha_name(channel_name: str) -> str:
    return f"{channel_name} Alpha"


def channel_socket_specs(channels) -> list[tuple[str, str, dict]]:
    """Socket layout implied by *channels*: (name, socket bl_idname, properties).

    Every channel contributes a value socket and an alpha socket. The same
    layout is used for the custom Group Input/Output nodes, for
    PaintSystemGroupLayerNode sockets, and for the compiled tree interface.
    """
    specs: list[tuple[str, str, dict]] = []
    for ch in channels:
        socket_type = channel_socket_type(ch.type)
        props = {'hide_value': True}
        if ch.type == 'COLOR':
            props['default_value'] = (0.0, 0.0, 0.0, 0.0)
        elif ch.type == 'FLOAT':
            props['default_value'] = 0.0
        specs.append((ch.name, socket_type, props))
        specs.append((channel_alpha_name(ch.name), 'NodeSocketFloat',
                      {'hide_value': True, 'default_value': 0.0}))
    return specs


def _set_name_transform(self, new_value, curr_value, is_set):
    return transform_unique_name(self, self.id_data, 'channels', new_value, curr_value, is_set)


def _on_channel_changed(self, context):
    tree = self.id_data
    if tree is None or tree.bl_idname != 'PaintSystemNodeTree':
        return
    if not is_newer_than(5, 0) and ensure_unique_name(self, tree, 'channels'):
        return  # the rename re-entered this callback and already refreshed
    tree.on_channels_changed()


class PaintSystemChannel(bpy.types.PropertyGroup):
    name: StringProperty(
        name="Name",
        default="Channel",
        update=_on_channel_changed,
        **unique_name_kwargs(_set_name_transform),
    )
    type: EnumProperty(
        name="Type",
        items=CHANNEL_SOCKET_TYPES,
        default='COLOR',
        update=_on_channel_changed,
    )
    uuid: StringProperty(name="UUID")

    def ensure_uuid(self):
        if not self.uuid:
            self.uuid = str(uuid.uuid4())


classes = (
    PaintSystemChannel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
