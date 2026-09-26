"""Vector channels and their paint spaces (PS-006).

A vector channel takes and gives world-space vectors, and its layers work
in the channel's Paint In space. Normals are stored as normal map colours.
The bakes compare the compiled shader with Blender's own nodes on a
curved, rotated mesh whose tangent UV map is mirrored on one half, so the
tangent frame is checked with both handednesses. A bake sees only front
faces, so back faces are rendered with Cycles from behind.

Run:  blender -b --factory-startup --python tests/test_vector_channels.py
"""
import math
import os
import sys
import tempfile

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, guarded, finish, import_from, register_addon, section  # noqa: E402

register_addon()
core = import_from("compiler.core")
library = import_from("compiler.library")
bake_node_cache = import_from("compiler.bake").bake_node_cache
socket_named = import_from("nodetree.stack_ops").socket_named
compile_tree = core.compile_tree
build_ir = core.build_ir

BAKE_UV = "Bake"
TANGENT_UV = "Mirrored"
SIZE = 32

# A tangent-space normal tilted 25 degrees, with a green that flips on the
# mirrored half. Much steeper normals would meet Cycles' reflection clamp.
SOURCE_COLOR = (0.65, 0.38, 0.9, 1.0)
PAINTED_COLOR = (0.6, 0.43, 0.93, 1.0)


def new_tree(name, *, space='TANGENT', kind='NORMAL'):
    """A tree with a "Normal" vector channel, painted in *space* on the mirrored UV map."""
    tree = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    tree.initialize()
    tree.create_channel("Normal", 'VECTOR')
    channel = tree.channels["Normal"]
    channel.vector_kind = kind
    channel.paint_space = space
    channel.tangent_uv_map = TANGENT_UV
    return tree


