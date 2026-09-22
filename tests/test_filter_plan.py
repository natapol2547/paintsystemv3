"""Which path a filter layer's input comes down (PS-057).

`filters.layer_plan.resolve_input` decides between the GPU composite and
a Cycles bake, and refuses when neither can run. It allocates nothing, so
these need no GPU context and run everywhere -- what they check is the
decision, not the pixels. `tests/test_filter_composite.py` checks the
pixels.

A composite samples every source image by normalised coordinate, so the
whole stack below has to share one UV map with the derived image. The
names decide that on their own unless one map is named and some layers
leave theirs empty. Only then is a mesh asked: the layer's own Object
first, else the active object, and either one must use the tree.
"""
import os
import sys
import tempfile
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (check, finish, import_from, register_addon,  # noqa: E402
                     section, use_tree)

register_addon()
core = import_from("compiler.core")
gpu_core = import_from("gpu_passes.core")
layer_plan = import_from("filters.layer_plan")
filters_core = import_from("filters.core")
stack_ops = import_from("nodetree.stack_ops")
filter_layer_node = import_from("nodes.layers.filter_layer_node")
create_managed_image = import_from("compiler.bake").create_managed_image

SOLID = 'PaintSystemSolidColorLayerNode'
IMAGE = 'PaintSystemImageLayerNode'
FOLDER = 'PaintSystemFolderLayerNode'
FILTER = 'PaintSystemFilterLayerNode'

# Background 4.2 to 5.1 have no way to reach a GPU context, so every
# composite decision there ends at the bake instead. The decision is
# still worth checking: what changes is only which side it lands on.
HAS_GPU = gpu_core.gpu_known() is not False


class FakeContext:
    """Just the `object` a plan reads, so a test can pass none."""

    def __init__(self, obj=None):
        self.object = obj


