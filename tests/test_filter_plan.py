"""Which path a filter layer's input comes down (PS-057).

`filters.layer_plan.resolve_input` decides between the GPU composite and
a Cycles bake, and refuses when neither can run. It allocates nothing, so
these need no GPU context and run everywhere -- what they check is the
decision, not the pixels. `tests/test_filter_composite.py` checks the
pixels.

A composite samples every source image by normalised coordinate, so the
whole stack below has to share one UV map with the derived image.
Deciding that is most of what is tested here: no object at all is fine
while nothing names a map, and as soon as one layer does, a mesh has to
be asked.
"""
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (check, finish, import_from, register_addon,  # noqa: E402
                     section)

register_addon()
core = import_from("compiler.core")
gpu_core = import_from("gpu_passes.core")
layer_plan = import_from("filters.layer_plan")
filters_core = import_from("filters.core")
stack_ops = import_from("nodetree.stack_ops")
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


def mesh_with_uvs(name, uv_names, active_render=0):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    for uv_name in uv_names:
        mesh.uv_layers.new(name=uv_name)
    mesh.uv_layers[active_render].active_render = True
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def expect_composite(plan, label):
    """A plan that should composite -- or, with no GPU here, bake and say so."""
    if HAS_GPU:
        check(plan.is_composite, f"{label}: {plan.path} {plan.reason}")
    else:
        check(plan.path == layer_plan.BAKE and "GPU context" in plan.reason,
              f"{label}: no GPU context here, so {plan.path} -- {plan.reason}")


def refusal(context, tree, node):
    """The message `resolve_input` refuses with, or "" when it does not."""
    try:
        layer_plan.resolve_input(context, tree, node)
    except filters_core.Refused as error:
        return str(error)
    return ""


try:
    section("a stack that needs no object")
    tree = bpy.data.node_groups.new("Plan", 'PaintSystemNodeTree')
    tree.initialize()
    with core.suspend_compile(tree):
        bottom = tree.insert_layer_node(SOLID)
        node = tree.insert_layer_node(FILTER)
    core.flush_now()

    plan = layer_plan.resolve_input(FakeContext(), tree, node)
    expect_composite(plan, "solid colours with no object selected")
    check(plan.source == bottom, "the plan names the node feeding the filter's Color input")
    check(plan.uv_map == "", "and leaves the UV map to the active render one")

    section("an image below")
    with core.suspend_compile(tree):
        picture = tree.insert_layer_node(IMAGE, target=bottom)
        picture.image = create_managed_image("Plan Image", 8, 8)
    core.flush_now()
    expect_composite(layer_plan.resolve_input(FakeContext(), tree, node),
                     "an image layer that names no UV map")

    picture.uv_map = "UVMap"
    core.flush_now()
    message = refusal(FakeContext(), tree, node)
    check("active mesh object" in message,
          f"naming a UV map with no object is refused: {message}")

    plane = mesh_with_uvs("Plan Plane", ["UVMap", "UVMap.001"])
    plan = layer_plan.resolve_input(FakeContext(plane), tree, node)
    expect_composite(plan, "the same named map against a mesh that has it")
    if plan.is_composite:
        check(plan.uv_map == "UVMap", f"resolved to {plan.uv_map!r}")

    picture.uv_map = "UVMap.001"
    core.flush_now()
    message = refusal(FakeContext(plane), tree, node)
    check("different UV maps" in message,
          f"a named map that is not the active render one is refused: {message}")

    picture.uv_map = "Nonexistent"
    core.flush_now()
    message = refusal(FakeContext(plane), tree, node)
    check("no UV map named" in message, f"a map the mesh does not have is refused: {message}")
    picture.uv_map = ""
    core.flush_now()

    section("what falls back to a bake")
    inner = bpy.data.node_groups.new("Plan Inner", 'PaintSystemNodeTree')
    inner.initialize()
    group = tree.nodes.new('PaintSystemGroupLayerNode')
    group.node_tree = inner
    tree.links.new(group.outputs['Color'], bottom.inputs['Color'])
    plan = layer_plan.resolve_input(FakeContext(plane), tree, node)
    check(plan.path == layer_plan.BAKE and group.name in plan.reason,
          f"a group layer below: {plan.path} -- {plan.reason}")
    check(plan.obj == plane and plan.uv_map == "UVMap",
          f"the bake plan carries the object and its resolved UV map ({plan.uv_map!r})")

    message = refusal(FakeContext(), tree, node)
    check("active mesh object" in message,
          f"and with nothing selected there is nowhere to bake: {message}")
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
    outer = layer_plan.channel_of(tree, node)
    check(layer_plan.channel_of(tree, inside) == outer,
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
