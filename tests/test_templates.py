"""What Add Paint System and the Add Channel menu build (PS-041).

Each material template runs on a set of materials: none yet, Blender's
default one, a textured Base Color, nothing on Surface, an Emission of
its own, split EEVEE and Cycles outputs, a Displacement, and a Principled
BSDF in a frame. Every run must keep the nodes and links from before (a
link the paint needs moves under the group node) and build the
template's own graph. Cycles renders show that PBR keeps the look and
that Unlit shows its canvas. The recommendation, the summary lines and
the dialog draw are checked without a window.

Run:  blender -b --factory-startup --python tests/test_templates.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (  # noqa: E402
    RecordingLayout, bake_group, before, check, close, finish, fmt, guarded, import_from, register_addon,
    section,
)

register_addon()
T = import_from("templates")
node_tree_ops = import_from("ops.node_tree_ops")
find_material_group_node = import_from("context").find_material_group_node
PREVIEW_TREE_KEY = import_from("context").PREVIEW_TREE_KEY
node_location = import_from("common").node_location
icon_kwargs = import_from("common").icon_kwargs
core = import_from("compiler.core")
PREVIEW_OUTPUT = import_from("props.channel").PREVIEW_OUTPUT

SETUP = node_tree_ops.PAINTSYSTEM_OT_setup_material
EEVEE = 'BLENDER_EEVEE_NEXT' if before(5) else 'BLENDER_EEVEE'
TEMPLATES = ['UNLIT', 'PBR', 'PAINT_OVER', 'NORMAL', 'GROUP']
IMAGE_LAYER = 'PaintSystemImageLayerNode'
SOLID_LAYER = 'PaintSystemSolidColorLayerNode'
scene = bpy.context.scene


# -- objects and materials -------------------------------------------------

def activate(*objects):
    """Select *objects* only, and make the first one active."""
    for other in bpy.context.view_layer.objects:
        other.select_set(other in objects)
    bpy.context.view_layer.objects.active = objects[0]


def new_object(name, material=None, uv_maps=("UVMap",)):
    """A unit plane whose UV maps cover 0..1, with *material* in its one slot, made the only selected object."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    for uv_name in uv_maps:
        uv = mesh.uv_layers.new(name=uv_name)
        for loop, co in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
            loop.uv = co
    if material is not None:
        mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    activate(obj)
    return obj


def default_material(name):
    """A material with Blender's default Principled BSDF into a Material Output, as the factory cube has."""
    material = bpy.data.materials.new(name)
    if before(5):
        material.use_nodes = True
    return material


def first(material, bl_idname):
    return next(node for node in material.node_tree.nodes if node.bl_idname == bl_idname)


def wood_image():
    image = bpy.data.images.get("PS Wood")
    if image is None:
        image = bpy.data.images.new("PS Wood", 4, 4)
        image.pixels.foreach_set(np.tile((0.6, 0.3, 0.1, 1.0), 16).astype(np.float32)
                                 * np.repeat(np.linspace(0.5, 1.0, 16), 4).astype(np.float32))
        image.pack()
    return image


def textured(name):
    """The default material with an Image Texture on Base Color, and its own Metallic and Roughness."""
    material = default_material(name)
    nt = material.node_tree
    principled = first(material, T.PRINCIPLED)
    texture = nt.nodes.new('ShaderNodeTexImage')
    texture.image = wood_image()
    texture.location = (principled.location.x - 300, principled.location.y)
    nt.links.new(texture.outputs['Color'], principled.inputs['Base Color'])
    principled.inputs['Metallic'].default_value = 0.25
    principled.inputs['Roughness'].default_value = 0.3
    return material


def no_surface(name):
    material = default_material(name)
    material.node_tree.nodes.remove(first(material, T.PRINCIPLED))
    return material


def emission(name):
    """A material whose own Emission feeds the output."""
    material = no_surface(name)
    nt = material.node_tree
    node = nt.nodes.new('ShaderNodeEmission')
    node.inputs['Color'].default_value = (0.2, 0.4, 0.9, 1.0)
    nt.links.new(node.outputs[0], first(material, T.MATERIAL_OUTPUT).inputs['Surface'])
    return material


def split(name):
    """The default material, whose output is for Cycles, and an Emission into an output for EEVEE."""
    material = default_material(name)
    nt = material.node_tree
    first(material, T.MATERIAL_OUTPUT).target = 'CYCLES'
    node = nt.nodes.new('ShaderNodeEmission')
    output = nt.nodes.new(T.MATERIAL_OUTPUT)
    output.target = 'EEVEE'
    output.location = (300, -300)
    nt.links.new(node.outputs[0], output.inputs['Surface'])
    return material


def displaced(name):
    material = default_material(name)
    nt = material.node_tree
    node = nt.nodes.new('ShaderNodeDisplacement')
    nt.links.new(node.outputs[0], first(material, T.MATERIAL_OUTPUT).inputs['Displacement'])
    return material


def framed(name):
    """The default material with its Principled BSDF in a frame away from the origin."""
    material = default_material(name)
    frame = material.node_tree.nodes.new('NodeFrame')
    frame.location = (-900.0, 500.0)
    first(material, T.PRINCIPLED).parent = frame
    return material


def muted_surface(name):
    """The default material with its link into Surface muted, so it shows nothing."""
    material = default_material(name)
    first(material, T.MATERIAL_OUTPUT).inputs['Surface'].links[0].is_muted = True
    return material


def dangling_reroute(name):
    """A material whose Surface is fed by a reroute that nothing feeds."""
    material = no_surface(name)
    reroute = material.node_tree.nodes.new('NodeReroute')
    material.node_tree.links.new(reroute.outputs[0], first(material, T.MATERIAL_OUTPUT).inputs['Surface'])
    return material


def layered(name):
    """A Principled BSDF "Top" mixed over a Mix Shader of another one, "Base", further from the output."""
    material = no_surface(name)
    nt = material.node_tree
    top, base = nt.nodes.new(T.PRINCIPLED), nt.nodes.new(T.PRINCIPLED)
    top.name, base.name = "Top", "Base"
    outer, inner = nt.nodes.new('ShaderNodeMixShader'), nt.nodes.new('ShaderNodeMixShader')
    nt.links.new(base.outputs[0], inner.inputs[1])
    nt.links.new(top.outputs[0], outer.inputs[1])
    nt.links.new(inner.outputs[0], outer.inputs[2])
    nt.links.new(outer.outputs[0], first(material, T.MATERIAL_OUTPUT).inputs['Surface'])
    return material


MATERIALS = {
    "no material": None,
    "the default material": default_material,
    "a textured Base Color": textured,
    "nothing on Surface": no_surface,
    "an Emission": emission,
    "split outputs": split,
    "a Displacement": displaced,
    "a framed Principled": framed,
    "a muted Surface link": muted_surface,
    "a reroute nothing feeds": dangling_reroute,
}
# Paint Over has nothing it could paint over on these. See test_refusals.
NOTHING_SHOWN = {"no material", "nothing on Surface", "a muted Surface link", "a reroute nothing feeds"}


def run(**options):
    """Run Add Paint System on the active object: its result, or the error it reported."""
    try:
        return bpy.ops.paint_system.setup_material('EXEC_DEFAULT', **options)
    except RuntimeError as error:
        return str(error)


def add_channel(key):
    try:
        return bpy.ops.paint_system.add_channel('EXEC_DEFAULT', template=key)
    except RuntimeError as error:
        return str(error)


# -- reading the graph -----------------------------------------------------

def link_key(link):
    return (link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier)


def snapshot(material):
    """The node names and link keys of *material*, empty when it has no nodes."""
    if material is None or material.node_tree is None:
        return set(), set()
    nt = material.node_tree
    return {node.name for node in nt.nodes}, {link_key(link) for link in nt.links}


def feeds(output_socket, input_socket):
    return any(link.from_socket == output_socket for link in input_socket.links)


def source(socket):
    return socket.links[0].from_node if socket.is_linked else None


def moved(nt, group, key):
    """Whether the link *key* from before now runs under the paint.

    Its source feeds the group's input for a channel whose output feeds the
    link's old input. Paint Over's old Surface link feeds the paint instead:
    the group, or the Shader to RGB before it.
    """
    from_node, from_socket, to_node, to_socket = key
    for link in group.id_data.links:
        if link.from_node.name != from_node or link.from_socket.identifier != from_socket:
            continue
        if to_socket == 'Surface' and (link.to_node == group or link.to_node.bl_idname == 'ShaderNodeShaderToRGB'):
            return True
        if link.to_node == group and any(
                other.from_node == group and other.from_socket.name == link.to_socket.name
                and other.to_node.name == to_node and other.to_socket.identifier == to_socket
                for other in nt.links):
            return True
    return False


def in_order(*nodes):
    """Whether *nodes* stand left to right."""
    xs = [node_location(node).x for node in nodes]
    return all(a < b for a, b in zip(xs, xs[1:]))


# -- templates on every kind of material -----------------------------------

