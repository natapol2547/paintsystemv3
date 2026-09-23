"""What Add Paint System and the Add Channel menu build (PS-041).

A material template adds the nodes that show a Paint System tree in a
material: Unlit, PBR, Paint Over, Normal or Group Only. A channel template
makes a channel, such as Roughness, and connects it to the input of the
shader node it paints into.

Every template follows one rule: nothing the user made is deleted and no
link is dropped. A link the paint needs is moved under the paint instead,
so right after Add the material looks as it did. The Add Paint System
dialog's recommendation and its summary of what changes come from here
too, so they describe what the build does.
"""
from dataclasses import dataclass

import blf
import bpy

from .common import blender_icon, is_newer_than, node_location
from .compiler.core import compile_tree, ensure_artifact
from .context import MATERIAL_GROUP_KEY, PREVIEW_RESTORE_KEY, PREVIEW_TREE_KEY, find_material_group_node
from .gpu_passes.texel_map import resolve_uv_map
from .props.channel import channel_alpha_name
from .props.preview import NEUTRAL_LOOK, set_enum


# The render engines that run EEVEE. Blender 4.2 and 4.5 call it EEVEE Next.
EEVEE_ENGINES = {'BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'}

PRINCIPLED = 'ShaderNodeBsdfPrincipled'
DIFFUSE = 'ShaderNodeBsdfDiffuse'
EMISSION = 'ShaderNodeEmission'
TRANSPARENT = 'ShaderNodeBsdfTransparent'
MIX_SHADER = 'ShaderNodeMixShader'
SHADER_TO_RGB = 'ShaderNodeShaderToRGB'
MATERIAL_OUTPUT = 'ShaderNodeOutputMaterial'

# The space the templates leave between the nodes they place, and the
# step between two nodes in one column. Node heights are not known in the
# background, so the step is a guess that fits the short shader nodes used.
NODE_GAP = 60.0
ROW_STEP = 130.0


# -- channel templates -----------------------------------------------------

@dataclass(frozen=True)
class ChannelTemplate:
    """A channel that paints into one input of a shader node."""
    name: str
    type: str
    description: str
    # The inputs it can paint into. It uses the first one the node has.
    sockets: tuple
    # The channel's options besides ``channel_defaults``, as (name, value).
    options: tuple = ()


CHANNEL_TEMPLATES = {
    'COLOR': ChannelTemplate(
        "Color", 'COLOR', "Paint the colour, into Base Color, or Color on a Diffuse BSDF",
        ('Base Color', 'Color')),
    'METALLIC': ChannelTemplate(
        "Metallic", 'FLOAT', "Paint how metallic the surface is, from 0 to 1",
        ('Metallic',), (('use_range', True), ('range_min', 0.0), ('range_max', 1.0))),
    'ROUGHNESS': ChannelTemplate(
        "Roughness", 'FLOAT', "Paint how rough the surface is, from 0 to 1",
        ('Roughness',), (('use_range', True), ('range_min', 0.0), ('range_max', 1.0))),
    'NORMAL': ChannelTemplate(
        "Normal", 'VECTOR', "Paint the surface's normals, in tangent space",
        ('Normal',), (('vector_kind', 'NORMAL'), ('paint_space', 'TANGENT'))),
}

# The shader input a colour channel's alpha paints into.
ALPHA_SOCKET = 'Alpha'


def channel_template(channel) -> ChannelTemplate | None:
    """The template *channel* matches by its name and type, or None."""
    return next((template for template in CHANNEL_TEMPLATES.values()
                 if template.name == channel.name and template.type == channel.type), None)


def create_template_channel(tree, key: str, uv_map: str = "", use_alpha: bool = True):
    """Add the channel of the template *key* below the active channel, and return it.

    *uv_map* is the UV map a vector channel's Tangent space follows; empty
    means the active render UV map. *use_alpha* False makes a colour
    channel without alpha, for a shader node with no Alpha input: its base
    alpha would stay 0 there, and a half-transparent stroke would show at
    full strength.
    """
    template = CHANNEL_TEMPLATES[key]
    channel = tree.create_channel(template.name, template.type)
    for prop, value in template.options:
        setattr(channel, prop, value)
    if template.type == 'VECTOR':
        channel.tangent_uv_map = uv_map
    if not use_alpha:
        channel.use_alpha = False
    return channel


# -- reading a material's nodes --------------------------------------------

