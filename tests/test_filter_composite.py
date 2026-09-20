"""The GPU composite draws the same stack the render engines do (PS-057).

`filters.composite` exists so a filter layer can be handed the picture
under it without a multi-second Cycles bake. It is only worth having if
the two agree, so every arrangement here is built as a real Paint System
stack, composited on the GPU, and compared with a Cycles bake of the
artifact that stack compiles to.

The stack is read through an unbuilt filter layer on top, which is an
exact pass-through (`tests/test_filter_layer.py`), so the bake of the
whole channel is the bake of everything below it.

These need a GPU context. Blender 5.2 added `gpu.init()`, which builds
one in background mode, so they run in the ordinary headless job there.
Background 4.2 to 5.1 have no way to get one and skip; that coverage
comes from the windowed job.
"""
import os
import sys
import traceback

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, finish, fmt, import_from,  # noqa: E402
                     register_addon, section, skip)

register_addon()
gpu_core = import_from("gpu_passes.core")
composite = import_from("filters.composite")
core = import_from("compiler.core")
derived = import_from("filters.derived")
filters_core = import_from("filters.core")
stack_ops = import_from("nodetree.stack_ops")
create_managed_image = import_from("compiler.bake").create_managed_image

SOLID = 'PaintSystemSolidColorLayerNode'
IMAGE = 'PaintSystemImageLayerNode'
FOLDER = 'PaintSystemFolderLayerNode'
FILTER = 'PaintSystemFilterLayerNode'

# The bake plane's UV covers 0..1 exactly, so an image of this size lands
# one texel per baked pixel and neither side interpolates.
SIZE = 16
# The ticket's number. Cycles bakes on the CPU into a float image, the
# pass runs on whatever GPU is here and carries RGBA16F.
TOL = 2.0 / 255.0


def available():
    if gpu_core.gpu_available():
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


def composited(node, pool=None):
    plan = composite.plan_below(node)
    texture = composite.composite_below(plan, (SIZE, SIZE), pool=pool)
    return composite.read_texture(texture, (SIZE, SIZE))