def check_unlit(label, group, output, canvas=None):
    """The unlit shader shows the group's Color. *canvas* is the base colour Unlit starts from."""
    mix = source(output.inputs['Surface'])
    if not check(mix is not None and mix.bl_idname == 'ShaderNodeMixShader', f"{label}: a Mix Shader feeds Surface"):
        return
    transparent, emitter = source(mix.inputs[1]), source(mix.inputs[2])
    check(transparent is not None and transparent.bl_idname == 'ShaderNodeBsdfTransparent'
          and emitter is not None and emitter.bl_idname == 'ShaderNodeEmission',
          f"{label}: between a Transparent BSDF and an Emission, by socket index")
    check(emitter is not None and feeds(group.outputs['Color'], emitter.inputs['Color'])
          and feeds(group.outputs['Color Alpha'], mix.inputs[0]) and mix.inputs[0].default_value == 1.0,
          f"{label}: the Emission shows Color and Color Alpha picks the shader, with a factor of 1 unlinked")
    check(transparent is not None and in_order(group, transparent, mix, output) and in_order(group, emitter, mix),
          f"{label}: the chain runs left to right into the output")
    if canvas is not None:
        check(close(tuple(group.inputs['Color'].default_value), canvas)
              and abs(group.inputs['Color Alpha'].default_value - canvas[3]) < 1e-6,
              f"{label}: the canvas is the base colour {fmt(group.inputs['Color'].default_value)}")


def check_template(label, template, material, tree, group, old):
    """The graph *template* builds. *old* holds the material's state before Add."""
    nt = material.node_tree
    output = T.material_output(material)
    channels = [channel.name for channel in tree.channels]
    check(channels == (['Normal'] if template == 'NORMAL' else ['Color']), f"{label}: channels {channels}")
    check(output is not None, f"{label}: the material has an output")
    replaced = old.output is not None and old.surface_linked and (
        template in {'UNLIT', 'NORMAL'} or (template == 'PBR' and old.principled is None))
    if replaced:
        check(output.name not in old.nodes and output.target == old.target and output.is_active_output,
              f"{label}: a new active output with the old target takes over from the old one")
        kept = {name for name in ('Volume', 'Displacement', 'Thickness') if name in old.output_links}
        check(all(source(output.inputs[name]) is not None
                  and source(output.inputs[name]).name == old.output_links[name] for name in kept),
              f"{label}: and shows its {sorted(kept)} sources")
    elif old.output is not None and template != 'GROUP':
        check(output.name == old.output.name, f"{label}: the output is the one from before")

    if template == 'UNLIT':
        check_unlit(label, group, output, canvas=(1.0, 1.0, 1.0, 1.0))
    if template == 'PAINT_OVER':
        check_unlit(label, group, output)
        to_rgb = source(group.inputs['Color'])
        check(to_rgb is not None and to_rgb.bl_idname == 'ShaderNodeShaderToRGB'
              and to_rgb.inputs['Shader'].links[0].from_node.name == old.surface
              and feeds(to_rgb.outputs['Alpha'], group.inputs['Color Alpha']),
              f"{label}: the old shader goes through Shader to RGB into Color and Color Alpha")
        check(to_rgb is not None and in_order(to_rgb, group), f"{label}: left of the group")
    if template == 'PBR':
        target = T.find_target(material, group)
        check(target is not None and target.bl_idname == T.PRINCIPLED
              and feeds(group.outputs['Color'], target.inputs['Base Color'])
              and feeds(group.outputs['Color Alpha'], target.inputs['Alpha']),
              f"{label}: Color and its alpha paint into the Principled BSDF")
        check(target is not None and (old.principled is None or target.name == old.principled),
              f"{label}: the Principled from before, when there was one")
        check(target is not None and T.surface_source(material) == target, f"{label}: which feeds Surface")
        check(target is not None and in_order(group, target)
              and abs(node_location(group).y - node_location(target).y) < 1e-3,
              f"{label}: the group sits left of it")
    if template == 'NORMAL':
        diffuse = source(output.inputs['Surface'])
        check(diffuse is not None and diffuse.bl_idname == T.DIFFUSE
              and feeds(group.outputs['Normal'], diffuse.inputs['Normal']),
              f"{label}: the Normal channel paints into a Diffuse BSDF on Surface")
        normal = tree.channels.get('Normal')
        check(normal is not None and normal.vector_kind == 'NORMAL' and normal.paint_space == 'TANGENT',
              f"{label}: in tangent space")
    if template == 'GROUP':
        others = [node for node in nt.nodes if node != group and node.bl_idname != 'NodeFrame']
        check(not any(link.from_node == group or link.to_node == group for link in nt.links),
              f"{label}: the group is not connected")
        check(all(node_location(group).x > node_location(node).x for node in others),
              f"{label}: and stands right of the other nodes")

    feeders = {link.from_node.name: link.from_node for link in nt.links if link.to_node == group}
    check(all(node_location(node).x + node.width <= node_location(group).x for node in feeders.values()),
          f"{label}: the nodes that feed the group stand left of it {sorted(feeders)}")
    principleds = [node.name for node in nt.nodes if node.bl_idname == T.PRINCIPLED]
    if old.material is None and template in {'UNLIT', 'NORMAL'}:
        check(not principleds, f"{label}: the new material's default Principled BSDF is removed")
    lost_nodes = old.nodes - {node.name for node in nt.nodes}
    check(not lost_nodes, f"{label}: every node from before is there {sorted(lost_nodes)}")
    lost_links = [key for key in old.links - {link_key(link) for link in nt.links} if not moved(nt, group, key)]
    check(not lost_links, f"{label}: every link from before is there, or moved under the paint {lost_links}")
    check(all(link.is_valid for link in nt.links), f"{label}: every link is valid")


def state_before(material):
    nodes, links = snapshot(material)
    output = T.material_output(material)
    surface = T.surface_source(material)
    principled = T._nearest_shader(material, (T.PRINCIPLED,)) if output is not None else None
    output_links = {socket.name: socket.links[0].from_node.name
                    for socket in output.inputs if socket.is_linked} if output is not None else {}
    return SimpleNamespace(material=material, nodes=nodes, links=links, output=output,
                           target=output.target if output is not None else 'ALL',
                           surface=surface.name if surface is not None else None,
                           surface_linked=output is not None and output.inputs['Surface'].is_linked,
                           principled=principled.name if principled is not None else None,
                           output_links=output_links)


def test_templates():
    section("every template keeps the material's nodes and links, and builds its own graph")
    scene.render.engine = EEVEE
    for kind, build in MATERIALS.items():
        for template in TEMPLATES:
            if template == 'PAINT_OVER' and kind in NOTHING_SHOWN:
                continue
            name = f"PS {template} on {kind}"
            material = build(name) if build is not None else None
            obj = new_object(name, material)
            old = state_before(material)
            label = f"{template} on {kind}"
            if not check(run(template=template) == {'FINISHED'}, f"{label}: Add finishes"):
                continue
            material = obj.active_material
            tree = material.paint_system.tree
            group = find_material_group_node(material, tree)
            check(tree is not None and group is not None and group.node_tree == tree.compiled
                  and scene.paint_system.active_node_tree == tree,
                  f"{label}: the material runs a new tree, which is the active one")
            if group is not None:
                check_template(label, template, material, tree, group, old)

    if before(5):
        section("before 5.0, Add turns a material's nodes on")
        material = default_material("PS Nodes Off")
        material.use_nodes = False
        new_object("PS Nodes Off", material)
        run(template='PBR')
        group = find_material_group_node(material, material.paint_system.tree)
        check(material.use_nodes and group is not None
              and feeds(group.outputs['Color'], first(material, T.PRINCIPLED).inputs['Base Color']),
              "and paints into its Principled BSDF")


def test_muted_links():
    section("a muted link stays muted under the paint, so the material still looks as it did")
    scene.render.engine = EEVEE
    material = textured("PS Muted Texture")
    principled = first(material, T.PRINCIPLED)
    principled.inputs['Base Color'].links[0].is_muted = True
    principled.inputs['Base Color'].default_value = (0.0, 1.0, 0.0, 1.0)
    new_object("PS Muted Texture", material)
    run(template='PBR')
    group = find_material_group_node(material, material.paint_system.tree)
    moved_link = group.inputs['Color'].links[0] if group.inputs['Color'].is_linked else None
    check(moved_link is not None and moved_link.from_node.bl_idname == 'ShaderNodeTexImage' and moved_link.is_muted
          and close(tuple(group.inputs['Color'].default_value), (0.0, 1.0, 0.0, 1.0)),
          "the texture's muted link moves onto the group's Color, muted, with the green that showed")
    check(feeds(group.outputs['Color'], principled.inputs['Base Color'])
          and not principled.inputs['Base Color'].links[0].is_muted, "and the paint feeds Base Color")

    material = displaced("PS Muted Displacement")
    first(material, T.MATERIAL_OUTPUT).inputs['Displacement'].links[0].is_muted = True
    new_object("PS Muted Displacement", material)
    run(template='UNLIT')
    output = T.material_output(material)
    check(output.name != "Material Output" and output.inputs['Displacement'].is_linked
          and output.inputs['Displacement'].links[0].is_muted,
          "the new output's copy of a muted Displacement link is muted too")