class _Links:
    """The links of a node tree, indexed once by the node at each end.

    A muted link feeds nothing: Blender uses the input's own value. So
    ``source`` and a *live* ``upstream`` pass over muted links, as they
    answer what shows. Moving and loop checks see every link.
    """

    def __init__(self, node_tree):
        self.into = {}
        self.out_of = {}
        for link in node_tree.links:
            self.into.setdefault(link.to_node.name, []).append(link)
            self.out_of.setdefault(link.from_node.name, []).append(link)

    def feeding(self, socket):
        """The link into the input *socket*, muted or not, or None."""
        return next((link for link in self.into.get(socket.node.name, ()) if link.to_socket == socket), None)

    def source(self, socket):
        """The node that feeds the input *socket*, past any reroutes, or None."""
        link = self.feeding(socket)
        while link is not None and not link.is_muted and link.from_node.bl_idname == 'NodeReroute':
            link = self.feeding(link.from_node.inputs[0])
        return link.from_node if link is not None and not link.is_muted else None

    def upstream(self, node, live=False) -> list:
        """The nodes that feed *node*, nearest first. *live* leaves out muted links."""
        return self._walk(node, self.into, 'from_node', live)

    def downstream(self, node) -> list:
        """The nodes *node* feeds, nearest first."""
        return self._walk(node, self.out_of, 'to_node', False)

    @staticmethod
    def _walk(node, index, end, live):
        found, queue, seen = [], [node], {node.name}
        while queue:
            current = queue.pop(0)
            for link in index.get(current.name, ()):
                if live and link.is_muted:
                    continue
                other = getattr(link, end)
                if other.name not in seen:
                    seen.add(other.name)
                    found.append(other)
                    queue.append(other)
        return found


def material_output(material):
    """The Material Output the viewport shows *material* through, or None.

    Painting shows in Material Preview, which renders with EEVEE, so this
    is the output EEVEE uses. Blender prefers an output made for EEVEE over
    an active one made for all engines. A channel preview's output
    (PS-061) stands in for the output it replaced, so that one is returned.
    """
    node_tree = material.node_tree if material is not None else None
    if node_tree is None:
        return None
    output = node_tree.get_output_node('EEVEE')
    seen = set()
    while output is not None and output.get(PREVIEW_TREE_KEY) is not None and output.name not in seen:
        seen.add(output.name)
        output = node_tree.nodes.get(output.get(PREVIEW_RESTORE_KEY, ""))
    return output if output is not None and output.bl_idname == MATERIAL_OUTPUT else None


def surface_source(material):
    """The node that feeds the Surface of *material*'s output, or None."""
    output = material_output(material)
    if output is None:
        return None
    return _Links(material.node_tree).source(output.inputs['Surface'])


def _shown_nodes(material, group=None):
    """The nodes that show through *material*'s output, nearest first, and the material's links.

    Nodes that feed *group*, the Paint System's group node, are left out:
    after Paint Over the material's own shader feeds the group, and
    linking the group back into it would make a loop, which Blender drops.
    """
    output = material_output(material)
    if output is None:
        return [], None
    links = _Links(material.node_tree)
    feeds_group = {node.name for node in links.upstream(group)} if group is not None else set()
    return [node for node in links.upstream(output, live=True) if node.name not in feeds_group], links


def _nearest_shader(material, kinds, group=None):
    """The node of the first type in *kinds* nearest *material*'s output upstream, or None.

    *group* is as in ``_shown_nodes``.
    """
    nodes, _ = _shown_nodes(material, group)
    for kind in kinds:
        node = next((node for node in nodes if node.bl_idname == kind), None)
        if node is not None:
            return node
    return None


def _unlit_mix(links, emission):
    """The Mix Shader that shows *emission* over a Transparent BSDF, as ``_add_unlit_shader`` builds it, or None."""
    for link in links.out_of.get(emission.name, ()):
        mix = link.to_node
        if (not link.is_muted and mix.bl_idname == MIX_SHADER and link.to_socket == mix.inputs[2]
                and getattr(links.source(mix.inputs[1]), 'bl_idname', None) == TRANSPARENT):
            return mix
    return None


def find_target(material, group=None):
    """The shader node channels paint into, or None.

    That is the Principled BSDF nearest *material*'s output, else the
    nearest Diffuse BSDF, else the Emission of an unlit shader as the Unlit
    and Paint Over templates build it. *group* is as in ``_shown_nodes``.
    """
    target = _nearest_shader(material, (PRINCIPLED, DIFFUSE), group)
    if target is not None:
        return target
    nodes, links = _shown_nodes(material, group)
    return next((node for node in nodes if node.bl_idname == EMISSION and _unlit_mix(links, node) is not None),
                None)


