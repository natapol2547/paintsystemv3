import re
import uuid

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.utils import register_classes_factory

from ..common import is_newer_than


CHANNEL_SOCKET_TYPES = [
    ('COLOR', "Color", "Color (RGBA) channel"),
    ('FLOAT', "Float", "Float (scalar) channel"),
    ('VECTOR', "Vector", "Vector (XYZ) channel"),
]

CHANNEL_COLOR_SPACES = [
    ('COLOR', "Color", "The values are colours, stored as sRGB"),
    ('NONCOLOR', "Non-Color", "The values are data, such as roughness or height, stored as they are"),
]


def channel_defaults(channel_type: str) -> dict:
    """The options a new channel of *channel_type* starts with.

    Only colour has a meaningful alpha. The other types stack over an
    opaque base, as a material input such as Roughness expects.
    """
    color = channel_type == 'COLOR'
    return {'use_alpha': color, 'color_space': 'COLOR' if color else 'NONCOLOR'}


def image_colorspace(channel) -> str:
    """The colour space of a new image painted in *channel*, which may be None."""
    return 'Non-Color' if channel is not None and channel.color_space == 'NONCOLOR' else 'sRGB'

_CHANNEL_TYPE_TO_SOCKET = {
    'COLOR': 'NodeSocketColor',
    'FLOAT': 'NodeSocketFloat',
    'VECTOR': 'NodeSocketVector',
}


def channel_socket_type(channel_type: str) -> str:
    return _CHANNEL_TYPE_TO_SOCKET.get(channel_type, 'NodeSocketColor')


def channel_alpha_name(channel_name: str) -> str:
    return f"{channel_name} Alpha"


# The range of a float interface socket that has no limits.
_FLOAT_LIMIT = 3.4028234663852886e+38


def _float_range_props(channel) -> dict:
    """Subtype and range of a FLOAT channel's interface socket.

    With ``use_range`` the material's input becomes a slider between the
    channel's min and max. The range is a soft limit: it bounds the
    slider's drag, and a typed or linked value outside it is kept.
    Blender never clamps a value when the range changes, and neither does
    the compiled shader.
    """
    if not channel.use_range:
        return {'subtype': 'NONE', 'min_value': -_FLOAT_LIMIT, 'max_value': _FLOAT_LIMIT,
                'default_value': 0.0}
    # Nothing stops min from being set above max, so sort them.
    low, high = sorted((channel.range_min, channel.range_max))
    return {'subtype': 'FACTOR', 'min_value': low, 'max_value': high,
            'default_value': min(max(0.0, low), high)}


def interface_socket_specs(channels) -> list[tuple[str, str, dict]]:
    """The compiled tree's interface: (name, socket bl_idname, properties).

    Every channel contributes a socket of its own type, and an alpha
    socket when ``use_alpha`` is on. Only inputs take the properties.
    """
    specs: list[tuple[str, str, dict]] = []
    for ch in channels:
        props = {}
        if ch.type == 'COLOR':
            props['default_value'] = (0.0, 0.0, 0.0, 0.0)
        elif ch.type == 'FLOAT':
            props = _float_range_props(ch)
        specs.append((ch.name, channel_socket_type(ch.type), props))
        if ch.use_alpha:
            specs.append((channel_alpha_name(ch.name), 'NodeSocketFloat',
                          {'subtype': 'FACTOR', 'min_value': 0.0, 'max_value': 1.0,
                           'default_value': 0.0}))
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
    # ``create_channel`` picks the next two from the type. The defaults
    # here keep channels saved before they existed as they were.
    use_alpha: BoolProperty(
        name="Use Alpha",
        description=("Give the material an alpha input and output for this channel. "
                     "Off, the layers stack over an opaque base and only the colour comes out"),
        default=True,
        update=_on_channel_changed,
    )
    color_space: EnumProperty(
        name="Color Space",
        description="How new images in this channel store their values",
        items=CHANNEL_COLOR_SPACES,
        default='COLOR',
    )
    use_range: BoolProperty(
        name="Limit Range",
        description="Show the material's input for this channel as a slider between Min and Max",
        default=False,
        update=_on_channel_changed,
    )
    range_min: FloatProperty(name="Min", default=0.0, update=_on_channel_changed)
    range_max: FloatProperty(name="Max", default=1.0, update=_on_channel_changed)
    uuid: StringProperty(name="UUID")

    def ensure_uuid(self):
        if not self.uuid:
            self.uuid = str(uuid.uuid4())


classes = (
    PaintSystemChannel,
)


register, unregister = register_classes_factory(classes)
