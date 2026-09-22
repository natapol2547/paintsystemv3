import re
import uuid

import bpy
from bpy.props import StringProperty, EnumProperty
from bpy.utils import register_classes_factory

from ..common import is_newer_than


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


def interface_socket_specs(channels) -> list[tuple[str, str, dict]]:
    """The compiled tree's interface: (name, socket bl_idname, properties).

    Every channel contributes a socket of its own type and an alpha socket.
    """
    specs: list[tuple[str, str, dict]] = []
    for ch in channels:
        props = {}
        if ch.type == 'COLOR':
            props['default_value'] = (0.0, 0.0, 0.0, 0.0)
        elif ch.type == 'FLOAT':
            props['default_value'] = 0.0
        specs.append((ch.name, channel_socket_type(ch.type), props))
        specs.append((channel_alpha_name(ch.name), 'NodeSocketFloat', {'default_value': 0.0}))
    return specs


def channel_socket_specs(channels) -> list[tuple[str, str, dict]]:
    """The Paint System tree's channel sockets: (name, socket bl_idname, properties).

    The custom Group Input/Output nodes and PaintSystemGroupLayerNode use
    this layout. Every link in a Paint System tree carries one RGBA value,
    whatever the channel's type, so each channel is one colour socket. The
    compiler splits it into the typed socket and the alpha socket of
    ``interface_socket_specs``. The sockets are only ever linked, so they
    hide their value fields.
    """
    return [(ch.name, 'NodeSocketColor', {'hide_value': True, 'default_value': (0.0, 0.0, 0.0, 0.0)})
            for ch in channels]


def get_next_unique_name(name: str, list_of_names: list[str]) -> str:
    """*name*, or the next numbered name in its sequence when *list_of_names* has it.

    The sequence is the name up to its first digit: with 'Color' and
    'Color 2' taken, 'Color' and 'Color 2' both become 'Color 3'.
    """
    if name not in list_of_names:
        return name
    base_name = re.match(r'(\D*)', name).group(1).strip()
    # 0 stands for the bare base name, so 'Color' alone gives 'Color 1'.
    numbers_found = {0}
    pattern = re.compile(rf"^{re.escape(base_name)}(?: (\d+))?$")
    for item in list_of_names:
        match = pattern.match(item)
        if match and match.group(1):
            numbers_found.add(int(match.group(1)))
    return f"{base_name} {max(numbers_found) + 1}"


def _other_channel_names(channel) -> list[str]:
    return [item.name for item in channel.id_data.channels if item != channel]


def _set_name_transform(self, new_value, curr_value, is_set):
    return get_next_unique_name(new_value, _other_channel_names(self))


def _ensure_unique_name(channel) -> bool:
    """Rename *channel* if another channel has its name. Used before Blender 5.0.

    Before 5.0 there is no `set_transform`, so the rename happens in the
    update callback. Assigning the name runs the update once more, and
    that second pass finds the name unique and stops.
    """
    unique = get_next_unique_name(channel.name, _other_channel_names(channel))
    if unique != channel.name:
        channel.name = unique
        return True
    return False


def _on_channel_changed(self, context):
    tree = self.id_data
    if tree is None or tree.bl_idname != 'PaintSystemNodeTree':
        return
    if not is_newer_than(5, 0) and _ensure_unique_name(self):
        return  # the rename re-entered this callback and already refreshed
    tree.on_channels_changed()


class PaintSystemChannel(bpy.types.PropertyGroup):
    name: StringProperty(
        name="Name",
        default="Channel",
        update=_on_channel_changed,
        # set_transform exists from Blender 5.0. Older versions rename in
        # the update callback.
        **({'set_transform': _set_name_transform} if is_newer_than(5, 0) else {}),
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


register, unregister = register_classes_factory(classes)