def test_refusals():
    section("a template that cannot run changes nothing")

    def counts():
        return {name: len(getattr(bpy.data, name)) for name in ('materials', 'node_groups', 'images', 'objects')}

    scene.render.engine = 'CYCLES'
    material = default_material("PS Refused")
    new_object("PS Refused", material)
    was, data = snapshot(material), counts()
    result = run(template='PAINT_OVER')
    check(T.PAINT_OVER_NEEDS in str(result), f"Paint Over without EEVEE is refused ({result})")
    check(snapshot(material) == was and counts() == data and material.paint_system.tree is None,
          "and bpy.data and the material are unchanged")
    result = run(template='PBR', add_color=False)
    check(T.NO_CHANNEL in str(result) and counts() == data, f"PBR without a channel is refused ({result})")

    scene.render.engine = EEVEE
    bare = new_object("PS Refused Bare")
    data = counts()
    result = run(template='PAINT_OVER')
    check(T.PAINT_OVER_NEEDS in str(result) and len(bare.material_slots) == 0 and counts() == data,
          f"Paint Over on an object without a material makes no material ({result})")
    for kind in ("nothing on Surface", "a muted Surface link", "a reroute nothing feeds"):
        material = MATERIALS[kind](f"PS Refused {kind}")
        new_object(f"PS Refused {kind}", material)
        data = counts()
        result = run(template='PAINT_OVER')
        check(T.PAINT_OVER_NEEDS in str(result) and counts() == data, f"nor on a material with {kind}")


def test_paint_over_sources():
    section("Paint Over keeps a transparent shader transparent, and takes a colour as it is")
    scene.render.engine = EEVEE
    material = no_surface("PS Over Transparent")
    nt = material.node_tree
    transparent = nt.nodes.new('ShaderNodeBsdfTransparent')
    nt.links.new(transparent.outputs[0], first(material, T.MATERIAL_OUTPUT).inputs['Surface'])
    new_object("PS Over Transparent", material)
    run(template='PAINT_OVER')
    group = find_material_group_node(material, material.paint_system.tree)
    to_rgb = source(group.inputs['Color'])
    check(to_rgb is not None and source(to_rgb.inputs['Shader']) == transparent
          and feeds(to_rgb.outputs['Alpha'], group.inputs['Color Alpha']),
          "a Transparent BSDF's alpha reaches Color Alpha through Shader to RGB")

    material = no_surface("PS Over Colour")
    nt = material.node_tree
    rgb = nt.nodes.new('ShaderNodeRGB')
    nt.links.new(rgb.outputs[0], first(material, T.MATERIAL_OUTPUT).inputs['Surface'])
    new_object("PS Over Colour", material)
    run(template='PAINT_OVER')
    group = find_material_group_node(material, material.paint_system.tree)
    check(source(group.inputs['Color']) == rgb and not group.inputs['Color Alpha'].is_linked
          and group.inputs['Color Alpha'].default_value == 1.0,
          "a colour goes straight into Color, with Color Alpha 1")
    check(not any(node.bl_idname == 'ShaderNodeShaderToRGB' for node in nt.nodes), "without a Shader to RGB")


# -- renders ---------------------------------------------------------------

RENDER_SIZE = 16


def render(obj):
    """Render *obj* alone from above with Cycles, lit by a sun, and return its RGBA pixels."""
    lens = bpy.data.cameras.new("PS Template Camera")
    lens.type = 'ORTHO'
    lens.ortho_scale = 1.0
    camera = bpy.data.objects.new("PS Template Camera", lens)
    camera.location = (0.5, 0.5, 5.0)
    light = bpy.data.lights.new("PS Template Sun", 'SUN')
    light.energy = 3.0
    sun = bpy.data.objects.new("PS Template Sun", light)
    sun.rotation_euler = (0.4, 0.2, 0.0)
    for helper in (camera, sun):
        scene.collection.objects.link(helper)
    previous = scene.camera, scene.render.engine
    scene.camera = camera
    hidden = {other: other.hide_render for other in scene.objects if other.type == 'MESH'}
    for other in hidden:
        other.hide_render = other != obj
    settings = scene.render
    settings.engine = 'CYCLES'
    scene.cycles.samples = 4
    scene.cycles.device = 'CPU'
    scene.cycles.use_denoising = False
    scene.cycles.pixel_filter_type = 'BOX'
    scene.cycles.filter_width = 0.01
    settings.resolution_x = settings.resolution_y = RENDER_SIZE
    settings.resolution_percentage = 100
    settings.film_transparent = True
    settings.image_settings.file_format = 'OPEN_EXR'
    settings.image_settings.color_depth = '32'
    try:
        with tempfile.TemporaryDirectory() as folder:
            settings.filepath = os.path.join(folder, "render.exr")
            bpy.ops.render.render(write_still=True)
            image = bpy.data.images.load(settings.filepath, check_existing=False)
            pixels = np.empty(RENDER_SIZE * RENDER_SIZE * 4, dtype=np.float32)
            image.pixels.foreach_get(pixels)
            bpy.data.images.remove(image)
        return pixels.reshape(-1, 4)
    finally:
        for other, value in hidden.items():
            other.hide_render = value
        scene.camera, settings.engine = previous
        bpy.data.objects.remove(camera)
        bpy.data.objects.remove(sun)
        bpy.data.cameras.remove(lens)
        bpy.data.lights.remove(light)


def test_pbr_channels():
    section("PBR connects every channel it makes, and the material renders as before")
    scene.render.engine = 'CYCLES'
    material = textured("PS PBR All")
    obj = new_object("PS PBR All", material)
    target = first(material, T.PRINCIPLED)
    texture = first(material, 'ShaderNodeTexImage')
    look = render(obj)
    result = run(template='PBR', add_metallic=True, add_roughness=True, add_normal=True,
                 start_with='IMAGE', resolution='1024')
    check(result == {'FINISHED'}, f"Add finishes ({result})")
    tree = material.paint_system.tree
    group = find_material_group_node(material, tree)
    check([channel.name for channel in tree.channels] == ['Color', 'Metallic', 'Roughness', 'Normal'],
          f"the four channels, in order {[channel.name for channel in tree.channels]}")
    for socket, name in (('Base Color', 'Color'), ('Alpha', 'Color Alpha'), ('Metallic', 'Metallic'),
                         ('Roughness', 'Roughness'), ('Normal', 'Normal')):
        check(feeds(group.outputs[name], target.inputs[socket]), f"{name} paints into {socket}")
    check(source(group.inputs['Color']) == texture, "the texture is now the base Color starts from")
    check(abs(group.inputs['Metallic'].default_value - 0.25) < 1e-6
          and abs(group.inputs['Roughness'].default_value - 0.3) < 1e-6
          and group.inputs['Color Alpha'].default_value == 1.0,
          "Metallic, Roughness and Alpha keep their values as the base")
    ranged = [tree.channels[name] for name in ('Metallic', 'Roughness')]
    check(all(ch.use_range and ch.range_min == 0.0 and ch.range_max == 1.0 for ch in ranged),
          "Metallic and Roughness are limited to 0..1")
    layer = tree.nodes.active
    check(tree.active_channel.name == 'Color' and layer is not None and layer.bl_idname == IMAGE_LAYER
          and layer.image is not None and layer.image.size[0] == 1024
          and layer.image.colorspace_settings.name == 'sRGB',
          "Color is active, with a 1024 sRGB image layer")
    check(scene.tool_settings.image_paint.canvas == layer.image, "which is the paint canvas")
    after = render(obj)
    check(float(look[:, :3].std()) > 0.01, "the render shows the texture's gradient, so a match means something")
    covered = look[:, 3] > 0.99
    error = float(np.abs(after[covered, :3] - look[covered, :3]).max()) if covered.any() else 1.0
    check(covered.all() and error < 0.01, f"the render matches the one from before Add (largest error {error:.4f})")


def test_unlit_render():
    section("Unlit shows its canvas colour, unlit, and a clear canvas is transparent")
    obj = new_object("PS Unlit Render")
    check(run(template='UNLIT', canvas=(0.8, 0.2, 0.1, 1.0)) == {'FINISHED'}, "Add finishes")
    pixels = render(obj)
    check(np.allclose(pixels[:, :3], (0.8, 0.2, 0.1), atol=0.01) and np.allclose(pixels[:, 3], 1.0, atol=0.01),
          f"every pixel is the canvas colour {fmt(pixels[0])}")
    obj = new_object("PS Unlit Clear")
    run(template='UNLIT', canvas=(1.0, 1.0, 1.0, 0.0))
    pixels = render(obj)
    check(np.allclose(pixels[:, 3], 0.0, atol=0.01), f"a clear canvas renders transparent {fmt(pixels[0])}")


# -- the recommendation and the defaults it brings -------------------------