def _alpha_input(links, target):
    """The input a colour channel's alpha paints into on *target*, or None.

    That is its Alpha input. The Emission of an unlit shader has none. Its
    alpha is the factor of the Mix Shader over the Transparent BSDF.
    """
    if target.bl_idname == EMISSION:
        mix = _unlit_mix(links, target)
        return mix.inputs[0] if mix is not None else None
    return target.inputs.get(ALPHA_SOCKET)


def node_title(node) -> str:
    """How the summary names *node*: an Image Texture by its image, else by its label or name."""
    image = getattr(node, 'image', None) if node.bl_idname == 'ShaderNodeTexImage' else None
    if image is not None:
        return image.name
    return node.label or node.name


# -- connecting channels ---------------------------------------------------

def ensure_group_node(material, tree):
    """The group node that runs *tree*'s compiled shader in *material*, added when there is none."""
    group = find_material_group_node(material, tree)
    if group is None:
        group = material.node_tree.nodes.new('ShaderNodeGroup')
        group[MATERIAL_GROUP_KEY] = tree.uuid
        group.label = tree.name
    group.node_tree = ensure_artifact(tree)
    return group


def connect_channel(material, group, channel, target) -> bool:
    """Paint *channel* into its input on the shader node *target*, and return whether it is connected.

    The input's link moves onto the group's input for the channel, or the
    input's value is copied there, so the material looks as it did. Then
    the group's output feeds the input. A colour channel's alpha goes the
    same way into the alpha input (see ``_alpha_input``), once the colour
    is connected. An input the group already feeds is left alone. So is
    one fed by a node the group feeds, and a *target* that feeds the
    group, since either would make a loop.
    """
    template = channel_template(channel)
    names = template.sockets if template is not None else ()
    socket = next((target.inputs[name] for name in names if target.inputs.get(name) is not None), None)
    if socket is None:
        return False
    node_tree = material.node_tree
    links = _Links(node_tree)
    loops = {node.name for node in links.downstream(group)} | {group.name}
    if target.name in {node.name for node in links.upstream(group)}:
        return False
    if not _route_through_group(node_tree, links, loops, group, socket, channel.name):
        return False
    if channel.use_alpha:
        name = channel_alpha_name(channel.name)
        alpha = _alpha_input(links, target)
        group_alpha = group.inputs.get(name)
        if alpha is not None:
            _route_through_group(node_tree, links, loops, group, alpha, name)
        elif group_alpha is not None and not group_alpha.is_linked:
            # Nothing shows the alpha, so the paint starts opaque. At the
            # input's default of 0 a half-transparent stroke would show at
            # full strength.
            group_alpha.default_value = 1.0
    return True


def _route_through_group(node_tree, links, loops, group, socket, name) -> bool:
    link = links.feeding(socket)
    group_input = group.inputs.get(name)
    group_output = group.outputs.get(name)
    if group_input is None or group_output is None:
        return False
    if link is not None and link.from_socket == group_output:
        return True
    # The group is in *loops* too, so this also keeps a link from another
    # of its outputs: the user connected another channel here.
    if link is not None and link.from_node.name in loops:
        return False
    if group_input.is_linked:
        # The group's input is taken, so the socket's own link has nowhere
        # to go. Keep it rather than drop it.
        if link is not None:
            return False
    elif link is not None:
        muted = link.is_muted
        moved = node_tree.links.new(link.from_socket, group_input)
        # A muted link feeds nothing, and the socket's own value shows. It
        # stays muted, and the value comes along.
        moved.is_muted = muted
        if muted:
            group_input.default_value = socket.default_value
    else:
        group_input.default_value = socket.default_value
    node_tree.links.new(group_output, socket)
    return True


def add_template_channel(tree, key: str):
    """Add the channel of template *key* to *tree* and connect it in every material that runs the tree.

    Returns the channel and the number of materials it was connected in.
    Each material connects it to its own target node. A nested tree has no
    group node in a material, so it only gets the channel. A linked
    material is left alone: it is read again from its library when the
    file opens, so the connection would not last.
    """
    found = []
    for material in bpy.data.materials:
        group = find_material_group_node(material, tree) if material.is_editable else None
        target = find_target(material, group) if group is not None else None
        if target is not None:
            found.append((material, group, target))
    # Without alpha only when every target lacks an alpha input.
    use_alpha = not found or any(_alpha_input(_Links(material.node_tree), target) is not None
                                 for material, _, target in found)
    channel = create_template_channel(tree, key, use_alpha=use_alpha)
    # The groups only get the channel's sockets once the tree compiles.
    compile_tree(tree)
    for material, group, target in found:
        group.node_tree = ensure_artifact(tree)
    connected = sum(connect_channel(material, group, channel, target) for material, group, target in found)
    return channel, connected