def compare(tree, node, label):
    """Composite the stack below *node* and check it against a bake of it."""
    got = composited(node)
    want = bake_group(tree.compiled, size=SIZE).reshape(SIZE, SIZE, 4)
    delta = np.abs(got - want)
    # A texel neither side shows carries no colour, so only its alpha
    # means anything.
    visible = np.maximum(got[..., 3], want[..., 3]) > TOL
    delta[..., :3] *= visible[..., None]
    worst = float(delta.max())
    where = np.unravel_index(int(delta.max(axis=2).argmax()), (SIZE, SIZE))
    middle = tuple(float(v) for v in got[SIZE // 2, SIZE // 2])
    check(worst <= TOL,
          f"{label}: worst {worst * 255:.2f}/255 at {where[1]},{where[0]}; "
          f"middle {fmt(middle)} baked {fmt(want[SIZE // 2, SIZE // 2])}")


def ramp_image(name):
    """An image whose every texel differs, so a flip or a swap shows up."""
    image = create_managed_image(name, SIZE, SIZE)
    x, y = np.meshgrid(np.arange(SIZE), np.arange(SIZE))
    values = np.stack([x / (SIZE - 1), y / (SIZE - 1),
                       (x + y) / (2 * SIZE - 2), np.full((SIZE, SIZE), 0.75)], axis=2)
    image.pixels.foreach_set(values.astype(np.float32).ravel())
    image.update()
    return image


def built_filter_image(name, color):
    """An image that looks to the compiler like a finished filter result."""
    image = create_managed_image(name, SIZE, SIZE, colorspace='Non-Color')
    image.pixels.foreach_set(list(color) * (SIZE * SIZE))
    image.update()
    image[derived.BUILD_KEY] = f"build-of-{name}"
    image[derived.UV_MAP_KEY] = ""
    return image


def fresh_tree(name):
    tree = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    tree.initialize()
    return tree


def above(tree, bl_idname, target):
    """Add a layer directly above *target*, even when *target* is a folder.

    ``insert_layer_node(target=folder)`` puts the layer inside the folder,
    which is right for the UI and wrong for building a stack around one.
    """
    with core.suspend_compile(tree):
        node = tree.nodes.new(bl_idname)
        stack_ops.insert_above(tree, node, target)
    return node


if available():
    try:
        section("one layer")
        tree = fresh_tree("Composite One")
        with core.suspend_compile(tree):
            bottom = tree.insert_layer_node(SOLID)
            bottom.fill_color = (0.2, 0.6, 0.4, 1.0)
            top = tree.insert_layer_node(FILTER)
        core.flush_now()
        compare(tree, top, "a single opaque solid")

        bottom.fill_color = (0.2, 0.6, 0.4, 0.5)
        core.flush_now()
        compare(tree, top, "a half-transparent solid over nothing")

        section("a blended stack")
        with core.suspend_compile(tree):
            bottom.fill_color = (0.8, 0.7, 0.6, 1.0)
            middle = tree.insert_layer_node(SOLID, target=bottom)
            middle.fill_color = (0.3, 0.4, 0.25, 1.0)
            middle.blend_mode = 'MULTIPLY'
            middle.opacity = 0.6
        core.flush_now()
        compare(tree, top, "a MULTIPLY layer at 0.6 over a solid")

        middle.inputs['Mask'].default_value = 0.35
        core.flush_now()
        compare(tree, top, "the same with the Mask input turned down")
        middle.inputs['Mask'].default_value = 1.0

        middle.enabled = False
        core.flush_now()
        compare(tree, top, "a disabled layer contributes nothing")
        middle.enabled = True

        section("an image layer")
        with core.suspend_compile(tree):
            picture = tree.insert_layer_node(IMAGE, target=middle)
            picture.image = ramp_image("Composite Ramp")
            picture.blend_mode = 'SCREEN'
        core.flush_now()
        compare(tree, top, "a ramp image over the stack, SCREEN")

        picture.blend_mode = 'MIX'
        picture.opacity = 0.5
        core.flush_now()
        compare(tree, top, "the same image at half opacity, MIX")

        section("a folder")
        with core.suspend_compile(tree):
            folder = tree.insert_layer_node(FOLDER, target=picture)
            folder.opacity = 0.5
            folder.blend_mode = 'ADD'
            inner_bottom = tree.insert_layer_node(SOLID, target=folder)
            inner_bottom.fill_color = (0.1, 0.2, 0.9, 0.8)
            inner_top = tree.insert_layer_node(SOLID, target=inner_bottom)
            inner_top.fill_color = (0.9, 0.2, 0.1, 0.4)
            inner_top.blend_mode = 'DIFFERENCE'
        core.flush_now()
        compare(tree, top, "a folder of two layers at 0.5, ADD")

        section("a clip run")
        clipped = above(tree, SOLID, folder)
        with core.suspend_compile(tree):
            clipped.fill_color = (1.0, 0.0, 1.0, 1.0)
            clipped.is_clip = True
            clipped.opacity = 0.7
        core.flush_now()
        compare(tree, top, "one layer clipped to the folder")

        with core.suspend_compile(tree):
            second_clipped = tree.insert_layer_node(SOLID, target=clipped)
            second_clipped.fill_color = (0.0, 0.9, 0.9, 0.6)
            second_clipped.is_clip = True
            second_clipped.blend_mode = 'OVERLAY'
        core.flush_now()
        compare(tree, top, "two layers clipped to the same base")

        with core.suspend_compile(tree):
            over_run = tree.insert_layer_node(SOLID, target=second_clipped)
            over_run.fill_color = (0.4, 0.4, 0.1, 0.5)
        core.flush_now()
        compare(tree, top, "an ordinary layer above a clip run")

        section("a filter layer below the filter layer")
        with core.suspend_compile(tree):
            inner_filter = tree.insert_layer_node(FILTER, target=over_run)
            inner_filter.derived_image = built_filter_image("Composite Inner", (0.9, 0.3, 0.1, 1.0))
            inner_filter.opacity = 0.6
        core.flush_now()
        compare(tree, top, "a built filter layer replacing the stack at Amount 0.6")

        inner_filter.derived_image = None
        core.flush_now()
        compare(tree, top, "and an unbuilt one passing it through")

        section("the pool")
        # The targets in flight are the backdrop, the layer's own content
        # and one held backdrop per open clip run, so the count follows
        # the nesting rather than the depth of the stack.
        inner_filter.derived_image = built_filter_image("Composite Inner 2", (0.2, 0.5, 0.8, 1.0))
        core.flush_now()
        pool = composite.Pool((SIZE, SIZE))
        plan = composite.plan_below(top)
        result = composite.composite_below(plan, (SIZE, SIZE), pool=pool)
        check(pool.made <= 6, f"{len(plan.layers)} layers deep, "
                              f"{len(plan.images)} images, {pool.made} targets allocated")
        pool.release(result)
        before = pool.made
        composite.composite_below(plan, (SIZE, SIZE), pool=pool)
        check(pool.made == before, "a second composite through the same pool allocates none")
        pool.close()
        result = None

        section("what falls back to the bake")
        # Any link into Mask, from a layer below so the graph stays acyclic.
        tree.links.new(bottom.outputs['Alpha'], over_run.inputs['Mask'])
        try:
            composite.plan_below(top)
            check(False, "a linked mask plans")
        except composite.Unsupported as error:
            check(over_run.name in str(error), f"a linked mask falls back: {error}")
        tree.links.remove(over_run.inputs['Mask'].links[0])

        section("what neither path can read")
        picture.image.source = 'TILED'
        try:
            composite.plan_below(top)
            check(False, "a UDIM image plans")
        except filters_core.Refused as error:
            check("UDIM" in str(error), f"a UDIM image is refused: {error}")
        picture.image.source = 'GENERATED'

        missing = bpy.data.images.new("Composite Missing", SIZE, SIZE)
        missing.source = 'FILE'
        missing.filepath = "//no-such-file.png"
        picture.image = missing
        try:
            composite.plan_below(top)
            check(False, f"an image with no pixels plans (has_data {missing.has_data})")
        except filters_core.Refused as error:
            check("no pixels" in str(error), f"an image with no pixels is refused: {error}")

        section("what the plan reports")
        picture.image = ramp_image("Composite Ramp 2")
        picture.uv_map = "SomeMap"
        plan = composite.plan_below(top)
        check(plan.uv_maps == {"SomeMap", ""},
              f"the plan gathers the UV map of every image below, folders included: {sorted(plan.uv_maps)}")
        check(len(plan.images) == 2,
              f"and the images themselves: {[image.name for image in plan.images]}")
        check(all(step.node.name != inner_bottom.name for step in plan.layers),
              "a folder's content is a chain of its own, not part of the outer one")

    except Exception:
        traceback.print_exc()
        check(False, "unexpected exception")

# Python's own teardown would free them after the GPU context has gone,
# which segfaults a background Blender.
composite.release()
import_from("filters.blend_glsl").release()

finish("FILTER COMPOSITE TEST")