def test_recommend():
    section("the dialog starts on the template the material suits, and says why")
    mixed = default_material("PS Recommend Mixed")
    nt = mixed.node_tree
    mix = nt.nodes.new('ShaderNodeMixShader')
    nt.links.new(first(mixed, T.PRINCIPLED).outputs[0], mix.inputs[1])
    nt.links.new(mix.outputs[0], first(mixed, T.MATERIAL_OUTPUT).inputs['Surface'])

    def rerouted(name, muted=False):
        """The default material with a reroute before Surface, whose link into Surface is *muted* or not."""
        material = default_material(name)
        nt = material.node_tree
        reroute = nt.nodes.new('NodeReroute')
        nt.links.new(first(material, T.PRINCIPLED).outputs[0], reroute.inputs[0])
        nt.links.new(reroute.outputs[0], first(material, T.MATERIAL_OUTPUT).inputs['Surface']).is_muted = muted
        return material
    has_principled = "Picked: the material has a Principled BSDF."
    cases = [
        ("no material", None, EEVEE, 'UNLIT', "Picked: the object has no material yet."),
        ("the default material", default_material("PS Recommend Default"), EEVEE, 'PBR', has_principled),
        ("the default material in Cycles", default_material("PS Recommend Cycles"), 'CYCLES', 'PBR', has_principled),
        # In EEVEE a shader other than a Principled would get Paint Over.
        ("a reroute before the Principled", rerouted("PS Recommend Reroute"), EEVEE, 'PBR', has_principled),
        ("a muted link out of a reroute", rerouted("PS Recommend Muted Reroute", muted=True), EEVEE, 'UNLIT',
         "Picked: nothing feeds the Material Output."),
        ("a muted Surface link", muted_surface("PS Recommend Muted"), EEVEE, 'UNLIT',
         "Picked: nothing feeds the Material Output."),
        ("a reroute nothing feeds", dangling_reroute("PS Recommend Dangling"), EEVEE, 'UNLIT',
         "Picked: nothing feeds the Material Output."),
        ("an Emission in EEVEE", emission("PS Recommend Emission"), EEVEE, 'PAINT_OVER',
         "Picked: the material has its own shader."),
        ("an Emission in Cycles", emission("PS Recommend Emission 2"), 'CYCLES', 'GROUP',
         "Picked: no Principled BSDF to paint into."),
        ("a Principled under a Mix Shader in Cycles", mixed, 'CYCLES', 'PBR', has_principled),
        ("a Principled under a Mix Shader in EEVEE", mixed, EEVEE, 'PAINT_OVER',
         "Picked: the material has its own shader."),
        ("nothing on Surface", no_surface("PS Recommend Empty"), EEVEE, 'UNLIT',
         "Picked: nothing feeds the Material Output."),
    ]
    if before(5):
        cases.append(("a material without nodes", bpy.data.materials.new("PS Recommend Bare"), EEVEE, 'UNLIT',
                      "Picked: the material has no nodes yet."))
    for label, material, engine, template, reason in cases:
        scene.render.engine = engine
        got = T.recommend(material, scene)
        check(got == (template, reason), f"{label}: {got}")
        check(T.paint_over_possible(material, scene) == (engine == EEVEE and T.surface_source(material) is not None),
              f"{label}: Paint Over can run only with EEVEE and a Surface source")

    section("a script that names no template gets the recommended one and its defaults")
    scene.render.engine = EEVEE
    # Blender keeps this look when the view switches to Standard; an AgX look it resets.
    contrast = ('Filmic', 'Very High Contrast')
    set_view(*contrast)
    material = default_material("PS Script PBR")
    new_object("PS Script PBR", material)
    run()
    group = find_material_group_node(material, material.paint_system.tree)
    check(group is not None and feeds(group.outputs['Color'], first(material, T.PRINCIPLED).inputs['Base Color']),
          "the default material gets PBR")
    check(not material.use_backface_culling and view() == contrast,
          "which keeps back faces and the view transform")
    material = default_material("PS Script Culling")
    new_object("PS Script Culling", material)
    run(use_backface_culling=True)
    check(material.use_backface_culling and view() == contrast,
          "an option the script sets wins over the recommended template's default")
    obj = new_object("PS Script Unlit")
    run()
    check(obj.active_material is not None and obj.active_material.name == "PS Script Unlit Material"
          and obj.active_material.use_backface_culling and not obj.active_material.use_transparency_overlap,
          "an object without a material gets a new Unlit one that hides back faces")
    check(view() == ('Standard', 'None'), f"and the view switches to Standard, with no look {view()}")
    set_view(*contrast)
    obj = new_object("PS Script Unlit Options")
    run(template='UNLIT', use_backface_culling=False, use_smooth_transparency=True)
    check(not obj.active_material.use_backface_culling and obj.active_material.surface_render_method == 'BLENDED'
          and view() == ('Standard', 'None'),
          "an option passed along with the template wins over the template's default")
    check(not any(node.bl_idname == IMAGE_LAYER for node in obj.active_material.paint_system.tree.nodes),
          "and a script starts with no layer")

    section("the UV Map option reaches the first layer and the Normal channel")
    obj = new_object("PS Script UV", uv_maps=("UVMap", "Detail"))
    run(template='NORMAL', start_with='IMAGE', uv_map="Detail")
    tree = obj.active_material.paint_system.tree
    layer = tree.nodes.active
    check(layer is not None and layer.bl_idname == IMAGE_LAYER and layer.uv_map == "Detail"
          and tree.channels['Normal'].tangent_uv_map == "Detail",
          "the image layer and the Normal channel's tangent space use the UV map picked")

    section("a new material goes into the active slot")
    obj = new_object("PS Script Slots")
    obj.data.materials.append(None)
    obj.data.materials.append(None)
    obj.active_material_index = 1
    run(template='UNLIT')
    check(obj.material_slots[0].material is None and obj.material_slots[1].material is not None
          and len(obj.material_slots) == 2,
          "the second, active slot of two empty ones gets it, and no slot is added")

    section("an empty parented to a mesh adds to the mesh's material")
    mesh_obj = new_object("PS Parent Mesh", default_material("PS Parent Material"))
    empty = bpy.data.objects.new("PS Parent Empty", None)
    scene.collection.objects.link(empty)
    empty.parent = mesh_obj
    activate(empty)
    run()
    check(mesh_obj.active_material.paint_system.tree is not None, "the mesh's material gets the tree")
    activate(mesh_obj)


def set_view(transform, look):
    scene.view_settings.view_transform = transform
    scene.view_settings.look = look


def view():
    return scene.view_settings.view_transform, scene.view_settings.look


def test_standard_view_while_previewing():
    section("while a channel preview shows, Standard goes to the display the preview puts back")
    scene.render.engine = EEVEE
    set_view('AgX', 'AgX - Punchy')
    material = default_material("PS View Preview")
    previewed = new_object("PS View Preview", material)
    run(add_roughness=True)
    tree = material.paint_system.tree
    # A Non-Color channel previews in Raw, so the preview's view differs from Standard.
    tree.active_channel_index = 1
    bpy.ops.paint_system.preview_channel()
    display = scene.paint_system.preview_display
    shown = view()
    check(tree.preview_channel and display.is_saved and shown == ('Raw', 'None'),
          f"the Roughness preview shows in Raw, and holds the scene's display {shown}")
    check(T.standard_view_applies(scene), "the scene's own view is not Standard, so the option applies")
    new_object("PS View Preview Unlit")
    run(template='UNLIT')
    check(view() == shown and display.view_transform == 'Standard' and display.look == 'None',
          f"the preview's view stays, and the saved one becomes Standard {view()}")
    check(not T.standard_view_applies(scene), "after which the option no longer applies")
    activate(previewed)
    bpy.ops.paint_system.preview_channel()
    check(view() == ('Standard', 'None'), f"the view is Standard once the preview ends {view()}")
    set_view('AgX', 'None')

    section("a view the user picked while previewing switches to Standard at once")
    # Color previews in Standard, Roughness in Raw.
    for index, applied in ((0, 'Standard'), (1, 'Raw')):
        tree.active_channel_index = index
        activate(previewed)
        bpy.ops.paint_system.preview_channel()
        check(view()[0] == applied, f"the {tree.active_channel.name} preview shows in {view()[0]}")
        set_view('Filmic', 'None')
        new_object(f"PS View Picked {applied}")
        run(template='UNLIT')
        check(view() == ('Standard', 'None'), f"the picked view becomes Standard {view()}")
        activate(previewed)
        bpy.ops.paint_system.preview_channel()
        check(view() == ('Standard', 'None'), f"and stays Standard once the preview ends {view()}")
        set_view('AgX', 'None')


# -- Add Channel -----------------------------------------------------------