def mesh_with_uvs(name, uv_names, active_render=0, tree=None):
    """A one-face mesh with these UV maps, showing *tree* when one is given."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    for uv_name in uv_names:
        mesh.uv_layers.new(name=uv_name)
    mesh.uv_layers[active_render].active_render = True
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    if tree is not None:
        use_tree(obj, tree)
    return obj


def expect_composite(context, tree, node, label, mesh=None):
    """Resolve a stack that should composite, and return its plan.

    With no GPU context here the same stack goes to the bake instead, and
    None comes back. The bake needs a mesh, so it is refused unless the
    caller names the *mesh* it should find.
    """
    if HAS_GPU:
        plan = layer_plan.resolve_input(context, tree, node)
        check(plan.is_composite, f"{label}: {plan.path} {plan.reason}")
        return plan
    if mesh is None:
        message = refusal(context, tree, node)
        check("needs a mesh to bake on" in message and "GPU context" in message,
              f"{label}: no GPU context here, so refused: {message}")
        return None
    plan = layer_plan.resolve_input(context, tree, node)
    check(plan.path == layer_plan.BAKE and "GPU context" in plan.reason and plan.surface == mesh,
          f"{label}: no GPU context here, so {plan.path} on {plan.surface} -- {plan.reason}")
    return None


def refusal(context, tree, node):
    """The message `resolve_input` refuses with, or "" when it does not."""
    try:
        layer_plan.resolve_input(context, tree, node)
    except filters_core.Refused as error:
        return str(error)
    return ""


def expect_refusal(context, tree, node, wanted, label, composite_only=False):
    """Check that resolving refuses with a message holding *wanted*.

    The rules on which mesh answers come from `surface_of` and hold for
    the bake too. The rules on UV maps agreeing are *composite_only*.
    With no GPU context the stack goes to the bake, which renders each
    layer through its own map, so those have nothing to check there.
    """
    if HAS_GPU or not composite_only:
        message = refusal(context, tree, node)
        check(wanted in message, f"{label}: {message}")


try:
    camera = bpy.data.objects.get("Camera")

    section("a stack that needs no object")
    tree = bpy.data.node_groups.new("Plan", 'PaintSystemNodeTree')
    tree.initialize()
    with core.suspend_compile(tree):
        bottom = tree.insert_layer_node(SOLID)
        node = tree.insert_layer_node(FILTER)
    core.flush_now()

    plan = expect_composite(FakeContext(), tree, node, "solid colours with no object selected")
    if plan is not None:
        check(plan.source == bottom, "the plan names the node feeding the filter's Color input")
        check(plan.uv_map == "" and plan.surface is None,
              "and leaves the UV map to the active render one, with no mesh")

    section("an image below")
    with core.suspend_compile(tree):
        picture = tree.insert_layer_node(IMAGE, target=bottom)
        picture.image = create_managed_image("Plan Image", 8, 8)
    core.flush_now()
    expect_composite(FakeContext(), tree, node, "an image layer that names no UV map")

    section("one named map needs no mesh")
    # The filter layer's own UV Map is empty, so it follows the one map
    # the layers below name. They agree on it without a mesh.
    picture.uv_map = "UVMap"
    core.flush_now()
    plan = expect_composite(FakeContext(), tree, node, "a named map with nothing selected")
    if plan is not None:
        check(plan.uv_map == "UVMap" and plan.surface is None,
              f"is laid out in that map ({plan.uv_map!r}) with no mesh")
    expect_composite(FakeContext(camera), tree, node, "and the same with the camera active")

    node.uv_map = "UVMap"
    core.flush_now()
    expect_composite(FakeContext(), tree, node, "the filter naming the same map")
    node.uv_map = "Other"
    core.flush_now()
    expect_refusal(FakeContext(), tree, node,
                   f"'{node.name}' is set to UV map 'Other', but the layers below use 'UVMap'",
                   "a filter naming another map is refused without asking a mesh, "
                   "and the message blames the filter", composite_only=True)
    node.uv_map = ""
    picture.uv_map = ""
    core.flush_now()

    solids = bpy.data.node_groups.new("Plan Solids", 'PaintSystemNodeTree')
    solids.initialize()
    with core.suspend_compile(solids):
        solids.insert_layer_node(SOLID)
        named_filter = solids.insert_layer_node(FILTER)
        named_filter.uv_map = "UVMap"
    core.flush_now()
    plan = expect_composite(FakeContext(), solids, named_filter,
                            "solid colours under a filter that names a map")
    if plan is not None:
        check(plan.uv_map == "UVMap", f"which lays it out in that map ({plan.uv_map!r})")

    section("a stack that needs a mesh")
    # One layer names the map and another leaves it empty, so only a mesh
    # can say whether its render map is the named one.
    with core.suspend_compile(tree):
        named = tree.insert_layer_node(IMAGE, target=picture)
        named.image = create_managed_image("Plan Named", 8, 8)
        named.uv_map = "UVMap"
    core.flush_now()
    plane = mesh_with_uvs("Plan Plane", ["UVMap", "UVMap.001"], tree=tree)
    stranger = mesh_with_uvs("Plan Stranger", ["UVMap"])

    expect_refusal(FakeContext(), tree, node, "nothing is selected",
                   "with nothing selected it is refused")
    expect_refusal(FakeContext(camera), tree, node, "'Camera' is not a mesh",
                   "and with only the camera selected")
    expect_refusal(FakeContext(stranger), tree, node, "'Plan Stranger' is not a mesh that uses",
                   "a mesh that does not show the tree is not asked")
    plan = expect_composite(FakeContext(plane), tree, node, "a mesh that shows the tree", plane)
    if plan is not None:
        check(plan.uv_map == "UVMap" and plan.surface == plane,
              f"resolves it on that mesh ({plan.uv_map!r}, {plan.surface})")

    handle = bpy.data.objects.new("Plan Handle", None)
    bpy.context.scene.collection.objects.link(handle)
    handle.parent = plane
    plan = expect_composite(FakeContext(handle), tree, node, "an empty parented to that mesh",
                            plane)
    if plan is not None:
        check(plan.surface == plane, f"stands in for the mesh ({plan.surface})")

    plane.data.uv_layers["UVMap.001"].active_render = True
    expect_refusal(FakeContext(plane), tree, node, "different UV maps on 'Plan Plane'",
                   "a render map that is not the named one is refused", composite_only=True)
    plane.data.uv_layers["UVMap"].active_render = True
    named.uv_map = "Nonexistent"
    core.flush_now()
    expect_refusal(FakeContext(plane), tree, node, "no UV map named",
                   "a map the mesh does not have is refused", composite_only=True)
    picture.uv_map = "UVMap.001"
    named.uv_map = "UVMap"
    core.flush_now()
    expect_refusal(FakeContext(plane), tree, node,
                   f"Layers below '{node.name}' use different UV maps ('UVMap' and 'UVMap.001')",
                   "two layers below naming different maps are refused", composite_only=True)
    picture.uv_map = ""
    named.uv_map = ""
    node.uv_map = "UVMap.001"
    core.flush_now()
    expect_refusal(FakeContext(plane), tree, node,
                   f"'{node.name}' is set to UV map 'UVMap.001', but the layers below use "
                   "the render map 'UVMap' on 'Plan Plane'",
                   "a filter naming a map other than the render map the layers below use "
                   "is blamed itself", composite_only=True)
    node.uv_map = ""
    named.uv_map = "UVMap"
    core.flush_now()

    section("the layer's Object")
    offered = filter_layer_node._surface_search(node, bpy.context, "")
    check(plane.name in offered and stranger.name not in offered and camera.name not in offered,
          f"the Object search offers only meshes that show the tree ({offered})")

    node.surface_name = plane.name
    check(node.surface_object == plane, "the Object is stored by name")
    for label, context in (("nothing", FakeContext()), ("the camera", FakeContext(camera)),
                           ("another mesh", FakeContext(stranger))):
        plan = expect_composite(context, tree, node, f"with {label} selected", plane)
        if plan is not None:
            check(plan.surface == plane, f"the stored mesh is used ({plan.surface})")

    ghost = mesh_with_uvs("Plan Ghost", ["UVMap"], tree=tree)
    node.surface_name = ghost.name
    # Deleting an object in the viewport only unlinks it while anything
    # else points at it, such as a modifier.
    for collection in ghost.users_collection:
        collection.objects.unlink(ghost)
    check(ghost.name not in filter_layer_node._surface_search(node, bpy.context, ""),
          "the search does not offer a deleted mesh")
    expect_refusal(FakeContext(), tree, node, "its Object 'Plan Ghost' was deleted",
                   "a deleted Object is refused by name")
    plan = expect_composite(FakeContext(plane), tree, node, "and the active mesh stands in", plane)
    if plan is not None:
        check(plan.surface == plane, f"in its place ({plan.surface})")
        layer_plan.keep_surface(node, plan)
        check(node.surface_name == plane.name, "and a build going ahead stores the stand-in")

    node.surface_name = stranger.name
    expect_refusal(FakeContext(), tree, node, "does not use this Paint System tree",
                   "an Object set to a mesh that does not show the tree")
    node.surface_name = camera.name
    expect_refusal(FakeContext(), tree, node, "its Object 'Camera' is not a mesh",
                   "and one that is not a mesh at all")

    removed = mesh_with_uvs("Plan Removed", ["UVMap"], tree=tree)
    node.surface_name = removed.name
    bpy.data.objects.remove(removed)
    check(node.surface_object is None, "removing the Object's mesh leaves a name that finds nothing")
    expect_refusal(FakeContext(), tree, node, "its Object 'Plan Removed' was deleted or renamed",
                   "so the stack that needs a mesh says so and waits")

    renamed = mesh_with_uvs("Plan Renamed", ["UVMap"], tree=tree)
    node.surface_name = renamed.name
    renamed.name = "Plan Renamed Again"
    expect_refusal(FakeContext(), tree, node, "its Object 'Plan Renamed' was deleted or renamed",
                   "renaming the Object's mesh loses it")
    plan = expect_composite(FakeContext(renamed), tree, node, "until it is selected again", renamed)
    if plan is not None:
        layer_plan.keep_surface(node, plan)
        check(node.surface_name == "Plan Renamed Again",
              f"and a build stores it under its new name ({node.surface_name!r})")
    named.uv_map = ""
    core.flush_now()
    expect_composite(FakeContext(), tree, node, "while a stack that needs none still resolves")

    section("keeping the mesh")
    # Composite plans only: a bake plan always carries its mesh.
    node.surface_name = ""
    if HAS_GPU:
        none_needed = layer_plan.resolve_input(FakeContext(plane), tree, node)
        layer_plan.keep_surface(node, none_needed)
        check(node.surface_name == "", "a plan that needed no mesh stores none")
    named.uv_map = "UVMap"
    core.flush_now()
    if HAS_GPU:
        needed = layer_plan.resolve_input(FakeContext(plane), tree, node)
        check(node.surface_name == "", "resolving alone stores nothing")
        layer_plan.keep_surface(node, needed)
        check(node.surface_name == plane.name, "keep_surface stores the mesh the plan needed")

    # A build of a linked layer lasts for the session, so the mesh it
    # needed is stored beside it. Otherwise the result would be stamped
    # for a mesh the layer does not name, and look out of date for good.
    library_path = os.path.join(tempfile.gettempdir(), "ps_plan_library.blend")
    shared = bpy.data.node_groups.new("Plan Shared", 'PaintSystemNodeTree')
    shared.initialize()
    shared.insert_layer_node(FILTER).surface_name = stranger.name
    core.flush_now()
    bpy.data.libraries.write(library_path, {shared}, fake_user=True)
    bpy.data.node_groups.remove(shared)
    objects = len(bpy.data.objects)
    with bpy.data.libraries.load(library_path, link=True) as (_, linked):
        linked.node_groups = ["Plan Shared"]
    shared = linked.node_groups[0]
    library = shared.library
    try:
        # The Object is a name, so it does not make the mesh part of the
        # tree. A pointer would have brought the library's copy along.
        check(len(bpy.data.objects) == objects, "loading a tree brings no mesh with it")
        shared_filter = next(n for n in shared.nodes if n.bl_idname == FILTER)
        linked_plan = layer_plan.InputPlan(layer_plan.COMPOSITE, shared_filter, None,
                                           "UVMap", "", plane)
        layer_plan.keep_surface(shared_filter, linked_plan)
        # The write marks the linked tree for a compile, which must run
        # before the library goes away.
        core.flush_now()
        check(not shared.is_editable and shared_filter.surface_name == plane.name,
              "a linked layer stores the mesh its build needed")
    finally:
        bpy.data.libraries.remove(library)
        os.remove(library_path)

    section("a nested tree")
    # A tree wrapped by a group layer compiles into the outer tree's
    # group, so a mesh showing the outer tree shows the inner one too.
    inner = bpy.data.node_groups.new("Plan Nested", 'PaintSystemNodeTree')
    inner.initialize()
    with core.suspend_compile(inner):
        inner_picture = inner.insert_layer_node(IMAGE)
        inner_picture.image = create_managed_image("Plan Nested Image", 8, 8)
        inner_named = inner.insert_layer_node(IMAGE)
        inner_named.image = create_managed_image("Plan Nested Named", 8, 8)
        inner_named.uv_map = "UVMap"
        inner_filter = inner.insert_layer_node(FILTER)
    core.flush_now()
    outer = bpy.data.node_groups.new("Plan Outer", 'PaintSystemNodeTree')
    outer.initialize()
    wrapper = outer.nodes.new('PaintSystemGroupLayerNode')
    wrapper.node_tree = inner
    outer_mesh = mesh_with_uvs("Plan Outer Mesh", ["UVMap"], tree=outer)
    plan = expect_composite(FakeContext(outer_mesh), inner, inner_filter,
                            "a mesh showing the outer tree", outer_mesh)
    if plan is not None:
        check(plan.surface == outer_mesh, f"resolves the inner tree's layer ({plan.surface})")

    section("what falls back to a bake")
    node.surface_name = ""
    named.uv_map = ""
    core.flush_now()
    group_inner = bpy.data.node_groups.new("Plan Inner", 'PaintSystemNodeTree')
    group_inner.initialize()
    group = tree.nodes.new('PaintSystemGroupLayerNode')
    group.node_tree = group_inner
    tree.links.new(group.outputs['Color'], bottom.inputs['Color'])
    plan = layer_plan.resolve_input(FakeContext(plane), tree, node)
    check(plan.path == layer_plan.BAKE and group.name in plan.reason,
          f"a group layer below: {plan.path} -- {plan.reason}")
    check(plan.uv_map == "UVMap" and plan.surface == plane,
          f"the bake plan carries the mesh and its UV map ({plan.uv_map!r}, {plan.surface})")

    message = refusal(FakeContext(), tree, node)
    check("needs a mesh to bake on" in message,
          f"and with nothing selected there is nowhere to bake: {message}")
    message = refusal(FakeContext(stranger), tree, node)
    check("needs a mesh to bake on" in message,
          f"nor on a mesh that does not show the tree: {message}")
    tree.links.remove(bottom.inputs['Color'].links[0])
    tree.nodes.remove(group)
    core.flush_now()

    section("nothing to filter")
    empty = bpy.data.node_groups.new("Plan Empty", 'PaintSystemNodeTree')
    empty.initialize()
    alone = empty.insert_layer_node(FILTER)
    core.flush_now()
    message = refusal(FakeContext(plane), empty, alone)
    check("nothing below" in message, f"a filter layer on its own is refused: {message}")

    section("the channel a layer feeds")
    check(layer_plan.channel_of(tree, node) is not None,
          "a top-level layer reports its channel")
    with core.suspend_compile(tree):
        folder = tree.insert_layer_node(FOLDER, target=picture)
        inside = tree.insert_layer_node(SOLID, target=folder)
    core.flush_now()
    outer_channel = layer_plan.channel_of(tree, node)
    check(layer_plan.channel_of(tree, inside) == outer_channel,
          "and so does one inside a folder, through the folder")

    section("a float channel")
    tree.create_channel("Height", 'FLOAT')
    with core.suspend_compile(tree):
        ground = tree.insert_layer_node(SOLID, channel_name="Height")
        float_filter = tree.insert_layer_node(FILTER, channel_name="Height")
    core.flush_now()
    channel = layer_plan.channel_of(tree, float_filter)
    check(channel is not None and channel.type == 'FLOAT',
          "a layer in a float channel reports it")
    check(layer_plan.channel_of(tree, ground) == channel, "and so does the one below it")
    message = refusal(FakeContext(plane), tree, float_filter)
    check("colour channels" in message,
          f"a filter layer on a float channel is refused: {message}")

    section("a detached layer")
    stack_ops.detach(tree, inside)
    check(layer_plan.channel_of(tree, inside) is None,
          "a layer that reaches no output has no channel")

except Exception:
    traceback.print_exc()
    check(False, "unexpected exception")

finish("FILTER PLAN TEST")