def restore_paint_over(material, group, tree, target) -> None:
    """Feed a new *group* from the Shader to RGB a deleted group node left behind.

    Paint Over renders the material through a Shader to RGB into the group
    node's colour. Deleting the group node leaves the Shader to RGB feeding
    nothing, and the unlit shader's Emission, *target*, painted into by
    nothing. The Shader to RGB then feeds the colour channel again, so the
    paint goes on top of the material as before.
    """
    if target is None or target.bl_idname != EMISSION:
        return
    channel = next((channel for channel in tree.channels
                    if channel_template(channel) == CHANNEL_TEMPLATES['COLOR']), None)
    node_tree = material.node_tree
    to_rgb = next((node for node in node_tree.nodes if node.bl_idname == SHADER_TO_RGB
                   and node.inputs['Shader'].is_linked
                   and not any(socket.is_linked for socket in node.outputs)), None)
    if channel is None or to_rgb is None:
        return
    for name, socket in ((channel.name, 'Color'), (channel_alpha_name(channel.name), 'Alpha')):
        group_input = group.inputs.get(name)
        if group_input is not None and not group_input.is_linked:
            node_tree.links.new(to_rgb.outputs[socket], group_input)


# -- placing nodes ---------------------------------------------------------

def _move_to(node, x, y):
    """Move *node* so its location outside any frame is (x, y). A node in a frame moves by the difference."""
    at = node_location(node)
    node.location = (node.location.x + x - at.x, node.location.y + y - at.y)


def _chain_start(node_tree, output, leave_out):
    """Where a new chain of nodes starts: right of the tree's nodes, at *output*'s height.

    The nodes named in *leave_out* do not count, such as the new group node
    and an output that moves to the end of the chain. Frames do not count
    either, as their nodes do. With nothing left the chain starts at x 0.
    """
    nodes = [node for node in node_tree.nodes
             if node.name not in leave_out and node.bl_idname != 'NodeFrame']
    x = max((node_location(node).x + node.width for node in nodes), default=-NODE_GAP) + NODE_GAP
    y = node_location(output).y if output is not None else 0.0
    return x, y


def _place_columns(columns, x, y):
    """Place *columns* of nodes left to right from (x, y). The nodes of a column stack downwards."""
    for column in columns:
        for row, node in enumerate(column):
            _move_to(node, x, y - row * ROW_STEP)
        x += max(node.width for node in column) + NODE_GAP


def _place_before(node_tree, group, target):
    """Put *group* left of *target*, moving the nodes that feed *target* left to make room."""
    shift = group.width + NODE_GAP
    for node in _Links(node_tree).upstream(target):
        node.location.x -= shift
    at = node_location(target)
    _move_to(group, at.x - shift, at.y)


def place_group(material, group, target):
    """Place a new *group* left of *target*, or right of the material's nodes when there is no target."""
    node_tree = material.node_tree
    if target is not None:
        _place_before(node_tree, group, target)
    else:
        x, y = _chain_start(node_tree, material_output(material), {group.name})
        _move_to(group, x, y)


# -- material templates ----------------------------------------------------

# (identifier, name, description, icon, number). The dialog draws them as
# cards with ``prop_enum``, which draws each item's own icon. Placeholder
# Blender icons: ``prop_enum`` takes no ``icon_value``, so an add-on icon
# would need an items callback.
MATERIAL_TEMPLATE_ITEMS = [
    ('UNLIT', "Unlit", "Paint colours that show as painted, without lighting",
     blender_icon('IMAGE'), 0),
    ('PBR', "PBR", "Paint into a Principled BSDF's inputs, such as Base Color and Roughness",
     blender_icon('MATERIAL'), 1),
    ('PAINT_OVER', "Paint Over",
     "Paint over the material as it renders. Needs EEVEE and something connected to the "
     "Material Output's Surface", blender_icon('BRUSH_DATA'), 2),
    ('NORMAL', "Normal", "Paint normals, shown on a grey Diffuse BSDF",
     blender_icon('NORMALS_FACE'), 3),
    ('GROUP', "Group Only", "Add the Paint System node without connecting it",
     blender_icon('NODETREE'), 4),
]