def test_add_channel():
    section("Add Channel makes a template's channel and connects it in every material that runs the tree")
    scene.render.engine = EEVEE
    first_material = default_material("PS Channel First")
    first(first_material, T.PRINCIPLED).inputs['Roughness'].default_value = 0.35
    obj = new_object("PS Channel First", first_material)
    run(template='PBR')
    tree = first_material.paint_system.tree
    second_material = default_material("PS Channel Second")
    first(second_material, T.PRINCIPLED).inputs['Roughness'].default_value = 0.6
    node_tree_ops.link_tree_to_material(second_material, tree)
    check(add_channel('ROUGHNESS') == {'FINISHED'}, "Roughness is added")
    rough = tree.channels.get('Roughness')
    check(rough is not None and rough.use_range and tree.active_channel == rough, "limited to 0..1, and active")
    for material, value in ((first_material, 0.35), (second_material, 0.6)):
        group = find_material_group_node(material, tree)
        principled = first(material, T.PRINCIPLED)
        check(feeds(group.outputs['Roughness'], principled.inputs['Roughness'])
              and abs(group.inputs['Roughness'].default_value - value) < 1e-6,
              f"{material.name}: into the Principled's Roughness, which keeps its value {value}")
    result = add_channel('ROUGHNESS')
    check('already has a "Roughness" channel' in str(result), f"a second Roughness is refused ({result})")

    section("Add Channel while a preview shows connects to the material's own shader")
    activate(obj)
    bpy.ops.paint_system.preview_channel()
    check(add_channel('METALLIC') == {'FINISHED'}, "Metallic is added during the preview")
    bpy.ops.paint_system.preview_channel()
    group = find_material_group_node(first_material, tree)
    previews = [node for node in first_material.node_tree.nodes if node.get(PREVIEW_TREE_KEY) is not None]
    check(feeds(group.outputs['Metallic'], first(first_material, T.PRINCIPLED).inputs['Metallic']) and not previews,
          "into the Principled, and the preview's output is gone once it ends")
    check(all(link.is_valid for link in first_material.node_tree.links), "every link is valid")

    section("after Paint Over the material's shader feeds the paint, so a new channel stays unconnected")
    material = default_material("PS Channel Over")
    new_object("PS Channel Over", material)
    run(template='PAINT_OVER')
    tree = material.paint_system.tree
    check(add_channel('ROUGHNESS') == {'FINISHED'} and tree.channels.get('Roughness') is not None,
          "Roughness is added")
    group = find_material_group_node(material, tree)
    check(not group.outputs['Roughness'].is_linked and not first(material, T.PRINCIPLED).inputs['Roughness'].is_linked,
          "and not connected, which would make a loop")
    check(all(link.is_valid for link in material.node_tree.links), "every link stays valid")

    section("Color on a Diffuse BSDF has no alpha, so a half-transparent stroke mixes with the base")
    obj = new_object("PS Channel Diffuse")
    run(template='NORMAL')
    material = obj.active_material
    tree = material.paint_system.tree
    diffuse = first(material, T.DIFFUSE)
    diffuse.inputs['Color'].default_value = (0.5, 0.5, 0.5, 1.0)
    check(add_channel('COLOR') == {'FINISHED'}, "Color is added")
    color = tree.channels.get('Color')
    group = find_material_group_node(material, tree)
    check(color is not None and not color.use_alpha and 'Color Alpha' not in group.outputs,
          "without alpha, as the Diffuse BSDF has no Alpha input")
    check(feeds(group.outputs['Color'], diffuse.inputs['Color'])
          and close(tuple(group.inputs['Color'].default_value), (0.5, 0.5, 0.5, 1.0)),
          "into the Diffuse's Color, whose value is the base")
    layer = tree.insert_layer_node(SOLID_LAYER, "Color")
    layer.fill_color = (1.0, 0.0, 0.0, 1.0)
    layer.opacity = 0.5
    rgba = bake_group(tree.compiled, color="Color", alpha="Color", inputs={"Color": (0.5, 0.5, 0.5, 1.0)})
    check(close(tuple(rgba[0][:3]), (0.75, 0.25, 0.25)),
          f"red at opacity 0.5 over 0.5 grey gives (0.75, 0.25, 0.25): {fmt(rgba[0][:3])}")

    section("a shader node that feeds the paint is passed over for the next one")
    material = default_material("PS Channel Passed")
    nt = material.node_tree
    new_object("PS Channel Passed", material)
    run(template='GROUP')
    group = find_material_group_node(material, material.paint_system.tree)
    # The Principled BSDF becomes the base the paint starts from, and the
    # paint shows through a Diffuse BSDF.
    to_rgb = nt.nodes.new('ShaderNodeShaderToRGB')
    diffuse = nt.nodes.new(T.DIFFUSE)
    nt.links.new(first(material, T.PRINCIPLED).outputs[0], to_rgb.inputs['Shader'])
    nt.links.new(to_rgb.outputs['Color'], group.inputs['Color'])
    nt.links.new(group.outputs['Color'], diffuse.inputs['Color'])
    nt.links.new(diffuse.outputs[0], first(material, T.MATERIAL_OUTPUT).inputs['Surface'])
    check(add_channel('NORMAL') == {'FINISHED'}
          and feeds(find_material_group_node(material, material.paint_system.tree).outputs['Normal'],
                    diffuse.inputs['Normal']),
          "Normal paints into the Diffuse, not the Principled that feeds the group")

    section("Color keeps its alpha when one material can show it, and starts opaque where none can")
    obj = new_object("PS Channel Mixed")
    run(template='NORMAL')
    diffuse_material = obj.active_material
    tree = diffuse_material.paint_system.tree
    first(diffuse_material, T.DIFFUSE).inputs['Color'].default_value = (0.5, 0.5, 0.5, 1.0)
    principled_material = default_material("PS Channel Mixed Principled")
    node_tree_ops.link_tree_to_material(principled_material, tree)
    activate(obj)
    check(add_channel('COLOR') == {'FINISHED'} and tree.channels['Color'].use_alpha, "Color is added with alpha")
    group = find_material_group_node(principled_material, tree)
    check(feeds(group.outputs['Color Alpha'], first(principled_material, T.PRINCIPLED).inputs['Alpha']),
          "which paints into the Principled's Alpha")
    group = find_material_group_node(diffuse_material, tree)
    check(not group.inputs['Color Alpha'].is_linked and group.inputs['Color Alpha'].default_value == 1.0,
          "and starts at 1 in the Diffuse material, which cannot show it")
    layer = tree.insert_layer_node(SOLID_LAYER, "Color")
    layer.fill_color = (1.0, 0.0, 0.0, 1.0)
    layer.opacity = 0.5
    rgba = bake_group(tree.compiled, inputs={"Color": tuple(group.inputs['Color'].default_value),
                                             "Color Alpha": group.inputs['Color Alpha'].default_value})
    check(close(tuple(rgba[0][:3]), (0.75, 0.25, 0.25)),
          f"so red at opacity 0.5 mixes with the grey there: {fmt(rgba[0][:3])}")

    section("an input another channel already paints into is left alone, and not counted")
    for label, tint in (("straight", False), ("through a Hue/Saturation node", True)):
        material = default_material(f"PS Channel Taken {label}")
        new_object(material.name, material)
        run(template='PBR')
        tree = material.paint_system.tree
        tree.channels['Color'].name = "Albedo"
        core.flush_now()
        nt = material.node_tree
        group = find_material_group_node(material, tree)
        principled = first(material, T.PRINCIPLED)
        if tint:
            hue = nt.nodes.new('ShaderNodeHueSaturation')
            nt.links.new(group.outputs['Albedo'], hue.inputs['Color'])
            nt.links.new(hue.outputs[0], principled.inputs['Base Color'])
            nt.links.remove(principled.inputs['Alpha'].links[0])
        channel, connected = T.add_template_channel(tree, 'COLOR')
        group = find_material_group_node(material, tree)
        check(connected == 0 and not group.outputs['Color'].is_linked
              and source(principled.inputs['Base Color']) == (hue if tint else group),
              f"Base Color fed {label} by Albedo stays so, and Color counts as not connected")
        alpha = source(principled.inputs['Alpha'])
        check(alpha is None if tint else feeds(group.outputs['Albedo Alpha'], principled.inputs['Alpha']),
              f"and Alpha is not moved under Color ({alpha.name if alpha is not None else None})")

    section("an Emission of the material's own is not painted into")
    mixed = emission("PS Channel Mixed Emission")
    nt = mixed.node_tree
    mix = nt.nodes.new(T.MIX_SHADER)
    nt.links.new(nt.nodes.new('ShaderNodeBsdfGlossy').outputs[0], mix.inputs[1])
    nt.links.new(first(mixed, 'ShaderNodeEmission').outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], first(mixed, T.MATERIAL_OUTPUT).inputs['Surface'])
    for label, material in (("alone", emission("PS Channel Own Emission")), ("mixed over a Glossy BSDF", mixed)):
        tree = bpy.data.node_groups.new(material.name, 'PaintSystemNodeTree')
        tree.initialize(add_channel=False)
        node_tree_ops.link_tree_to_material(material, tree)
        channel, connected = T.add_template_channel(tree, 'COLOR')
        check(connected == 0 and not first(material, 'ShaderNodeEmission').inputs['Color'].is_linked,
              f"{label}: only the Emission of an unlit shader, over a Transparent BSDF, is")

    section("the Principled BSDF nearest the output is painted into")
    scene.render.engine = 'CYCLES'
    material = layered("PS Channel Layered")
    new_object("PS Channel Layered", material)
    run(template='PBR')
    tree = material.paint_system.tree
    group = find_material_group_node(material, tree)
    add_channel('ROUGHNESS')
    top = material.node_tree.nodes["Top"]
    check(feeds(group.outputs['Color'], top.inputs['Base Color'])
          and feeds(group.outputs['Roughness'], top.inputs['Roughness'])
          and not material.node_tree.nodes["Base"].inputs['Base Color'].is_linked,
          "Top, not Base, for the template and for Add Channel")
    scene.render.engine = EEVEE

    section("a tree no material runs only gets the channel")
    nested = bpy.data.node_groups.new("PS Channel Nested", 'PaintSystemNodeTree')
    nested.initialize()
    channel, connected = T.add_template_channel(nested, 'METALLIC')
    check(channel.name == 'Metallic' and connected == 0, "Metallic, connected nowhere")


