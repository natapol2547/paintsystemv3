"""What happens to a filter layer's image when the layer does (PS-057).

A filter result is the layer's content rather than a re-derivable
artifact, which decides all of this: duplicating a layer copies it,
removing one removes it, and an orphan is swept rather than carried into
the next file. A cache image is the other way round on every point, which
is why the two are not handled together.

No GPU: the images are stamped by hand with what a build would have
stamped them with, and nothing here reads a pixel.
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
derived = import_from("filters.derived")
handlers = import_from("handlers.node_tree_handlers")
create_managed_image = import_from("compiler.bake").create_managed_image

SOLID = 'PaintSystemSolidColorLayerNode'
FOLDER = 'PaintSystemFolderLayerNode'
FILTER = 'PaintSystemFilterLayerNode'


def built(node, name):
    """An image stamped as though *node* had just built it."""
    image = create_managed_image(name, 8, 8)
    # A generated image has no buffer until something touches its pixels,
    # and `pack` on one with no buffer quietly does nothing. A build
    # writes before it packs for the same reason.
    image.pixels.foreach_set([0.5, 0.5, 0.5, 1.0] * 64)
    image.update()
    image.pack()
    image[derived.OWNER_KEY] = f"{node.id_data.uuid}:{node.uuid}"
    image[derived.BUILD_KEY] = "hand-stamped"
    image[derived.UV_MAP_KEY] = ""
    node.derived_image = image
    return image


def remove(context, tree, node):
    """Run the remove-layer operator on *node*, dialog and all."""
    context.scene.paint_system.active_node_tree = tree
    tree.activate_layer_node(node)
    return bpy.ops.paint_system.remove_layer('EXEC_DEFAULT')


try:
    section("removing the layer")
    tree = bpy.data.node_groups.new("Life", 'PaintSystemNodeTree')
    tree.initialize()
    with core.suspend_compile(tree):
        tree.insert_layer_node(SOLID)
        node = tree.insert_layer_node(FILTER)
    core.flush_now()
    name = built(node, "Life Result").name

    check(remove(bpy.context, tree, node) == {'FINISHED'}, "removes the layer")
    check(name not in bpy.data.images,
          "and its image with it, rather than leaving an orphan behind")

    section("removing a folder holding one")
    with core.suspend_compile(tree):
        folder = tree.insert_layer_node(FOLDER)
        inner = tree.insert_layer_node(FILTER, target=folder)
    core.flush_now()
    name = built(inner, "Life Inner Result").name
    check(remove(bpy.context, tree, folder) == {'FINISHED'}, "removes the folder")
    check(name not in bpy.data.images, "and the image of the filter layer inside it")

    section("duplicating one")
    with core.suspend_compile(tree):
        node = tree.insert_layer_node(FILTER)
    core.flush_now()
    image = built(node, "Life Copy Result")
    copy = tree.nodes.new(FILTER)
    # Blender duplicates the properties itself and then calls `copy` to
    # fix up what a shared pointer would break, so the pointer is already
    # the original's by the time the hook runs.
    copy.derived_image = image
    copy.copy(node)
    check(copy.derived_image is not None and copy.derived_image != image,
          "the duplicate gets its own image, not a second pointer at the original's")
    check(derived.is_built(copy.derived_image),
          "which arrives built: the stamps and the packed pixels both come with a copy")
    check(tuple(copy.derived_image.pixels[:4]) == tuple(image.pixels[:4]),
          "and holds the same pixels, because the original was packed")
    tree.nodes.remove(copy)

    section("the save path")
    images = handlers.paint_system_images()
    check(image in images, "a result a layer points at is saved with the file")
    node.derived_image = None
    check(image not in handlers.paint_system_images(),
          "one nothing points at is not, so an orphan is never packed into the next file")

    section("the sweep")
    # A node-editor delete, a removed channel and a file from an older
    # build all leave results behind that the remove operator never saw.
    kept = bpy.data.node_groups.new("Life Kept", 'PaintSystemNodeTree')
    kept.initialize()
    with core.suspend_compile(kept):
        live = kept.insert_layer_node(FILTER)
    core.flush_now()
    live_image = built(live, "Life Kept Result")

    unrelated = create_managed_image("Life Unrelated", 8, 8)
    check(derived.cleanup_orphan_derived() == 1, "the orphan is removed")
    check("Life Copy Result" not in bpy.data.images, "by name, it is the one that went")
    check(live_image.name in bpy.data.images, "the result a layer still points at stays")
    check(unrelated.name in bpy.data.images, "and an image that is not a filter result is not touched")

    live_image.use_fake_user = True
    node.derived_image = None
    check(derived.cleanup_orphan_derived() == 0,
          "a second sweep has nothing to do")

except Exception:
    traceback.print_exc()
    check(False, "unexpected exception")

finish("FILTER LIFECYCLE TEST")