# The templates whose shader is unlit. They hide back faces and switch the
# view to Standard unless told otherwise, so the paint shows as painted.
UNLIT_TEMPLATES = {'UNLIT', 'PAINT_OVER'}

# The dialog's checkbox of each channel the PBR template can make, in order.
PBR_CHANNEL_OPTIONS = {
    'COLOR': 'add_color',
    'METALLIC': 'add_metallic',
    'ROUGHNESS': 'add_roughness',
    'NORMAL': 'add_normal',
}

PAINT_OVER_NEEDS = "Paint Over needs EEVEE and something connected to the Material Output's Surface"
NO_CHANNEL = "Pick at least one channel"


def template_defaults(template: str) -> dict:
    """The dialog options that picking *template* sets."""
    unlit = template in UNLIT_TEMPLATES
    return {'use_backface_culling': unlit, 'use_standard_view': unlit}


def template_channels(template: str, options) -> list[str]:
    """The channel templates *template* makes, in order. *options* holds the PBR checkboxes."""
    if template == 'PBR':
        return [key for key, prop in PBR_CHANNEL_OPTIONS.items() if getattr(options, prop)]
    return ['NORMAL'] if template == 'NORMAL' else ['COLOR']


def paint_over_possible(material, scene) -> bool:
    """Whether Paint Over can run: it renders the material through Shader to RGB, which only EEVEE has."""
    return scene.render.engine in EEVEE_ENGINES and surface_source(material) is not None


def recommend(material, scene) -> tuple[str, str]:
    """The template the dialog starts on for *material*, and the reason it shows."""
    if material is None:
        return 'UNLIT', "Picked: the object has no material yet."
    if material.node_tree is None:
        return 'UNLIT', "Picked: the material has no nodes yet."
    source = surface_source(material)
    if source is None:
        return 'UNLIT', "Picked: nothing feeds the Material Output."
    if source.bl_idname == PRINCIPLED:
        return 'PBR', "Picked: the material has a Principled BSDF."
    if scene.render.engine in EEVEE_ENGINES:
        return 'PAINT_OVER', "Picked: the material has its own shader."
    if _nearest_shader(material, (PRINCIPLED,)) is not None:
        return 'PBR', "Picked: the material has a Principled BSDF."
    return 'GROUP', "Picked: no Principled BSDF to paint into."


def refusal(template: str, options, material, scene) -> str | None:
    """Why *template* cannot be built with *options*, or None when it can."""
    if template == 'PAINT_OVER' and not paint_over_possible(material, scene):
        return PAINT_OVER_NEEDS
    if not template_channels(template, options):
        return NO_CHANNEL
    return None


def new_material_name(obj) -> str:
    """A free name for *obj*'s new material, so the summary names it as it will be."""
    base = name = f"{obj.name} Material"
    number = 0
    while name in bpy.data.materials:
        number += 1
        name = f"{base}.{number:03d}"
    return name


def prepare_material(material, template: str, fresh: bool) -> None:
    """Make sure *material* has nodes before a template builds in it.

    Before Blender 5.0 a material can have Use Nodes off, or no node tree
    at all; turning it on gives a missing tree Blender's default Principled
    BSDF and Material Output. From 5.0 every material has its nodes. A
    *fresh* material, one without a tree until now, has only those
    defaults. Unlit and Normal have no use for the Principled, and it was
    never the user's, so it is removed.
    """
    if not is_newer_than(5, 0):
        material.use_nodes = True
    if fresh and template in {'UNLIT', 'NORMAL'}:
        nodes = material.node_tree.nodes
        principled = next((node for node in nodes if node.bl_idname == PRINCIPLED), None)
        if principled is not None:
            nodes.remove(principled)


def _take_over(node_tree, old):
    """A new active Material Output that shows what *old* showed, except its Surface.

    It takes *old*'s target engine, and its Volume, Displacement and
    Thickness links, muted as they were: an output socket can feed several
    inputs, so no link moves. *old* may be None.
    """
    links = _Links(node_tree)
    output = node_tree.nodes.new(MATERIAL_OUTPUT)
    if old is not None:
        output.target = old.target
        for name in ('Volume', 'Displacement', 'Thickness'):
            link = links.feeding(old.inputs[name]) if old.inputs.get(name) is not None else None
            if link is not None and output.inputs.get(name) is not None:
                copied = node_tree.links.new(link.from_socket, output.inputs[name])
                copied.is_muted = link.is_muted
    output.is_active_output = True
    return output