def test_reconnect():
    section("a material whose group node was deleted is connected again")
    scene.render.engine = EEVEE
    material = default_material("PS Reconnect")
    first(material, T.PRINCIPLED).inputs['Roughness'].default_value = 0.4
    new_object("PS Reconnect", material)
    run(template='PBR', add_roughness=True)
    tree = material.paint_system.tree
    check(not bpy.ops.paint_system.setup_material.poll(), "the button is off while the material runs its tree")
    material.node_tree.nodes.remove(find_material_group_node(material, tree))
    check(bpy.ops.paint_system.setup_material.poll(), "and on again once its group node is gone")
    trees = len(bpy.data.node_groups)
    check(run() == {'FINISHED'} and len(bpy.data.node_groups) == trees, "Add finishes without a new tree")
    group = find_material_group_node(material, tree)
    principled = first(material, T.PRINCIPLED)
    check(group is not None and feeds(group.outputs['Color'], principled.inputs['Base Color'])
          and feeds(group.outputs['Roughness'], principled.inputs['Roughness'])
          and abs(group.inputs['Roughness'].default_value - 0.4) < 1e-6,
          "the group node is back, with Color and Roughness connected, and Roughness keeps its value")
    check(group is not None and in_order(group, principled), "left of the Principled")
    check(not bpy.ops.paint_system.setup_material.poll(), "and the button is off again")

    section("a colour channel without alpha leaves Alpha alone, whatever the other channels are called")
    tree.channels['Color'].use_alpha = False
    # This channel's input has the name Color's alpha input would have.
    tree.create_channel("Color Alpha", 'FLOAT')
    material.node_tree.nodes.remove(find_material_group_node(material, tree))
    run()
    check(find_material_group_node(material, tree) is not None and not principled.inputs['Alpha'].is_linked,
          "the Principled's Alpha is not linked after connecting again")

    section("Unlit and Paint Over connect again through their unlit shader")
    for template in ('UNLIT', 'PAINT_OVER'):
        material = default_material(f"PS Reconnect {template}")
        new_object(material.name, material)
        run(template=template)
        tree = material.paint_system.tree
        nt = material.node_tree
        mix = source(T.material_output(material).inputs['Surface'])
        emitter = source(mix.inputs[2])
        to_rgb = next((node for node in nt.nodes if node.bl_idname == T.SHADER_TO_RGB), None)
        # A Shader to RGB of the material's own feeds a node, so it is not one Paint Over left behind.
        own = nt.nodes.new(T.SHADER_TO_RGB)
        nt.links.new(nt.nodes.new('ShaderNodeBsdfGlossy').outputs[0], own.inputs['Shader'])
        nt.links.new(own.outputs['Color'], nt.nodes.new('ShaderNodeRGBCurve').inputs['Color'])
        nt.nodes.remove(find_material_group_node(material, tree))
        reports = reconnect()
        group = find_material_group_node(material, tree)
        check(group is not None and feeds(group.outputs['Color'], emitter.inputs['Color'])
              and feeds(group.outputs['Color Alpha'], mix.inputs[0]) and in_order(group, emitter),
              f"{template}: the group node is back left of the Emission, with Color and its alpha connected")
        if template == 'PAINT_OVER':
            check(group is not None and feeds(to_rgb.outputs['Color'], group.inputs['Color'])
                  and feeds(to_rgb.outputs['Alpha'], group.inputs['Color Alpha']) and in_order(to_rgb, group),
                  f"{template}: the Shader to RGB left behind feeds it again")
        else:
            check(group is not None and not group.inputs['Color'].is_linked,
                  f"{template}: a Shader to RGB of the material's own is left alone")
        check(reports == [({'INFO'}, f'Connected "{tree.name}" to "{material.name}" again')],
              f"{template}: and says so {reports}")
        check(all(link.is_valid for link in nt.links), f"{template}: every link is valid")

    section("a group node with nothing to connect to says so")
    material = emission("PS Reconnect Nothing")
    new_object(material.name, material)
    run(template='GROUP')
    tree = material.paint_system.tree
    material.node_tree.nodes.remove(find_material_group_node(material, tree))
    reports = reconnect()
    check(find_material_group_node(material, tree) is not None and len(reports) == 1
          and reports[0][0] == {'WARNING'} and "found no shader node" in reports[0][1],
          f"the node is back, and a warning says it is not connected {reports}")

    section("a group node deleted during a channel preview shows the preview again")
    material = default_material("PS Reconnect Preview")
    new_object(material.name, material)
    run(template='PBR')
    tree = material.paint_system.tree
    bpy.ops.paint_system.preview_channel()
    material.node_tree.nodes.remove(find_material_group_node(material, tree))
    run()
    group = find_material_group_node(material, tree)
    active = material.node_tree.get_output_node('EEVEE')
    check(active.get(PREVIEW_TREE_KEY) == tree.uuid and feeds(group.outputs[PREVIEW_OUTPUT], active.inputs['Surface'])
          and feeds(group.outputs['Color'], first(material, T.PRINCIPLED).inputs['Base Color']),
          "the preview's output shows the new group node, which paints into the Principled too")
    bpy.ops.paint_system.preview_channel()
    check(material.node_tree.get_output_node('EEVEE').name == "Material Output", "until the preview ends")
    set_view('AgX', 'None')

    section("a copied material and tree run the copy, not the tree they were copied from")
    material = default_material("PS Copy")
    obj = new_object("PS Copy", material)
    run(template='PBR')
    tree = material.paint_system.tree
    copy_material, copy_tree = material.copy(), tree.copy()
    copy_material.paint_system.tree = copy_tree
    core.mark_dirty()
    core.flush_now()
    copy_group = find_material_group_node(copy_material, copy_tree)
    check(copy_tree.uuid != tree.uuid and copy_group is not None and copy_group.node_tree == copy_tree.compiled
          and copy_tree.compiled not in (None, tree.compiled),
          "the copy's group node runs the copy's own shader")
    activate(obj)
    channel, connected = T.add_template_channel(tree, 'ROUGHNESS')
    check(connected == 1 and not first(copy_material, T.PRINCIPLED).inputs['Roughness'].is_linked
          and copy_group.node_tree == copy_tree.compiled,
          "so a channel added to the first tree leaves the copy alone")


class Reconnect(SimpleNamespace):
    """Stands in for the operator connecting a material again, and records its reports."""
    execute = SETUP.execute
    _finish = SETUP._finish


def reconnect():
    reports = []
    Reconnect(report=lambda kind, text: reports.append((kind, text))).execute(bpy.context)
    return reports


class Setup(Reconnect):
    """Stands in for Add Paint System as its button calls it, through invoke."""
    invoke = SETUP.invoke


ADD_CHANNEL = import_from("ops.channel_ops").PAINTSYSTEM_OT_add_channel


class AddChannel(SimpleNamespace):
    """Stands in for Add Channel as its menu calls it, through invoke."""
    invoke = ADD_CHANNEL.invoke
    execute = ADD_CHANNEL.execute


class DialogContext:
    """bpy.context, but its window manager records the dialog it is asked for instead of opening it.

    ``invoke`` only runs with a window, so the tests call it on stand-ins
    with this context.
    """

    def __init__(self):
        self.dialogs = []
        self.window_manager = SimpleNamespace(invoke_props_dialog=self._dialog)

    def _dialog(self, operator, **kwargs):
        self.dialogs.append(kwargs)
        return {'RUNNING_MODAL'}

    def __getattr__(self, name):
        return getattr(bpy.context, name)


def ignore_report(kind, text):
    """A stand-in's report, where the test does not look at it."""