def curved_mesh(name, rotation=(0.4, 0.3, 0.8), scale=(1.3, 1.3, 1.3)):
    """A smooth, curved grid. Its UV map "Mirrored" is mirrored where x < 0.

    "Bake" is the active UV map and lays the grid out flat, so each half
    bakes to pixels of its own.
    """
    steps = 12
    coords = [-1.0 + 2.0 * i / steps for i in range(steps + 1)]
    verts = [(x, y, 0.25 * math.sin(2.0 * x) * math.cos(1.5 * y)) for y in coords for x in coords]
    row = steps + 1
    faces = [(j * row + i, j * row + i + 1, (j + 1) * row + i + 1, (j + 1) * row + i)
             for j in range(steps) for i in range(steps)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    bake_uv = mesh.uv_layers.new(name=BAKE_UV)
    mirrored = mesh.uv_layers.new(name=TANGENT_UV)
    for loop in mesh.loops:
        x, y, _ = verts[loop.vertex_index]
        bake_uv.data[loop.index].uv = ((x + 1.0) / 2.0, (y + 1.0) / 2.0)
        mirrored.data[loop.index].uv = (abs(x), (y + 1.0) / 2.0)
    mesh.uv_layers.active = bake_uv
    bake_uv.active_render = True
    obj = bpy.data.objects.new(name, mesh)
    obj.rotation_euler = rotation
    obj.scale = scale
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def normal_map(nt, space, color):
    node = nt.nodes.new('ShaderNodeNormalMap')
    node.space = space
    node.uv_map = TANGENT_UV
    node.inputs['Color'].default_value = color
    return node.outputs['Normal']


def constant(nt, vector):
    node = nt.nodes.new('ShaderNodeCombineXYZ')
    for index, value in enumerate(vector):
        node.inputs[index].default_value = value
    return node.outputs['Vector']


def distance(nt, a, b):
    node = nt.nodes.new('ShaderNodeVectorMath')
    node.operation = 'DISTANCE'
    nt.links.new(a, node.inputs[0])
    nt.links.new(b, node.inputs[1])
    return node.outputs['Value']


def emit(nt, red, green=None):
    """An emission of (*red*, *green*, 1). The blue marks the pixels the mesh covers."""
    combine = nt.nodes.new('ShaderNodeCombineXYZ')
    combine.inputs[2].default_value = 1.0
    nt.links.new(red, combine.inputs[0])
    if green is not None:
        nt.links.new(green, combine.inputs[1])
    emission = nt.nodes.new('ShaderNodeEmission')
    nt.links.new(combine.outputs['Vector'], emission.inputs['Color'])
    return emission.outputs['Emission']


def bake(obj, node_group, build):
    """Bake on *obj* the light of the shader that *build(nt, group)* returns.

    *group* is a group node running *node_group*, such as a tree's
    compiled shader. Returns the RGBA rows of the pixels whose blue is
    set, which are the pixels the mesh covers when the shader comes from
    ``emit``.
    """
    mat = bpy.data.materials.new("PS Vector Bake")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    group = nt.nodes.new('ShaderNodeGroup')
    group.node_tree = node_group
    output = nt.nodes.new('ShaderNodeOutputMaterial')
    nt.links.new(build(nt, group), output.inputs['Surface'])
    target = nt.nodes.new('ShaderNodeTexImage')
    image = bpy.data.images.new("PS Vector Bake", SIZE, SIZE, alpha=True, float_buffer=True)
    image.colorspace_settings.name = 'Non-Color'
    target.image = image
    nt.nodes.active = target
    obj.data.materials.clear()
    obj.data.materials.append(mat)

    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 1
    scene.cycles.device = 'CPU'
    scene.render.bake.target = 'IMAGE_TEXTURES'
    scene.render.bake.margin = 0
    view_layer = bpy.context.view_layer
    for other in view_layer.objects:
        other.select_set(other == obj)
    view_layer.objects.active = obj
    try:
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
            bpy.ops.object.bake(type='EMIT')
        pixels = np.empty(SIZE * SIZE * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        rows = pixels.reshape(SIZE * SIZE, 4)
        return rows[rows[:, 2] > 0.5]
    finally:
        obj.data.materials.clear()
        bpy.data.materials.remove(mat)
        bpy.data.images.remove(image)


def render_back(obj, node_group, build):
    """Render with Cycles, from behind *obj*, the light of the shader *build(nt, group)* returns.

    A bake only sees front faces. Here an orthographic camera looks along
    the object's Z at its back faces, and every other object is left out.
    Returns the rows ``bake`` would.
    """
    scene = bpy.context.scene
    mat = bpy.data.materials.new("PS Vector Render")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    group = nt.nodes.new('ShaderNodeGroup')
    group.node_tree = node_group
    output = nt.nodes.new('ShaderNodeOutputMaterial')
    nt.links.new(build(nt, group), output.inputs['Surface'])
    obj.data.materials.clear()
    obj.data.materials.append(mat)

    camera = bpy.data.objects.new("PS Vector Camera", bpy.data.cameras.new("PS Vector Camera"))
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 2.0
    scene.collection.objects.link(camera)
    behind = -obj.matrix_world.to_3x3().col[2].normalized()
    camera.location = obj.matrix_world.translation + behind * 6.0
    camera.rotation_euler = (-behind).to_track_quat('-Z', 'Y').to_euler()
    scene.camera = camera
    hidden = {other: other.hide_render for other in scene.objects if other.type == 'MESH'}
    for other in hidden:
        other.hide_render = other != obj
    render = scene.render
    render.engine = 'CYCLES'
    scene.cycles.samples = 1
    scene.cycles.device = 'CPU'
    scene.cycles.use_denoising = False
    # A narrow box filter, so each pixel is one sample of the shader.
    scene.cycles.pixel_filter_type = 'BOX'
    scene.cycles.filter_width = 0.01
    render.resolution_x = render.resolution_y = SIZE
    render.resolution_percentage = 100
    render.film_transparent = True
    render.image_settings.file_format = 'OPEN_EXR'
    render.image_settings.color_depth = '32'
    try:
        with tempfile.TemporaryDirectory() as folder:
            render.filepath = os.path.join(folder, "back.exr")
            bpy.ops.render.render(write_still=True)
            image = bpy.data.images.load(render.filepath, check_existing=False)
            pixels = np.empty(SIZE * SIZE * 4, dtype=np.float32)
            image.pixels.foreach_get(pixels)
            bpy.data.images.remove(image)
        rows = pixels.reshape(SIZE * SIZE, 4)
        return rows[(rows[:, 3] > 0.999) & (rows[:, 2] > 0.5)]
    finally:
        for other, value in hidden.items():
            other.hide_render = value
        obj.data.materials.clear()
        bpy.data.materials.remove(mat)
        bpy.data.cameras.remove(camera.data)


def linked_trip(name="Normal"):
    """A build for ``bake``: the distance of a linked normal from what the *name* channel gives back.

    The green is how far the linked normal is from the surface's, so a
    tree that ignores its input cannot pass.
    """
    def build(nt, group):
        source = normal_map(nt, 'TANGENT', SOURCE_COLOR)
        nt.links.new(source, group.inputs[name])
        geometry = nt.nodes.new('ShaderNodeNewGeometry')
        return emit(nt, distance(nt, group.outputs[name], source),
                    distance(nt, source, geometry.outputs['Normal']))
    return build


def round_trip(obj, tree, name="Normal", capture=bake):
    """(largest error, largest tilt, pixels) of *tree* giving back a linked normal."""
    rows = capture(obj, tree.compiled, linked_trip(name))
    return float(rows[:, 0].max()), float(rows[:, 1].max()), len(rows)


def largest_error(obj, tree, reference, capture=bake, name="Normal"):
    """The largest distance between *tree*'s *name* output and the vector *reference(nt)* gives."""
    def build(nt, group):
        return emit(nt, distance(nt, group.outputs[name], reference(nt)))
    return float(capture(obj, tree.compiled, build)[:, 0].max())


def test_defaults():
    section("a new vector channel")
    tree = bpy.data.node_groups.new("Vector Defaults", 'PaintSystemNodeTree')
    tree.initialize()
    tree.create_channel("Normal", 'VECTOR')
    channel = tree.channels["Normal"]
    check((channel.vector_kind, channel.paint_space, channel.tangent_uv_map) == ('NORMAL', 'TANGENT', ""),
          "it holds normals, painted in tangent space on the active render UV map")
    check(not channel.use_alpha and channel.color_space == 'NONCOLOR', "no alpha, and non-colour images")

    def normal_input():
        return next(item for item in tree.compiled.interface.items_tree
                    if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == "Normal")
    check(normal_input().socket_type == 'NodeSocketVector', "the material's input is a vector")
    check(normal_input().hide_value, "a normal input hides its value, as unlinked it is the shading normal")
    channel.vector_kind = 'VECTOR'
    check(not normal_input().hide_value, "a vector input shows its value")

    section("a value typed while the channel held vectors")
    material = bpy.data.materials.new("Vector Typed")
    material.use_nodes = True
    group = material.node_tree.nodes.new('ShaderNodeGroup')
    group.node_tree = tree.compiled
    group.inputs["Normal"].default_value = (0.3, -0.5, 0.8)
    channel.vector_kind = 'NORMAL'
    check(tuple(group.inputs["Normal"].default_value) == (0.0, 0.0, 0.0),
          "is cleared once it holds normals, so the hidden input stands for the shading normal again")
    bpy.data.materials.remove(material)

    section("a channel that stops holding vectors")
    for kind in ('COLOR', 'FLOAT'):
        channel.type = kind
        check(not normal_input().hide_value, f"{kind}: its input shows its value again")
        channel.type = 'VECTOR'


def test_round_trip():
    section("an empty stack gives back the linked normal")
    obj = curved_mesh("Vector Curved")
    for space in ('TANGENT', 'OBJECT', 'WORLD'):
        error, tilt, covered = round_trip(obj, new_tree(f"Vector Trip {space}", space=space))
        check(covered > SIZE * SIZE // 2, f"{space}: the bake covers the mesh ({covered} pixels)")
        check(tilt > 0.3, f"{space}: the linked normal is not the surface's ({tilt:.3f})")
        check(error < 0.01, f"{space}: both halves come back unchanged (largest error {error:.4f})")

    section("an unevenly scaled object")
    # The Normal Map node's frame is not a rotation there.
    stretched = curved_mesh("Vector Stretched", (0.2, -0.5, 0.3), (1.6, 0.7, 1.0))
    for space in ('TANGENT', 'OBJECT', 'WORLD'):
        error, _, _ = round_trip(stretched, new_tree(f"Vector Trip Stretched {space}", space=space))
        check(error < 0.01, f"{space}: the linked normal comes back unchanged (largest error {error:.4f})")


def test_tangent_frame():
    section("the tangent frame is at right angles to the surface")
    obj = curved_mesh("Vector Frame")

    # Cycles' tangents are close to perpendicular already, and EEVEE's are
    # not, so the tangent here leans towards the normal on purpose.
    def build(nt, group):
        geometry = nt.nodes.new('ShaderNodeNewGeometry')
        tangent = nt.nodes.new('ShaderNodeTangent')
        tangent.direction_type = 'UV_MAP'
        tangent.uv_map = TANGENT_UV
        leaning = nt.nodes.new('ShaderNodeVectorMath')
        leaning.operation = 'MULTIPLY_ADD'
        nt.links.new(geometry.outputs['Normal'], leaning.inputs[0])
        leaning.inputs[1].default_value = (0.8, 0.8, 0.8)
        nt.links.new(tangent.outputs['Tangent'], leaning.inputs[2])
        nt.links.new(geometry.outputs['Normal'], group.inputs['Vector'])
        nt.links.new(leaning.outputs['Vector'], group.inputs['Tangent'])
        nt.links.new(normal_map(nt, 'TANGENT', (0.5, 1.0, 0.5, 1.0)), group.inputs['Bitangent'])
        return emit(nt, distance(nt, group.outputs['Vector'], constant(nt, (0.0, 0.0, 1.0))))
    error = float(bake(obj, library.world_to_tangent_group(), build)[:, 0].max())
    check(error < 1e-3, f"the surface's own normal is +Z, however the tangent leans (largest error {error:.5f})")


def surface_normal(nt):
    """The surface's own normal: the shading normal, which faces the viewer, turned back on a back face."""
    geometry = nt.nodes.new('ShaderNodeNewGeometry')
    facing = nt.nodes.new('ShaderNodeMath')
    facing.operation = 'MULTIPLY_ADD'
    nt.links.new(geometry.outputs['Backfacing'], facing.inputs[0])
    facing.inputs[1].default_value = -2.0
    facing.inputs[2].default_value = 1.0
    turned = nt.nodes.new('ShaderNodeVectorMath')
    turned.operation = 'SCALE'
    nt.links.new(geometry.outputs['Normal'], turned.inputs[0])
    nt.links.new(facing.outputs[0], turned.inputs['Scale'])
    return turned.outputs['Vector']


def uv_tangent(nt):
    node = nt.nodes.new('ShaderNodeTangent')
    node.direction_type = 'UV_MAP'
    node.uv_map = TANGENT_UV
    return node.outputs['Tangent']


def test_back_faces():
    section("back faces")
    # Cycles' Normal Map node turns its result round on a back face, in
    # every space, and turns the tangent frame with it.
    obj = curved_mesh("Vector Back")
    for space in ('TANGENT', 'OBJECT', 'WORLD'):
        tree = new_tree(f"Vector Back {space}", space=space)
        error, tilt, covered = round_trip(obj, tree, capture=render_back)
        check(covered > SIZE * SIZE // 4 and tilt > 0.3,
              f"{space}: the render sees the back of the mesh ({covered} pixels, tilt {tilt:.3f})")
        check(error < 0.01, f"{space}: the linked normal comes back unchanged (largest error {error:.4f})")
        tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Normal").fill_color = PAINTED_COLOR
        error = largest_error(obj, tree, lambda nt: normal_map(nt, space, PAINTED_COLOR), capture=render_back)
        check(error < 1e-3, f"{space}: a painted normal is a Normal Map node of that colour (largest error {error:.5f})")

    section("a tangent-space vector is the same seen from either side")
    tree = new_tree("Vector Back Plain", kind='VECTOR')
    layer = tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Normal")
    for color, reference, label in (((0.0, 0.0, 1.0, 1.0), surface_normal, "Z is the surface's normal"),
                                    ((1.0, 0.0, 0.0, 1.0), uv_tangent, "X is the UV map's tangent")):
        layer.fill_color = color
        for capture, side in ((bake, "front"), (render_back, "back")):
            error = largest_error(obj, tree, reference, capture=capture)
            check(error < 1e-3, f"{label}, on the {side} (largest error {error:.5f})")

    # V runs along the object's +Y on both halves of the mirrored UV map,
    # and the mesh tilts less than 25 degrees from it.
    layer.fill_color = (0.0, 1.0, 0.0, 1.0)
    along_v = tuple(obj.matrix_world.to_3x3().col[1].normalized())

    def build(nt, group):
        along = nt.nodes.new('ShaderNodeVectorMath')
        along.operation = 'DOT_PRODUCT'
        nt.links.new(group.outputs["Normal"], along.inputs[0])
        nt.links.new(constant(nt, along_v), along.inputs[1])
        return emit(nt, along.outputs['Value'])
    for capture, side in ((bake, "front"), (render_back, "back")):
        least = float(capture(obj, tree.compiled, build)[:, 0].min())
        check(least > 0.9, f"Y runs along the UV map's V, on the {side} (least {least:.3f})")


def test_painted():
    section("a painted normal reads as a normal map")
    obj = curved_mesh("Vector Painted")
    for space in ('TANGENT', 'OBJECT', 'WORLD'):
        tree = new_tree(f"Vector Painted {space}", space=space)
        tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Normal").fill_color = PAINTED_COLOR
        error = largest_error(obj, tree, lambda nt: normal_map(nt, space, PAINTED_COLOR))
        check(error < 1e-3, f"{space}: the same as a Normal Map node of that colour (largest error {error:.5f})")


def test_vectors():
    section("vectors are painted as they are")
    obj = curved_mesh("Vector Plain")
    value = (0.3, -0.5, 0.8)
    for space in ('TANGENT', 'OBJECT', 'WORLD'):
        tree = new_tree(f"Vector Plain {space}", space=space, kind='VECTOR')

        def build(nt, group):
            group.inputs["Normal"].default_value = value
            return emit(nt, distance(nt, group.outputs["Normal"], constant(nt, value)))
        error = float(bake(obj, tree.compiled, build)[:, 0].max())
        check(error < 1e-3, f"{space}: an empty stack gives back the input (largest error {error:.5f})")

    section("a painted vector")
    # A unit vector, so it points where a normal map of it points. A
    # colour cannot be negative.
    painted = (0.24, 0.32, 0.9165)
    color = tuple(v * 0.5 + 0.5 for v in painted) + (1.0,)
    tree = new_tree("Vector Painted Tangent", kind='VECTOR')
    tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Normal").fill_color = painted + (1.0,)
    error = largest_error(obj, tree, lambda nt: normal_map(nt, 'TANGENT', color))
    check(error < 0.01, f"TANGENT: it points where a normal map of it points (largest error {error:.4f})")
    tree.channels["Normal"].paint_space = 'OBJECT'
    world = tuple(obj.matrix_world.to_3x3() @ Vector(painted))
    error = largest_error(obj, tree, lambda nt: constant(nt, world))
    check(error < 1e-3, f"OBJECT: it turns and scales with the object (largest error {error:.5f})")


def test_group_layer():
    section("a group layer converts between the two trees' spaces")
    obj = curved_mesh("Vector Group")
    child = new_tree("Vector Child", space='OBJECT')
    parent = new_tree("Vector Parent", space='TANGENT')
    group = parent.nodes.new('PaintSystemGroupLayerNode')
    group.node_tree = child
    gin, gout = parent.get_input_node(), parent.get_output_node()
    link_in = parent.links.new(gin.outputs["Normal"], group.inputs["Normal"])
    parent.links.new(group.outputs["Normal"], gout.inputs["Normal"])
    compile_tree(parent)
    error, _, _ = round_trip(obj, parent)
    check(error < 0.01, f"the linked normal comes back through the wrapped tree (largest error {error:.4f})")

    section("the group layer's hash follows the conversions")
    parent.links.remove(link_in)
    before = build_ir(parent).ctx.subtree_hash(group)
    parent.channels["Normal"].paint_space = 'WORLD'
    check(build_ir(parent).ctx.subtree_hash(group) != before,
          "with nothing linked in, this tree's paint space still changes what the group layer gives")

    section("a group layer converts for the stack it sits in, not the channel of its sockets' name")
    # The wrapped tree paints, so a conversion on the wrong channel's
    # space cannot cancel out on the way back.
    painting = new_tree("Vector Painting Child", space='OBJECT')
    painting.insert_layer_node('PaintSystemSolidColorLayerNode', "Normal").fill_color = PAINTED_COLOR
    crossed = new_tree("Vector Crossed", space='WORLD')
    crossed.create_channel("Bump", 'VECTOR')
    crossed.channels["Bump"].tangent_uv_map = TANGENT_UV
    group = crossed.nodes.new('PaintSystemGroupLayerNode')
    group.node_tree = painting
    gin, gout = crossed.get_input_node(), crossed.get_output_node()
    crossed.links.new(socket_named(gin.outputs, "Bump"), group.inputs["Normal"])
    crossed.links.new(group.outputs["Normal"], socket_named(gout.inputs, "Bump"))
    compile_tree(crossed)
    error = largest_error(obj, crossed, lambda nt: normal_map(nt, 'OBJECT', PAINTED_COLOR), name="Bump")
    check(error < 0.01, f"the Normal sockets in the Bump stack paint in its tangent space (largest error {error:.4f})")


def test_cache():
    section("a cache keeps vectors below 0")
    obj = curved_mesh("Vector Cache")
    tree = new_tree("Vector Cache", space='WORLD', kind='VECTOR')
    tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Normal").fill_color = (0.2, 0.4, 0.6, 1.0)
    top = tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Normal")
    top.fill_color = (0.9, 0.9, 0.9, 1.0)
    top.blend_mode = 'SUBTRACT'
    below_zero = (-0.7, -0.5, -0.3)
    # A cache baked while the channel held colours, whose new layers
    # paint in sRGB.
    channel = tree.channels["Normal"]
    channel.type = 'COLOR'
    channel.color_space = 'COLOR'
    colour_cache = bake_node_cache(bpy.context, tree, top, obj, width=SIZE, height=SIZE, margin=2).name
    channel.type = 'VECTOR'
    error = largest_error(obj, tree, lambda nt: constant(nt, below_zero))
    check(error < 1e-3, f"the live stack is below 0 (largest error {error:.5f})")
    bake_node_cache(bpy.context, tree, top, obj, width=SIZE, height=SIZE, margin=2)
    top.cache_enabled = True
    compile_tree(tree)
    image = top.cache_image
    check(image.is_float and image.colorspace_settings.name == 'Non-Color',
          f"the cache is a float image of data ({image.is_float}, {image.colorspace_settings.name})")
    check(colour_cache not in bpy.data.images, "the byte cache from before is removed, as nothing else uses it")
    error = largest_error(obj, tree, lambda nt: constant(nt, below_zero))
    check(error < 1e-3, f"and the cached stack gives the same (largest error {error:.5f})")


def test_preview():
    section("the preview shows the layer values")
    obj = curved_mesh("Vector Preview")
    tree = new_tree("Vector Preview")
    tree.active_channel_index = tree.channels.find("Normal")
    tree.preview_channel = True
    compile_tree(tree)
    rows = bake(obj, tree.compiled, lambda nt, group: group.outputs["Preview"])
    flat = np.abs(rows[:, :3] - (0.5, 0.5, 1.0)).max() if len(rows) else 1.0
    check(len(rows) > SIZE * SIZE // 2 and flat < 0.01,
          f"with nothing linked in, a tangent-space normal is flat blue everywhere (largest error {flat:.4f})")

    # Cycles then has no tangent frame, so the normal has no colour.
    tree.channels["Normal"].tangent_uv_map = "Missing"
    rows = bake(obj, tree.compiled, lambda nt, group: group.outputs["Preview"])
    flat = np.abs(rows[:, :3] - (0.5, 0.5, 1.0)).max() if len(rows) else 1.0
    check(len(rows) > SIZE * SIZE // 2 and flat < 0.01,
          f"on a UV map the mesh does not have, it is flat blue too (largest error {flat:.4f})")
    tree.preview_channel = False


def test_patching():
    section("switching spaces patches the compiled tree in place")
    tree = new_tree("Vector Patch")
    gout = tree.get_output_node()
    socket = next(s for s in gout.inputs if s.name == "Normal")
    decode_id = f"{gout.uuid}:out:{socket.identifier}:decode"

    def decode():
        return next(node for node in tree.compiled.nodes if node.get("ps_identifier") == decode_id)
    first = decode()
    check(first.uv_map == TANGENT_UV, "in tangent space the stack is read back on the channel's UV map")
    pointer = first.as_pointer()
    for space in ('OBJECT', 'WORLD', 'TANGENT'):
        tree.channels["Normal"].paint_space = space
        node = decode()
        check(node.as_pointer() == pointer and node.space == space,
              f"{space}: the same Normal Map node reads the stack back ({node.space})")
    channel = tree.channels["Normal"]
    channel.paint_space = 'OBJECT'
    before = core.artifact_fingerprint(tree)
    channel.tangent_uv_map = BAKE_UV
    check(core.artifact_fingerprint(tree) == before, "outside tangent space a UV map change leaves the compiled tree")
    channel.tangent_uv_map = TANGENT_UV
    channel.paint_space = 'TANGENT'

    section("caches above the input follow the settings")
    layer = tree.insert_layer_node('PaintSystemSolidColorLayerNode', "Normal")

    def layer_hash():
        return build_ir(tree).ctx.subtree_hash(layer)
    hashes = {layer_hash()}
    channel.tangent_uv_map = BAKE_UV
    hashes.add(layer_hash())
    channel.paint_space = 'OBJECT'
    object_hash = layer_hash()
    hashes.add(object_hash)
    channel.tangent_uv_map = TANGENT_UV
    check(layer_hash() == object_hash, "outside tangent space the UV map changes nothing")
    channel.vector_kind = 'VECTOR'
    hashes.add(layer_hash())
    check(len(hashes) == 4, f"the UV map, the space and the kind each change the stack below ({len(hashes)})")


guarded(test_defaults)
guarded(test_round_trip)
guarded(test_tangent_frame)
guarded(test_back_faces)
guarded(test_painted)
guarded(test_vectors)
guarded(test_group_layer)
guarded(test_cache)
guarded(test_preview)
guarded(test_patching)
finish("VECTOR CHANNELS TEST")