def _surface_output(material, group):
    """The output a template's shader goes into, and where its chain of nodes starts.

    That is the material's output when no link goes into its Surface. It
    moves to the end of the chain. Otherwise a new output takes over from
    it, so a link that shows nothing, such as a muted one, stays too.
    """
    node_tree = material.node_tree
    output = material_output(material)
    if output is not None and not output.inputs['Surface'].is_linked:
        return output, _chain_start(node_tree, output, {group.name, output.name})
    start = _chain_start(node_tree, output, {group.name})
    return _take_over(node_tree, output), start


def _add_unlit_shader(node_tree, group, channel):
    """Show *channel*'s colour unlit, over a transparent background by its alpha.

    Returns the Transparent BSDF, the Emission and the Mix Shader. The Mix
    Shader's inputs are both called Shader, and its factor is Fac before
    Blender 5.0 and Factor from 5.0, so they are used by index. The factor's
    own value is 1, so turning the channel's Use Alpha off, which drops its
    link, leaves the material opaque instead of half transparent.
    """
    transparent = node_tree.nodes.new(TRANSPARENT)
    emission = node_tree.nodes.new(EMISSION)
    mix = node_tree.nodes.new(MIX_SHADER)
    mix.inputs[0].default_value = 1.0
    node_tree.links.new(group.outputs[channel.name], emission.inputs['Color'])
    alpha = group.outputs.get(channel_alpha_name(channel.name))
    if alpha is not None:
        node_tree.links.new(alpha, mix.inputs[0])
    node_tree.links.new(transparent.outputs[0], mix.inputs[1])
    node_tree.links.new(emission.outputs[0], mix.inputs[2])
    return transparent, emission, mix


def _build_unlit(material, group, tree, options):
    node_tree = material.node_tree
    channel = tree.channels[0]
    output, (x, y) = _surface_output(material, group)
    transparent, emission, mix = _add_unlit_shader(node_tree, group, channel)
    node_tree.links.new(mix.outputs[0], output.inputs['Surface'])
    _place_columns([[group], [transparent, emission], [mix], [output]], x, y)
    # The canvas is the base the layers paint over.
    group.inputs[channel.name].default_value = options.canvas
    alpha = group.inputs.get(channel_alpha_name(channel.name))
    if alpha is not None:
        alpha.default_value = options.canvas[3]


def _build_pbr(material, group, tree, options):
    node_tree = material.node_tree
    principled = _nearest_shader(material, (PRINCIPLED,))
    if principled is None:
        output, (x, y) = _surface_output(material, group)
        principled = node_tree.nodes.new(PRINCIPLED)
        node_tree.links.new(principled.outputs[0], output.inputs['Surface'])
        _place_columns([[group], [principled], [output]], x, y)
    else:
        _place_before(node_tree, group, principled)
    for channel in tree.channels:
        connect_channel(material, group, channel, principled)


def _build_paint_over(material, group, tree, options):
    node_tree = material.node_tree
    channel = tree.channels[0]
    output = material_output(material)
    surface = output.inputs['Surface']
    source = _Links(node_tree).feeding(surface).from_socket
    x, y = _chain_start(node_tree, output, {group.name, output.name})
    columns = []
    alpha = group.inputs.get(channel_alpha_name(channel.name))
    if source.type == 'SHADER':
        # Shader to RGB gives the shader's colour as it renders, with its
        # transparency as alpha, so the paint goes on top of the material.
        to_rgb = node_tree.nodes.new(SHADER_TO_RGB)
        node_tree.links.new(source, to_rgb.inputs['Shader'])
        node_tree.links.new(to_rgb.outputs['Color'], group.inputs[channel.name])
        if alpha is not None:
            node_tree.links.new(to_rgb.outputs['Alpha'], alpha)
        columns.append([to_rgb])
    else:
        node_tree.links.new(source, group.inputs[channel.name])
        if alpha is not None:
            alpha.default_value = 1.0
    transparent, emission, mix = _add_unlit_shader(node_tree, group, channel)
    # Replaces the source's link, which now feeds the paint.
    node_tree.links.new(mix.outputs[0], surface)
    _place_columns(columns + [[group], [transparent, emission], [mix], [output]], x, y)