def test_invoke():
    section("the buttons open a dialog only when there is something to ask")
    scene.render.engine = EEVEE
    material = default_material("PS Invoke")
    new_object("PS Invoke", material)
    context = DialogContext()
    setup = Setup(template='UNLIT', start_with='NOTHING', report=ignore_report)
    check(setup.invoke(context, None) == {'RUNNING_MODAL'}
          and context.dialogs == [dict(width=node_tree_ops.DIALOG_WIDTH, title="Add Paint System", confirm_text="Add")],
          f"Add Paint System opens its dialog {context.dialogs}")
    check((setup.template, setup.start_with) == ('PBR', 'IMAGE'),
          f"on the recommended template, with an image layer to start ({setup.template}, {setup.start_with})")

    run(template='PBR')
    tree = material.paint_system.tree
    material.node_tree.nodes.remove(find_material_group_node(material, tree))
    context = DialogContext()
    check(Setup(report=ignore_report).invoke(context, None) == {'FINISHED'} and context.dialogs == []
          and find_material_group_node(material, tree) is not None,
          "Connect to the Material connects the tree again without the dialog")

    context = DialogContext()
    menu_item = AddChannel(template='METALLIC', name="Channel", type='COLOR', report=ignore_report)
    check(menu_item.invoke(context, None) == {'FINISHED'}
          and context.dialogs == [] and 'Metallic' in tree.channels,
          "a channel template from the Add Channel menu adds its channel without a dialog")
    custom = AddChannel(template='CUSTOM', name="Metallic", type='FLOAT', report=ignore_report)
    check(custom.invoke(context, None) == {'RUNNING_MODAL'} and context.dialogs == [{}]
          and custom.name == "Metallic 1",
          f"Custom... asks for the name and type, starting from a free name ({custom.name})")


def test_linked_data():
    section("linked data is not set up, and Add Channel leaves a linked material alone")
    scene.render.engine = EEVEE
    material = default_material("PS Linked")
    obj = new_object("PS Linked", material)
    run(template='PBR')
    tree = material.paint_system.tree
    plain = default_material("PS Linked Plain")
    bare = bpy.data.meshes.new("PS Linked Bare")
    bare.materials.append(None)
    path = os.path.join(tempfile.mkdtemp(), "ps_templates_linked.blend")
    bpy.data.libraries.write(path, {material, plain, bare}, fake_user=True)
    with bpy.data.libraries.load(path, link=True) as (_, linked):
        linked.materials = ["PS Linked", "PS Linked Plain"]
        linked.meshes = ["PS Linked Bare"]
    linked_material, linked_plain = linked.materials
    linked_bare = linked.meshes[0]
    library = linked_material.library
    users = []
    try:
        # The linked material's group node names this tree until the next
        # compile gives the linked copy of the tree a uuid of its own.
        linked_group = next(node for node in linked_material.node_tree.nodes if node.bl_idname == 'ShaderNodeGroup')
        check(find_material_group_node(linked_material, tree) == linked_group, "the linked group node names the tree")
        channel, connected = T.add_template_channel(tree, 'ROUGHNESS')
        check(connected == 1 and linked_group.node_tree == linked_material.paint_system.tree.compiled
              and not first(linked_material, T.PRINCIPLED).inputs['Roughness'].is_linked,
              "a channel is connected in the local material only, and the linked one runs its own tree")

        def user(name, mesh):
            other = bpy.data.objects.new(name, mesh)
            scene.collection.objects.link(other)
            users.append(other)
            activate(other)
            return other

        user("PS Linked Plain User", bpy.data.meshes.new("PS Linked Plain User")).data.materials.append(linked_plain)
        check(not bpy.ops.paint_system.setup_material.poll(), "Add is off for a linked material")
        bare_user = user("PS Linked Bare User", linked_bare)
        check(not bpy.ops.paint_system.setup_material.poll(), "and for a linked mesh that would get a new one")
        bare_user.material_slots[0].link = 'OBJECT'
        check(bpy.ops.paint_system.setup_material.poll(), "but on when the object's own slot gets it")
    finally:
        for other in users:
            bpy.data.objects.remove(other)
        bpy.data.libraries.remove(library)
        os.remove(path)
        activate(obj)


# -- the summary and the dialog --------------------------------------------

def options(**values):
    """The dialog's options, at their defaults and the template's, with *values* set."""
    props = {}
    for prop in bpy.ops.paint_system.setup_material.get_rna_type().properties:
        if prop.identifier == 'rna_type':
            continue
        props[prop.identifier] = tuple(prop.default_array) if getattr(prop, 'is_array', False) else prop.default
    props.update(T.template_defaults(values.get('template', props['template'])))
    props.update(values)
    return SimpleNamespace(**props)


def summary(obj, **values):
    opts = options(**values)
    return [line.text for line in T.summary_lines(opts.template, opts, obj, bpy.context)]


def test_summary():
    section("the summary says what Add changes, one fact per line")
    scene.render.engine = EEVEE
    set_view('AgX', 'None')
    nothing = "Nothing is deleted or disconnected."
    obj = new_object("PS Summary Plane")
    got = summary(obj, template='UNLIT', start_with='IMAGE')
    check(got == ['Makes a new material "PS Summary Plane Material".', "Adds an unlit shader that shows the paint.",
                  'Adds a 2048 x 2048 image layer to "Color".', 'Uses the UV map "UVMap".', "Hides back faces.",
                  "Switches the view to Standard.", nothing], f"Unlit on a new material {got}")

    obj = new_object("PS Summary Textured", textured("PS Summary Textured"))
    opts = options(template='PBR', add_roughness=True)
    lines = T.summary_lines('PBR', opts, obj, bpy.context)
    check([line.text for line in lines] == ['Adds to the material "PS Summary Textured".',
                                            'Paints into "Principled BSDF":', 'Base Color, over "PS Wood"',
                                            "Roughness", nothing],
          f"PBR over a texture {[line.text for line in lines]}")
    check([line.indent for line in lines] == [False, False, True, True, False], "the channels are indented")
    nt = obj.active_material.node_tree
    nt.links.new(first(obj.active_material, 'ShaderNodeTexImage').outputs['Alpha'],
                 first(obj.active_material, T.PRINCIPLED).inputs['Alpha'])
    got = summary(obj, template='PBR')
    check(got[1:4] == ['Paints into "Principled BSDF":', 'Base Color, over "PS Wood"', 'Alpha, over "PS Wood"'],
          f"a linked Alpha goes under the paint too {got}")
    got = summary(obj, template='PBR', add_color=False)
    check(got[-2:] == ["Pick at least one channel.", nothing], f"PBR without a channel {got}")
    lines = T.summary_lines('PBR', options(template='PBR', add_color=False), obj, bpy.context)
    check([line.warning for line in lines].count(True) == 1 and lines[-2].warning, "which is a warning")

    obj = new_object("PS Summary Emission", emission("PS Summary Emission"))
    got = summary(obj, template='UNLIT')
    check(got == ['Adds to the material "PS Summary Emission".', "Adds an unlit shader that shows the paint.",
                  '"Emission" stays, but no longer shows.', "Hides back faces.", "Switches the view to Standard.",
                  nothing], f"Unlit over an Emission {got}")
    got = summary(obj, template='PBR')
    check(got[1:4] == ["Adds a Principled BSDF to paint into:", "Base Color", '"Emission" stays, but no longer shows.'],
          f"PBR without a Principled {got}")
    for kind in ("a muted Surface link", "a reroute nothing feeds"):
        name = f"PS Summary {kind}"
        obj = new_object(name, MATERIALS[kind](name))
        got = summary(obj, template='UNLIT')
        check(got == [f'Adds to the material "{name}".', "Adds an unlit shader that shows the paint.",
                      "Hides back faces.", "Switches the view to Standard.", nothing],
              f"Unlit over {kind}: nothing stops showing {got}")

    obj = new_object("PS Summary Split", split("PS Summary Split"))
    got = summary(obj, template='PAINT_OVER')
    check(got[1:4] == ['Paints over "Emission" as it renders.', "The paint on top is unlit.",
                       'Cycles keeps using "Material Output".'], f"Paint Over on split outputs {got}")
    material = no_surface("PS Summary Empty EEVEE")
    first(material, T.MATERIAL_OUTPUT).target = 'EEVEE'
    cycles = material.node_tree.nodes.new(T.MATERIAL_OUTPUT)
    cycles.target = 'CYCLES'
    cycles.label = "Cycles Output"
    material.node_tree.links.new(material.node_tree.nodes.new(T.PRINCIPLED).outputs[0], cycles.inputs['Surface'])
    obj = new_object("PS Summary Empty EEVEE", material)
    got = summary(obj, template='UNLIT')
    check('Cycles keeps using "Cycles Output".' in got, f"an empty EEVEE output while Cycles has a shader {got}")
    material = default_material("PS Summary Cycles Only")
    first(material, T.MATERIAL_OUTPUT).target = 'CYCLES'
    obj = new_object("PS Summary Cycles Only", material)
    got = summary(obj, template='UNLIT')
    check('Cycles keeps using "Material Output".' in got, f"an output for Cycles only {got}")
    run(template='UNLIT')
    nt = material.node_tree
    check(nt.get_output_node('CYCLES').name == "Material Output" and nt.get_output_node('EEVEE').name != "Material Output",
          "which it does after Add")

    material = no_surface("PS Summary Reroute")
    nt = material.node_tree
    reroute = nt.nodes.new('NodeReroute')
    nt.links.new(nt.nodes.new('ShaderNodeEmission').outputs[0], reroute.inputs[0])
    nt.links.new(reroute.outputs[0], first(material, T.MATERIAL_OUTPUT).inputs['Surface'])
    obj = new_object("PS Summary Reroute", material)
    got = summary(obj, template='PAINT_OVER')
    check(got[1] == 'Paints over "Emission" as it renders.', f"the source is named past a reroute {got}")

    bpy.data.materials.new("PS Summary Taken Material")
    obj = new_object("PS Summary Taken")
    got = summary(obj, template='UNLIT')
    check(got[0] == 'Makes a new material "PS Summary Taken Material.001".', f"a taken name gets a number {got}")
    run(template='UNLIT')
    check(obj.active_material.name == "PS Summary Taken Material.001", "which the new material has")

    obj = new_object("PS Summary Displaced", displaced("PS Summary Displaced"))
    got = summary(obj, template='NORMAL')
    check(got == ['Adds to the material "PS Summary Displaced".', "Adds a grey Diffuse shader to show the normals.",
                  '"Principled BSDF" stays, but no longer shows.', "The new output keeps the Displacement.",
                  'Uses the UV map "UVMap".', nothing], f"Normal over a displaced material {got}")

    obj = new_object("PS Summary Group", default_material("PS Summary Group"))
    got = summary(obj, template='GROUP', use_smooth_transparency=True)
    check(got == ['Adds to the material "PS Summary Group".', "Adds the Paint System node, not connected.",
                  "Nothing shows until you connect it.", "Turns on smooth transparency.", nothing],
          f"Group Only {got}")
    scene.render.engine = 'CYCLES'
    got = summary(obj, template='PAINT_OVER')
    check(got[1] == f"{T.PAINT_OVER_NEEDS}.", f"Paint Over in Cycles says what it needs {got}")
    scene.render.engine = EEVEE

    other = new_object("PS Summary Other")
    no_uv = new_object("PS Summary No UV", uv_maps=())
    activate(no_uv, other)
    got = summary(no_uv, template='UNLIT', start_with='IMAGE')
    check("Only the active object is set up." in got and "The mesh has no UV map. Unwrap it to paint." in got,
          f"several meshes selected, and a mesh without a UV map {got}")
    shared = default_material("PS Summary Shared")
    one, two = new_object("PS Summary Shared One", shared), new_object("PS Summary Shared Two", shared)
    activate(one, two)
    check("Only the active object is set up." not in summary(one, template='PBR'),
          "a selected mesh with the same material shows the paint too")
    two = new_object("PS Summary Two UVs", uv_maps=("UVMap", "Detail"))
    two.data.uv_layers["Detail"].active_render = True
    got = summary(two, template='NORMAL')
    check('Uses the UV map "Detail".' in got, f"an empty UV map option names the active render UV map {got}")
    got = summary(two, template='NORMAL', uv_map="UVMap")
    check('Uses the UV map "UVMap".' in got, "and a picked one names it")
    set_view('Standard', 'None')
    check("Switches the view to Standard." not in summary(two, template='UNLIT'),
          "a scene already in Standard is not switched")
    set_view('AgX', 'None')
    if before(5):
        material = default_material("PS Summary Nodes Off")
        material.use_nodes = False
        obj = new_object("PS Summary Nodes Off", material)
        check("Turns on the material's nodes." in summary(obj, template='PBR'), "nodes switched off are turned on")

    long = "word " * 40
    lines = T.wrap_text(long.strip(), 200, 11)
    check(len(lines) > 1 and " ".join(lines) == long.strip(), f"a long line wraps at spaces into {len(lines)} lines")