def _build_normal(material, group, tree, options):
    node_tree = material.node_tree
    output, (x, y) = _surface_output(material, group)
    # A grey Diffuse BSDF shows the painted normals by their shading.
    diffuse = node_tree.nodes.new(DIFFUSE)
    node_tree.links.new(diffuse.outputs[0], output.inputs['Surface'])
    _place_columns([[group], [diffuse], [output]], x, y)
    connect_channel(material, group, tree.channels[0], diffuse)


def _build_group(material, group, tree, options):
    place_group(material, group, None)


_BUILDERS = {
    'UNLIT': _build_unlit,
    'PBR': _build_pbr,
    'PAINT_OVER': _build_paint_over,
    'NORMAL': _build_normal,
    'GROUP': _build_group,
}


def build_material(template: str, material, group, tree, options) -> None:
    """Build *template*'s nodes around *group*, the new group node that runs *tree* in *material*.

    The tree has its channels and is compiled, so the group has its
    sockets. *options* holds the dialog's options, such as the Unlit canvas.
    """
    _BUILDERS[template](material, group, tree, options)


def apply_material_settings(material, options) -> None:
    if options.use_smooth_transparency:
        material.surface_render_method = 'BLENDED'
    if options.use_backface_culling:
        material.use_backface_culling = True
        material.use_transparency_overlap = False


def _preview_view_shows(scene) -> bool:
    """Whether a channel preview's (PS-061) view transform is on screen.

    It is not when the user picked another one while previewing. The
    preview then leaves that one when it ends.
    """
    display = scene.paint_system.preview_display
    return display.is_saved and scene.view_settings.view_transform == display.applied


def standard_view_applies(scene) -> bool:
    """Whether switching *scene*'s own view transform to Standard changes it.

    While a channel preview's view transform shows, the scene's own one is
    the one the preview puts back. A custom colour management may have no
    Standard view.
    """
    display = scene.paint_system.preview_display
    own = display.view_transform if _preview_view_shows(scene) else scene.view_settings.view_transform
    has_standard = bpy.types.UILayout.enum_item_name(scene.view_settings, 'view_transform', 'Standard') != ""
    return own != 'Standard' and has_standard


def apply_standard_view(scene) -> None:
    """Switch *scene*'s own view transform to Standard, with no look.

    A Filmic look such as Very High Contrast survives the switch, so the
    look is cleared too. While a preview is saved, the display it puts back
    at the end gets Standard. When the user picked another view while
    previewing, the screen switches now as well.
    """
    display = scene.paint_system.preview_display
    shows = _preview_view_shows(scene)
    if display.is_saved:
        display.view_transform = 'Standard'
        display.look = NEUTRAL_LOOK
    if not shows and set_enum(scene.view_settings, 'view_transform', 'Standard'):
        set_enum(scene.view_settings, 'look', NEUTRAL_LOOK)


# -- the dialog's summary --------------------------------------------------

@dataclass(frozen=True)
class SummaryLine:
    text: str
    # A problem to fix before Add, or before painting. It shows an error icon.
    warning: bool = False
    indent: bool = False


def _join(names) -> str:
    """'A', 'A and B', or 'A, B and C'."""
    names = list(names)
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _replaced_lines(material, template) -> list[SummaryLine]:
    """What stops showing when *template* takes over the material's output, and what Cycles shows."""
    output = material_output(material)
    # The templates that add a shader of their own for the output.
    adds_shader = template in {'UNLIT', 'NORMAL'} or (
        template == 'PBR' and _nearest_shader(material, (PRINCIPLED,)) is None)
    lines = []
    if adds_shader and output is not None and output.inputs['Surface'].is_linked:
        source = surface_source(material)
        if source is not None:
            lines.append(SummaryLine(f'"{node_title(source)}" stays, but no longer shows.'))
        kept = [socket.name for socket in output.inputs if socket.name != 'Surface' and socket.is_linked]
        if kept:
            lines.append(SummaryLine(f"The new output keeps the {_join(kept)}."))
    if adds_shader or template == 'PAINT_OVER':
        # Cycles prefers an output made for it. It shows the paint only
        # through the output the paint goes into.
        cycles = material.node_tree.get_output_node('CYCLES')
        if cycles is not None and cycles != output:
            lines.append(SummaryLine(f'Cycles keeps using "{node_title(cycles)}".'))
    return lines