class Dialog(SimpleNamespace):
    """Stands in for the operator: its draw methods, on the options it is given."""
    draw = SETUP.draw
    _draw_summary = SETUP._draw_summary
    _draw_options = SETUP._draw_options


def draw_dialog(open_panels=False, **values):
    calls = []
    Dialog(layout=RecordingLayout(calls, open_panels), **vars(options(**values))).draw(bpy.context)
    return calls


def drawn(calls, name):
    return [call for call in calls if call[0] == name]


def props(calls):
    """The names of the properties drawn, in order, searches included."""
    return [call[1][1] for call in calls if call[0] in {"prop", "prop_search"}]


def test_dialog():
    section("the dialog draws the cards, the reason, the summary and the chosen template's options")
    scene.render.engine = 'CYCLES'
    obj = new_object("PS Dialog", default_material("PS Dialog"))
    calls = draw_dialog(template='PBR')
    cards = drawn(calls, "prop_enum")
    check([call[1][1:] for call in cards] == [("template", key) for key in TEMPLATES],
          f"one card per template {[call[1][1:] for call in cards]}")
    check(all(call.layout.setting("scale_y") == 1.4 for call in cards), "drawn large")
    enabled = {call[1][2]: call.layout.setting("enabled", True) for call in cards}
    check(enabled == {key: key != 'PAINT_OVER' for key in TEMPLATES}, f"Paint Over is off in Cycles {enabled}")
    reason = T.recommend(obj.active_material, scene)[1]
    labels = [call[2].get("text") for call in drawn(calls, "label")]
    check(reason in labels, "the recommended card shows the reason")
    texts = [line.text for line in T.summary_lines('PBR', options(template='PBR'), obj, bpy.context)]
    check(all(text in labels for text in texts), "and the summary")
    check(props(calls)[:5] == ["add_color", "add_metallic", "add_roughness", "add_normal", "start_with"]
          and "canvas" not in props(calls), f"PBR shows its channels and Start With {props(calls)}")
    check("resolution" not in props(calls) and "uv_map" not in props(calls),
          "no resolution or UV map with nothing to start with")
    section_panels = {call[1][0]: call[2] for call in drawn(calls, "panel")}
    check(section_panels.get("paint_system_add_material_viewport") == {"default_closed": True},
          f"Material & Viewport starts closed {section_panels}")

    calls = draw_dialog(template='PAINT_OVER')
    needs = f"{T.PAINT_OVER_NEEDS}."
    points = bpy.context.preferences.ui_styles[0].widget.points
    wrapped = T.wrap_text(needs, node_tree_ops.SUMMARY_WIDTH, points)
    icons = {call[2].get("text"): {key: value for key, value in call[2].items() if key != "text"}
             for call in drawn(calls, "label")}
    check(len(wrapped) > 1 and [icons.get(text) for text in wrapped]
          == [icon_kwargs('ERROR')] + [icon_kwargs('BLANK1')] * (len(wrapped) - 1),
          f"a warning shows an error icon, and its wrapped lines a blank one {wrapped}")
    check(icons.get("Nothing is deleted or disconnected.") == icon_kwargs('NONE'), "other lines show none")

    scene.render.engine = EEVEE
    calls = draw_dialog(template='GROUP')
    enabled = {call[1][2]: call.layout.setting("enabled", True) for call in drawn(calls, "prop_enum")}
    check(enabled['PAINT_OVER'], "Paint Over is on in EEVEE")
    check(reason not in [call[2].get("text") for call in drawn(calls, "label")],
          "another card shows no reason")

    new_object("PS Dialog Two UVs", uv_maps=("UVMap", "Detail"))
    calls = draw_dialog(template='UNLIT', start_with='IMAGE')
    check(props(calls)[:4] == ["canvas", "start_with", "resolution", "uv_map"],
          f"Unlit shows the canvas, the resolution and the UV map search {props(calls)}")
    resolution = next(call for call in drawn(calls, "prop") if call[1][1] == "resolution")
    check(resolution[2].get("expand") is True, "the resolution as buttons")
    calls = draw_dialog(template='NORMAL')
    check(props(calls)[:2] == ["start_with", "uv_map"], f"a Normal channel uses the UV map too {props(calls)}")
    activate(obj)
    calls = draw_dialog(template='NORMAL')
    check("uv_map" not in props(calls), "not on a mesh with one UV map")

    set_view('AgX', 'None')
    calls = draw_dialog(open_panels=True, template='UNLIT', use_smooth_transparency=True)
    check(props(calls)[-3:] == ["use_smooth_transparency", "use_backface_culling", "use_standard_view"],
          f"the open section holds the material and view options {props(calls)}")
    check(any(call[2].get("text", "").startswith("Overlapping transparent faces") for call in drawn(calls, "label")),
          "with a warning about smooth transparency")
    set_view('Standard', 'None')
    calls = draw_dialog(open_panels=True, template='UNLIT')
    check("use_standard_view" not in props(calls), "Standard View Transform is left out in Standard")
    set_view('AgX', 'None')


guarded(test_recommend)
guarded(test_templates)
guarded(test_refusals)
guarded(test_paint_over_sources)
guarded(test_muted_links)
guarded(test_pbr_channels)
guarded(test_unlit_render)
guarded(test_standard_view_while_previewing)
guarded(test_add_channel)
guarded(test_reconnect)
guarded(test_linked_data)
guarded(test_invoke)
guarded(test_summary)
guarded(test_dialog)

finish("TEMPLATES TEST")