def _template_lines(template, options, material, scene) -> list[SummaryLine]:
    if template == 'UNLIT':
        return [SummaryLine("Adds an unlit shader that shows the paint.")]
    if template == 'NORMAL':
        return [SummaryLine("Adds a grey Diffuse shader to show the normals.")]
    if template == 'GROUP':
        return [SummaryLine("Adds the Paint System node, not connected."),
                SummaryLine("Nothing shows until you connect it.")]
    if template == 'PAINT_OVER':
        if not paint_over_possible(material, scene):
            return [SummaryLine(f"{PAINT_OVER_NEEDS}.", warning=True)]
        return [SummaryLine(f'Paints over "{node_title(surface_source(material))}" as it renders.'),
                SummaryLine("The paint on top is unlit.")]
    # PBR. A material without nodes gets Blender's default Principled BSDF.
    fresh = material is None or material.node_tree is None
    principled = None if fresh else _nearest_shader(material, (PRINCIPLED,))
    if principled is not None or fresh:
        title = node_title(principled) if principled is not None else "Principled BSDF"
        lines = [SummaryLine(f'Paints into "{title}":')]
    else:
        lines = [SummaryLine("Adds a Principled BSDF to paint into:")]
    links = _Links(material.node_tree) if principled is not None else None
    for key in template_channels(template, options):
        name = CHANNEL_TEMPLATES[key].sockets[0]
        source = _input_source(links, principled, name)
        lines.append(SummaryLine(f'{name}, over "{node_title(source)}"' if source is not None else name,
                                 indent=True))
        # The colour's alpha goes under the paint too. That only shows when
        # something feeds the Alpha. Its value alone changes nothing.
        alpha = _input_source(links, principled, ALPHA_SOCKET) if key == 'COLOR' else None
        if alpha is not None:
            lines.append(SummaryLine(f'{ALPHA_SOCKET}, over "{node_title(alpha)}"', indent=True))
    return lines


def _input_source(links, node, name):
    """The node that feeds *node*'s input *name*, or None. *node* may be None."""
    socket = node.inputs.get(name) if node is not None else None
    return links.source(socket) if socket is not None else None


def summary_lines(template: str, options, obj, context) -> list[SummaryLine]:
    """What Add does to *obj*'s material with *template* and *options*, one fact per line."""
    material = obj.active_material
    scene = context.scene
    if material is None:
        lines = [SummaryLine(f'Makes a new material "{new_material_name(obj)}".')]
    else:
        lines = [SummaryLine(f'Adds to the material "{material.name}".')]
    # A selected mesh that uses the material shows the paint as well.
    if any(other.type == 'MESH' and other != obj
           and (material is None or all(slot.material != material for slot in other.material_slots))
           for other in context.selected_objects):
        lines.append(SummaryLine("Only the active object is set up."))
    lines += _template_lines(template, options, material, scene)
    if material is not None and material.node_tree is not None:
        lines += _replaced_lines(material, template)
    if material is not None and not is_newer_than(5, 0) and not material.use_nodes:
        lines.append(SummaryLine("Turns on the material's nodes."))

    channels = template_channels(template, options)
    if options.start_with == 'IMAGE' and channels:
        size = options.resolution
        lines.append(SummaryLine(
            f'Adds a {size} x {size} image layer to "{CHANNEL_TEMPLATES[channels[0]].name}".'))
    if options.start_with == 'IMAGE' or 'NORMAL' in channels:
        uv_map = resolve_uv_map(obj, options.uv_map)
        if len(obj.data.uv_layers) == 0:
            lines.append(SummaryLine("The mesh has no UV map. Unwrap it to paint.", warning=True))
        elif uv_map is not None:
            lines.append(SummaryLine(f'Uses the UV map "{uv_map}".'))

    if options.use_smooth_transparency:
        lines.append(SummaryLine("Turns on smooth transparency."))
    if options.use_backface_culling:
        lines.append(SummaryLine("Hides back faces."))
    if options.use_standard_view and standard_view_applies(scene):
        lines.append(SummaryLine("Switches the view to Standard."))
    if not channels:
        lines.append(SummaryLine(f"{NO_CHANNEL}.", warning=True))
    lines.append(SummaryLine("Nothing is deleted or disconnected."))
    return lines


def wrap_text(text: str, width: float, points: float) -> list[str]:
    """*text* broken at spaces into lines no wider than *width*.

    Widths are measured in the UI font at *points*, in UI units. A label
    never wraps by itself, so the dialog wraps long lines with this.
    """
    blf.size(0, points)
    lines, line = [], ""
    for word in text.split(" "):
        candidate = f"{line} {word}" if line else word
        if line and blf.dimensions(0, candidate)[0] > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    lines.append(line)
    return lines
