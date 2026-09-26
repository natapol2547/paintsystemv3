"""Linked layers, and copy and paste of layers (PS-016, PS-017).

Linked layers are nodes of one type in one tree with the same
``link_id``. A change to a linked setting on one is copied to the
others. These check what is linked and what is not, how links start
and end, how copies of linked layers behave, the clipboard, the
operators and the layer list, and the tab that marks a linked node.

Run:  blender -b --factory-startup --python tests/test_links.py
"""
import os
import sys
import tempfile
from contextlib import contextmanager
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (RecordingLayout, bake_group, check, close, finish, guarded, import_from,  # noqa: E402
                     pixel_at, register_addon, section, skip)

register_addon()
core = import_from("compiler.core")
IR = import_from("compiler.ir").IR
bake = import_from("compiler.bake")
links = import_from("nodes.layers.links")
clipboard = import_from("nodes.layers.clipboard")
registry = import_from("nodes.layers.registry")
link_tabs = import_from("nodes.link_tabs")
link_ops = import_from("ops.link_ops")
layers_panels = import_from("panels.layers_panels")
stack_ops = import_from("nodetree.stack_ops")
gpu_core = import_from("gpu_passes.core")
common = import_from("common")

SOLID = 'PaintSystemSolidColorLayerNode'
FOLDER = 'PaintSystemFolderLayerNode'
IMAGE = 'PaintSystemImageLayerNode'
FILTER = 'PaintSystemFilterLayerNode'
RED = (1.0, 0.0, 0.0, 1.0)
BLUE = (0.0, 0.0, 1.0, 1.0)
GREEN = (0.0, 1.0, 0.0, 1.0)


def new_tree(name):
    tree = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    tree.initialize()
    bpy.context.scene.paint_system.active_node_tree = tree
    return tree


def add(tree, bl_idname, name, target=None, channel=None):
    node = tree.insert_layer_node(bl_idname, channel_name=channel, target=target)
    node.name = name
    return node


def layout(tree):
    return [(item.node.name, item.level) for item in tree.stack()]


def fill_pixels(image, color):
    image.pixels.foreach_set(np.tile(np.array(color, dtype=np.float32), image.size[0] * image.size[1]))
    image.update()


def painted_image(name, color):
    """A generated image with unsaved painting: its pixels differ from what it generates."""
    image = bpy.data.images.new(name, 4, 4, alpha=True)
    fill_pixels(image, color)
    return image


def first_pixel(image):
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return tuple(round(float(value), 3) for value in pixels[:4])


def node_editor(tree):
    """A context override for a node editor that shows *tree*."""
    window = bpy.context.window_manager.windows[0]
    area = max(window.screen.areas, key=lambda area: area.width * area.height)
    area.type = 'NODE_EDITOR'
    space = area.spaces.active
    space.tree_type = 'PaintSystemNodeTree'
    space.node_tree = tree
    region = next(region for region in area.regions if region.type == 'WINDOW')
    return bpy.context.temp_override(window=window, area=area, region=region, space_data=space)


@contextmanager
def counted_applies():
    """A list that gets one entry for each artifact write inside the block."""
    applies = []
    apply = IR.apply

    def counting(self, *args, **kwargs):
        applies.append(self)
        return apply(self, *args, **kwargs)

    IR.apply = counting
    try:
        yield applies
    finally:
        IR.apply = apply


def select_only(tree, active, *others):
    for node in tree.nodes:
        node.select = node == active or node in others
    tree.nodes.active = active


def test_what_is_linked():
    section("every setting a layer declares is linked, unless the class says it is not")
    base = {prop.identifier for prop in bpy.types.Node.bl_rna.properties}
    for cls in registry.layer_types():
        unlinked = set()
        for klass in cls.__mro__:
            unlinked |= set(klass.__dict__.get('ps_unlinked_props', ()))
        declared = {prop.identifier for prop in cls.bl_rna.properties if prop.identifier not in base}
        expected = declared - unlinked - {'uuid'}
        check(set(cls.ps_linked_props) == expected,
              f"{cls.ps_type}: linked are the declared settings less {sorted(unlinked)} "
              f"(extra {sorted(set(cls.ps_linked_props) - expected)}, missing {sorted(expected - set(cls.ps_linked_props))})")
        check(unlinked <= declared | {'link_id'}, f"{cls.ps_type}: every unlinked name is a real property")
    check({'cache_enabled', 'cache_image', 'link_id'}.isdisjoint(registry.layer_type('SOLID_COLOR').ps_linked_props),
          "a cache and the link id are never linked")
    check('is_expanded' not in registry.layer_type('FOLDER').ps_linked_props, "a folder's expanded state is not linked")
    check('derived_image' not in registry.layer_type('FILTER').ps_linked_props
          and 'blur_sigma' in registry.layer_type('FILTER').ps_linked_props,
          "a filter's result is not linked, its settings are")


def test_link_and_sync():
    section("linking takes the source's settings, and changes copy both ways")
    tree = new_tree("Link Sync")
    a = add(tree, SOLID, "A")
    b = add(tree, SOLID, "B")
    a.fill_color = RED
    a.opacity = 0.5
    a.blend_mode = 'MULTIPLY'
    b.fill_color = BLUE
    fingerprint = core.build_ir(tree).fingerprint()
    b.link_id = "only-an-id"
    check(core.build_ir(tree).fingerprint() == fingerprint, "a link id alone does not change the compiled tree")
    b.link_id = ""

    check(links.link_candidates(a) == [b], "the only candidate is the other solid")
    links.link([b], a)
    check(a.link_id and a.link_id == b.link_id, f"both have one link id ({a.link_id!r}, {b.link_id!r})")
    check(tuple(b.fill_color) == RED and abs(b.opacity - 0.5) < 1e-6 and b.blend_mode == 'MULTIPLY',
          "B takes A's settings")
    check(tuple(a.fill_color) == RED, "A keeps its own")
    check(links.linked_layers(a) == [b] and links.linked_layers(b) == [a], "each lists the other")
    check(links.link_candidates(a) == [], "a linked layer is no longer a candidate")
    check(links.linked_names(tree) == {"A", "B"}, f"both are linked names {links.linked_names(tree)}")

    b.fill_color = GREEN
    check(tuple(a.fill_color) == GREEN, "a change on B reaches A")
    a.opacity = 0.25
    check(abs(b.opacity - 0.25) < 1e-6, "a change on A reaches B")
    a.lock_layer = True
    check(b.lock_layer, "the lock is linked")
    a.lock_layer = False

    section("a linked change compiles the tree once")
    tree = new_tree("Link Once")
    a = add(tree, SOLID, "A")
    b = add(tree, SOLID, "B")
    c = add(tree, SOLID, "C")
    a.fill_color = RED
    a.opacity = 0.5
    with counted_applies() as applies:
        links.link([b, c], a)
    check(len(applies) == 1, f"linking two layers that differ in two settings writes the artifact once ({len(applies)})")
    with counted_applies() as applies:
        b.opacity = 0.75
    check(len(applies) == 1 and abs(a.opacity - 0.75) < 1e-6 and abs(c.opacity - 0.75) < 1e-6,
          f"a change on one of three writes it once too ({len(applies)})")

    section("a change reaches the compiled result of the other layer")
    tree = new_tree("Link Pixels")
    tree.create_channel("Other", 'COLOR')
    shown = add(tree, SOLID, "Shown", channel="Color")
    other = add(tree, SOLID, "Other Layer", channel="Other")
    shown.fill_color = RED
    links.link([other], shown)
    other.fill_color = BLUE
    core.compile_tree(tree)
    rgba = bake_group(tree.compiled, color="Color", alpha="Color Alpha", size=4)
    check(close(pixel_at(rgba, 0.5, 0.5, 4)[:3], BLUE[:3]),
          f"the Color channel shows the colour set on the layer in Other {pixel_at(rgba, 0.5, 0.5, 4)}")

    section("settings that are not linked stay per layer")
    tree = new_tree("Link Unlinked")
    a = add(tree, SOLID, "A")
    b = add(tree, SOLID, "B")
    links.link([b], a)
    cache = bpy.data.images.new("Link Cache", 4, 4)
    a.cache_image = cache
    a.cache_enabled = True
    check(b.cache_image is None and not b.cache_enabled, "a cache stays on its own layer")
    bpy.data.images.remove(cache)

    f1 = add(tree, FOLDER, "F1")
    f2 = add(tree, FOLDER, "F2")
    add(tree, SOLID, "Inside", target=f1)
    links.link([f2], f1)
    f1.is_expanded = False
    f1.opacity = 0.4
    check(f2.is_expanded and abs(f2.opacity - 0.4) < 1e-6, "a folder shares its opacity, not its expanded state")
    check([node.name for node in stack_ops.descendants(f2)] == [], "nor its content")

    x = add(tree, FILTER, "X")
    y = add(tree, FILTER, "Y")
    links.link([y], x)
    x.blur_sigma = 7.0
    x.derived_stale_reason = "changed below"
    check(abs(y.blur_sigma - 7.0) < 1e-6 and y.derived_stale_reason == "",
          "a filter shares its settings, not its build state")
    x.auto_refresh = False
    check(y.auto_refresh, "nor Auto Refresh, which the refresh job turns off on the one layer that failed")
    check(links.link_candidates(x) == [] and x not in links.link_candidates(a),
          "a layer only links with layers of its own type")


def test_unlink_and_copies():
    section("unlinking one of three")
    tree = new_tree("Unlink")
    a = add(tree, SOLID, "A")
    b = add(tree, SOLID, "B")
    c = add(tree, SOLID, "C")
    links.link([b, c], a)
    links.unlink(b)
    check(b.link_id == "" and a.link_id == c.link_id != "", "B leaves, A and C stay linked")
    a.fill_color = GREEN
    check(tuple(c.fill_color) == GREEN and tuple(b.fill_color) != GREEN, "a change reaches C only")
    links.unlink(c)
    check(a.link_id == "" and c.link_id == "", "the last one left alone loses its id too")

    section("linking a layer that was linked elsewhere")
    d = add(tree, SOLID, "D")
    e = add(tree, SOLID, "E")
    links.link([e], d)
    links.link([e], a)
    check(d.link_id == "" and e.link_id == a.link_id, "E moves to A's group, and D, left alone, loses its id")

    section("unlinking an image layer gives it its own image")
    tree = new_tree("Unlink Image")
    shared = painted_image("Shared Paint", (0.2, 0.4, 0.6, 1.0))
    i1 = add(tree, IMAGE, "I1")
    i2 = add(tree, IMAGE, "I2")
    i1.image = shared
    links.link([i2], i1)
    check(i2.image == shared, "linked image layers share one image")
    links.unlink(i2)
    check(i2.image != shared and i1.image == shared, "after unlinking, I2 has its own image")
    check(first_pixel(i2.image) == (0.2, 0.4, 0.6, 1.0), f"with the painted pixels {first_pixel(i2.image)}")

    section("copies of a linked layer")
    tree = new_tree("Copies")
    a = add(tree, SOLID, "A")
    b = add(tree, SOLID, "B")
    links.link([b], a)
    with node_editor(tree):
        select_only(tree, a)
        bpy.ops.node.duplicate()
    copy = tree.nodes.active
    check(copy != a and copy.bl_idname == SOLID and copy.link_id == "", f"Shift+D makes an unlinked copy ({copy.name})")
    check(a.link_id == b.link_id != "", "the original stays linked")
    tree.nodes.remove(copy)

    # The node editor's clipboard, which copies with Node.copy too. The
    # source is found by its uuid, so an edit between Ctrl+C and Ctrl+V
    # does not fool it.
    def node_paste(source, edit):
        with node_editor(tree):
            select_only(tree, source)
            bpy.ops.node.clipboard_copy()
            edit()
            bpy.ops.node.clipboard_paste()
        pasted = next(node for node in tree.nodes if node.select)
        return pasted

    pasted = node_paste(a, lambda: setattr(a, 'name', "A Renamed"))
    check(pasted.link_id == "" and a.link_id == b.link_id != "",
          f"Ctrl+V after the source was renamed makes an unlinked copy ({pasted.name})")
    tree.nodes.remove(pasted)
    a.name = "A"

    def unlink_a():
        links.unlink(a)
        b.opacity = 0.25
    pasted = node_paste(a, unlink_a)
    check(pasted.link_id == "", "and after the source was unlinked")
    tree.nodes.remove(pasted)
    links.link([b], a)

    def delete_a():
        tree.nodes.remove(a)
        b.opacity = 0.75
    pasted = node_paste(a, delete_a)
    check(pasted.link_id == b.link_id != "" and abs(pasted.opacity - 0.75) < 1e-6,
          f"after the source was deleted the copy takes its place, with the group's settings ({pasted.opacity:.2f})")
    a = pasted

    tree_copy = tree.copy()
    a2, b2 = tree_copy.nodes["A"], tree_copy.nodes["B"]
    check(a2.link_id == b2.link_id == a.link_id, "a copied tree keeps the links among its own layers")
    a2.fill_color = BLUE
    check(tuple(b2.fill_color) == BLUE and tuple(a.fill_color) != BLUE, "and they stay inside the copy")
    bpy.data.node_groups.remove(tree_copy)

    tree.remove_layer_node(a)
    b.fill_color = GREEN
    check("B" not in links.linked_names(tree) and tuple(b.fill_color) == GREEN,
          "removing one leaves the other alone and working")


def test_images():
    section("which images linking takes out of use")
    tree = new_tree("Lost Images")
    painted = painted_image("Painted", (1, 0, 0, 1))
    pristine = bpy.data.images.new("Pristine", 4, 4)
    kept = painted_image("Kept", (0, 1, 0, 1))
    i1 = add(tree, IMAGE, "I1")
    i2 = add(tree, IMAGE, "I2")
    i3 = add(tree, IMAGE, "I3")
    i4 = add(tree, IMAGE, "I4")
    i1.image = painted
    i2.image = pristine
    i3.image = kept
    i4.image = kept
    check(links.lost_images([i1], i2) == [(i1, painted)], "painting nothing else uses is lost")
    check(links.lost_images([i2], i1) == [], "a generated image never painted holds nothing to lose")
    check(links.lost_images([i3], i1) == [], "an image another layer still uses is kept")
    painted.use_fake_user = True
    check(links.lost_images([i1], i2) == [], "an image with a fake user is saved anyway")
    painted.use_fake_user = False
    check(links.lost_images([i1, i3, i4], i2) == [(i1, painted), (i3, kept)],
          "every image the linked layers leave, once each")

    section("an image copy stands on its own")
    copy = bake.duplicate_image(painted)
    check(copy != painted and first_pixel(copy) == (1.0, 0.0, 0.0, 1.0) and copy.packed_file is not None,
          f"a painted image copies its pixels and is packed ({first_pixel(copy)})")
    path = os.path.join(tempfile.gettempdir(), "ps_links_file.png")
    painted.filepath_raw = path
    painted.file_format = 'PNG'
    painted.save()
    from_file = bpy.data.images.load(path)
    copy = bake.duplicate_image(from_file)
    check(copy.packed_file is not None and from_file.packed_file is None,
          "a copy of an image read from a file is packed, so painting it cannot overwrite the file")

    # Image.copy reads the file again, or generates the image again, at
    # the size it had before an unsaved resize.
    from_file.scale(8, 8)
    fill_pixels(from_file, (0.0, 0.5, 1.0, 1.0))
    resized = painted_image("Resized", (1, 0, 0, 1))
    resized.scale(8, 8)
    fill_pixels(resized, (0.0, 0.5, 1.0, 1.0))
    for image in (from_file, resized):
        copy = bake.duplicate_image(image)
        check(tuple(copy.size) == (8, 8) and close(first_pixel(copy), (0.0, 0.5, 1.0, 1.0)),
              f"a copy of {image.source.lower()} {image.name!r} after an unsaved resize has its size and pixels "
              f"({tuple(copy.size)}, {first_pixel(copy)})")
    os.remove(path)
    copy = bake.duplicate_image(from_file)
    check(tuple(copy.size) == (8, 8) and close(first_pixel(copy), (0.0, 0.5, 1.0, 1.0)) and copy.packed_file,
          f"and so does one whose file is gone, packed ({tuple(copy.size)}, {first_pixel(copy)})")
    i1.image = i2.image = resized
    links.link([i2], i1)
    links.unlink(i2)
    check(not i2.link_id and i2.image not in (None, resized) and tuple(i2.image.size) == (8, 8),
          "an unlinked layer gets its own copy of a resized image")


def test_clipboard():
    section("copy in one tree, paste in another")
    source = new_tree("Clip Source")
    under = add(source, SOLID, "Under")
    folder = add(source, FOLDER, "Folder")
    inner = add(source, SOLID, "Inner", target=folder)
    nested = add(source, FOLDER, "Nested", target=inner)
    stack_ops.detach(source, nested)
    stack_ops.insert_below(source, nested, inner)
    add(source, SOLID, "Deep", target=nested)
    # A mask reads a layer below the one it masks. One above would read
    # the masked layer's result as well, which is a loop.
    masker = add(source, SOLID, "Masker")
    stack_ops.detach(source, masker)
    stack_ops.insert_below(source, masker, folder)
    outsider = add(source, SOLID, "Outsider")
    stack_ops.detach(source, outsider)
    stack_ops.insert_below(source, outsider, under)
    source.links.new(stack_ops.stack_output(masker), inner.inputs['Mask'])
    source.links.new(stack_ops.stack_output(outsider), under.inputs['Mask'])
    masker.fill_color = RED
    before = layout(source)
    clipboard.copy_layers([folder, masker, under])
    check(clipboard.copied_layers() == [folder, masker, under], "the clipboard finds the copied layers")

    target = new_tree("Clip Target")
    existing = add(target, SOLID, "Existing")
    pasted = clipboard.paste_layers(target, existing)
    check([node.name for node in pasted] == ["Folder", "Masker", "Under"], "names are kept in another tree")
    check(layout(target) == [("Folder", 0), ("Inner", 1), ("Nested", 1), ("Deep", 2), ("Masker", 0),
                             ("Under", 0), ("Existing", 0)],
          f"the layers keep their order and folders their content, above the active layer {layout(target)}")
    check(layout(source) == before, "the source tree is unchanged")
    check(tuple(target.nodes["Masker"].fill_color) == RED, "settings are copied")
    check(all(node.uuid != source.nodes[node.name].uuid for node in pasted), "each copy has its own uuid")
    mask = stack_ops.feeding_link(target.nodes["Inner"].inputs['Mask'])
    check(mask is not None and mask.from_node == target.nodes["Masker"], "a mask among the copied layers is kept")
    check(stack_ops.feeding_link(target.nodes["Under"].inputs['Mask']) is None,
          "a mask from a layer that was not copied is left out")
    check(target.nodes.active == target.nodes["Folder"], "the first pasted layer is active")
    check(clipboard.can_paste_linked(source) and not clipboard.can_paste_linked(target),
          "a linked paste only works in the tree the layers came from")

    section("paste into a folder, and into the folder that was copied")
    clipboard.copy_layers([folder])
    pasted = clipboard.paste_layers(source, folder)
    check([(name, level) for name, level in layout(source)][:2] == [("Folder", 0), ("Folder.001", 1)]
          and ("Deep.001", 3) in layout(source),
          f"a folder pasted into itself holds a copy of its content as it was {layout(source)}")

    section("several layers pasted on top, and a layer moved into a copied folder")
    tree = new_tree("Clip Moved")
    folder = add(tree, FOLDER, "F")
    add(tree, SOLID, "F Inner", target=folder)
    moved = add(tree, SOLID, "X")
    clipboard.copy_layers([moved, folder])
    other = new_tree("Clip Moved Paste")
    clipboard.paste_layers(other)
    check(layout(other) == [("X", 0), ("F", 0), ("F Inner", 1)], f"they keep their order {layout(other)}")
    stack_ops.detach(tree, moved)
    stack_ops.insert_into(tree, folder, moved)
    other = new_tree("Clip Moved Again")
    clipboard.paste_layers(other)
    check(layout(other) == [("F", 0), ("X", 1), ("F Inner", 1)],
          f"a layer moved into a copied folder since is pasted once, in the folder, with all the folder holds "
          f"{layout(other)}")

    section("a mask linked since the copy cannot make a loop")
    tree = new_tree("Clip Loop")
    a = add(tree, SOLID, "A")
    m = add(tree, SOLID, "M")
    clipboard.copy_layers([m, a])
    stack_ops.detach(tree, a)
    stack_ops.insert_above(tree, a, m)
    tree.links.new(stack_ops.stack_output(m), a.inputs['Mask'])
    other = new_tree("Clip Loop Paste")
    clipboard.paste_layers(other)
    check(layout(other) == [("M", 0), ("A", 0)], f"the layers keep the order they were copied in {layout(other)}")
    check(stack_ops.feeding_link(other.nodes["A"].inputs['Mask']) is None
          and all(link.is_valid for link in other.links),
          "M sits above A there and reads it, so the mask is left out")

    section("a plain paste stands alone")
    tree = new_tree("Clip Plain")
    shared = painted_image("Clip Shared", (0.5, 0.5, 0.5, 1.0))
    i1 = add(tree, IMAGE, "I1")
    i2 = add(tree, IMAGE, "I2")
    i1.image = shared
    links.link([i2], i1)
    lone = add(tree, IMAGE, "Lone")
    lone.image = shared
    clipboard.copy_layers([i1, i2, lone])
    new_i1, new_i2, new_lone = clipboard.paste_layers(tree)
    check(new_i1.link_id and new_i1.link_id == new_i2.link_id != i1.link_id,
          "copies of layers linked with each other are linked with each other only")
    check(new_lone.link_id == "", "a copy of an unlinked layer is unlinked")
    check(new_i1.image == new_i2.image and new_i1.image not in {shared, None}
          and new_lone.image not in {shared, None, new_i1.image},
          "each copy has its own image, shared only within its own new group")
    check(close(first_pixel(new_lone.image), (0.5, 0.5, 0.5, 1.0), 1 / 255),
          f"with the painted pixels {first_pixel(new_lone.image)}")
    clipboard.copy_layers([i1])
    (alone,) = clipboard.paste_layers(tree)
    check(alone.link_id == "" and alone.image not in {shared, None},
          "a copy of one layer of a linked group stands alone")

    section("a linked paste")
    clipboard.copy_layers([lone])
    (linked,) = clipboard.paste_layers(tree, linked=True)
    check(linked.link_id == lone.link_id != "" and linked.image == shared, "links with the copied layer and shares its image")
    lone.opacity = 0.3
    check(abs(linked.opacity - 0.3) < 1e-6, "and stays in sync")

    section("the clipboard passes over removed layers")
    tree.remove_layer_node(lone)
    check(clipboard.copied_layers() == [], "a removed layer is gone from the clipboard")
    check(clipboard.paste_layers(tree) == [], "and a paste adds nothing")


def test_operators():
    section("copy and paste operators")
    tree = new_tree("Clip Ops")
    a = add(tree, SOLID, "A")
    other = new_tree("Clip Ops Other")
    bpy.context.scene.paint_system.active_node_tree = tree
    tree.nodes.active = a
    check(bpy.ops.paint_system.copy_layer() == {'FINISHED'}, "copy the active layer")
    check(bpy.ops.paint_system.paste_linked_layer.poll(), "a linked paste is on offer in the same tree")
    check(bpy.ops.paint_system.paste_linked_layer() == {'FINISHED'}
          and tree.nodes.active.link_id == a.link_id != "", "Paste Linked links the new layer")
    bpy.context.scene.paint_system.active_node_tree = other
    check(bpy.ops.paint_system.paste_layer.poll() and not bpy.ops.paint_system.paste_linked_layer.poll(),
          "in another tree only a plain paste is on offer")
    check(bpy.ops.paint_system.paste_layer() == {'FINISHED'} and layout(other) == [("A", 0)],
          f"a plain paste works there {layout(other)}")
    check(bpy.ops.paint_system.copy_all_layers() == {'FINISHED'}
          and clipboard.copied_layers() == [other.nodes["A"]], "Copy All copies the top-level layers")

    section("Ctrl+L links the selected layers with the active one")
    tree = new_tree("Ctrl L")
    a = add(tree, SOLID, "A")
    b = add(tree, SOLID, "B")
    c = add(tree, SOLID, "C")
    locked = add(tree, SOLID, "Locked")
    folder = add(tree, FOLDER, "Folder")
    locked.lock_layer = True
    a.fill_color = RED
    with node_editor(tree):
        select_only(tree, a)
        check(not bpy.ops.paint_system.link_selected_layers.poll(), "nothing selected to link: the key passes on")
        select_only(tree, a, b, c, locked, folder)
        check(bpy.ops.paint_system.link_selected_layers.poll(), "on offer with other solids selected")
        result = bpy.ops.paint_system.link_selected_layers('INVOKE_DEFAULT')
    check(result == {'FINISHED'}, f"Ctrl+L links them {result}")
    check(b.link_id == c.link_id == a.link_id != "" and tuple(c.fill_color) == RED, "B and C take A's settings")
    check(locked.link_id == "" and folder.link_id == "", "a locked layer and another type are left out")
    bpy.context.scene.paint_system.active_node_tree = tree
    with bpy.context.temp_override(area=None, space_data=None):
        check(not bpy.ops.paint_system.link_selected_layers.poll(), "only in the node editor")

    section("Link With and Unlink")
    d = add(tree, SOLID, "D")
    tree.nodes.active = d
    names = link_ops.target_names(None, bpy.context, "")
    check(names == ["A", "B", "C", "Locked"], f"Link With offers the other layers of its type {names}")
    # With undo on, the call is kept for Adjust Last Operation.
    check(bpy.ops.paint_system.link_layer('EXEC_DEFAULT', True, target="A") == {'FINISHED'} and d.link_id == a.link_id,
          "Link With links the active layer with the one picked")
    check(tuple(d.fill_color) == RED, "and it takes that layer's settings")
    last = bpy.context.window_manager.operators[-1]
    check(last.bl_idname == "PAINT_SYSTEM_OT_link_layer" and last.properties.target == "A",
          f"a redo links with the layer picked, which has left the list since ({last.properties.target!r})")
    check(bpy.ops.paint_system.unlink_layer() == {'FINISHED'} and d.link_id == "", "Unlink undoes it")
    check(not bpy.ops.paint_system.unlink_layer.poll(), "an unlinked layer cannot be unlinked")
    tree.nodes.active = locked
    check(not bpy.ops.paint_system.link_layer.poll(), "a locked layer cannot take another's settings")

    section("Ctrl+L asks first when painting would be lost")
    # A background Blender runs execute in place of invoke, so invoke is
    # called here with a window manager that records the dialog.
    pictures = new_tree("Ctrl L Images")
    kept = add(pictures, IMAGE, "Kept")
    lost = add(pictures, IMAGE, "Lost")
    kept.image = bpy.data.images.new("Kept Painting", 4, 4)
    lost.image = painted_image("Lost Painting", GREEN)
    operator = link_ops.PAINTSYSTEM_OT_link_selected_layers

    def invoke():
        calls = []
        stand_in = SimpleNamespace(targets=operator.targets, execute=lambda context: calls.append('execute') or {'FINISHED'})
        window_manager = SimpleNamespace(invoke_props_dialog=lambda op, **kwargs: calls.append('dialog') or {'RUNNING_MODAL'})
        with node_editor(pictures):
            select_only(pictures, kept, lost)
            context = SimpleNamespace(space_data=bpy.context.space_data, window_manager=window_manager)
            operator.invoke(stand_in, context, None)
        return calls, stand_in.lost

    calls, lost_images = invoke()
    check(calls == ['dialog'] and lost_images == [(lost, lost.image)],
          f"linking a layer whose painting nothing else uses opens a dialog, which names the image {calls}")
    kept.image = lost.image
    calls, lost_images = invoke()
    check(calls == ['execute'] and lost_images == [], f"with no painting to lose it links at once {calls}")


def test_ui():
    section("the layer list marks linked layers")
    tree = new_tree("Link UI")
    a = add(tree, SOLID, "A")
    b = add(tree, SOLID, "B")
    add(tree, SOLID, "C")
    links.link([b], a)
    rows = layers_panels.layer_rows(tree)
    check(rows["A"].linked and rows["B"].linked and not rows["C"].linked, "rows know which layers are linked")

    def menu_items(active):
        tree.nodes.active = active
        calls = []
        layers_panels.PAINTSYSTEM_MT_layer_menu.draw(SimpleNamespace(layout=RecordingLayout(calls)), bpy.context)
        return [call[1][0] for call in calls if call[0] == "operator"]

    plain = ["paint_system.copy_layer", "paint_system.copy_all_layers",
             "paint_system.paste_layer", "paint_system.paste_linked_layer"]
    check(menu_items(a) == ["paint_system.unlink_layer", *plain], "the layer menu offers Unlink for a linked layer")
    check(menu_items(tree.nodes["C"]) == plain, "and not otherwise")

    calls = []
    tree.nodes.active = a
    with node_editor(tree):
        check(layers_panels.PAINTSYSTEM_PT_node_links.poll(bpy.context), "the node panel shows for a layer")
        layers_panels.PAINTSYSTEM_PT_node_links.draw(SimpleNamespace(layout=RecordingLayout(calls)), bpy.context)
    labels = [call[2].get("text") for call in calls if call[0] == "label"]
    check("B" in labels and "Color" in labels, f"it lists the linked layer and its channel {labels}")


def test_tabs():
    section("the tab on a linked node")
    preview = common.icon_preview(link_tabs.ICON_NAME)
    check(preview is not None and tuple(preview.image_size) != (0, 0), "the chain icon is shipped")
    path = os.path.join(os.path.dirname(common.__file__), "icons", f"{link_tabs.ICON_NAME}.png")
    loaded = bpy.data.images.load(path)
    size = loaded.size[0] * loaded.size[1] * 4
    from_file = np.empty(size, dtype=np.float32)
    loaded.pixels.foreach_get(from_file)
    bpy.data.images.remove(loaded)
    from_preview = np.array(preview.image_pixels_float[:], dtype=np.float32)
    check(from_preview.shape == from_file.shape and np.allclose(from_preview[3::4], from_file[3::4], atol=2 / 255),
          "the preview's pixels run bottom row first, as a texture's do")
    check(np.allclose(from_preview[0::4], from_file[0::4] * from_file[3::4], atol=2 / 255),
          "and their colour is premultiplied by their alpha")

    if not gpu_core.gpu_available():
        skip("no GPU context in this background Blender, so the tab is not drawn")
        return
    import gpu
    tree = new_tree("Link Tabs")
    a = add(tree, SOLID, "A")
    b = add(tree, SOLID, "B")
    lone = add(tree, SOLID, "Lone")
    links.link([b], a)
    a.location = (100, 200)
    b.location = (1000, 1000)
    lone.location = (150, 200)
    scale = 4.0
    # View space x 400..760, y 780..880 fills the image one pixel per unit.
    # A's tab and the place Lone's would be are both inside it.
    x0, y0, width, height = 400, 780, 360, 100
    projection = Matrix(((2 / width, 0, 0, -(2 * x0 + width) / width),
                         (0, 2 / height, 0, -(2 * y0 + height) / height),
                         (0, 0, 1, 0), (0, 0, 0, 1)))
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        with offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.clear(color=(0.0, 0.0, 0.0, 0.0))
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_identity()
                gpu.matrix.load_projection_matrix(projection)
                link_tabs.draw_tabs(tree, scale)
            buffer = framebuffer.read_color(0, 0, width, height, 4, 0, 'UBYTE')
        pixels = np.array(buffer.to_list(), dtype=np.float32).reshape(height, width, 4) / 255
    finally:
        offscreen.free()

    def at(x, y):
        return tuple(pixels[int(y - y0), int(x - x0)])

    left, top = 100 * scale, 200 * scale
    tab_left = left + link_tabs.TAB_INSET * scale
    tab_right = tab_left + link_tabs.TAB_WIDTH * scale
    tab_top = top + link_tabs.TAB_HEIGHT * scale
    header = (*a.header_color, 1.0)
    check(close(at(tab_left + 3, top + 3), header, 2 / 255), f"the tab has the header colour {at(tab_left + 3, top + 3)}")
    check(at(tab_left - 3, top + 3)[3] == 0 and at(tab_right + 3, top + 3)[3] == 0, "it is as wide as a tab")
    half = link_tabs.ICON_SIZE * scale / 2
    cx, cy = (tab_left + tab_right) / 2, (top + tab_top) / 2
    # Above and below the icon, at the middle, clear of the round corners.
    check(at(cx, tab_top - 3)[3] > 0 and at(cx, tab_top + 3)[3] == 0, "and as high")
    check(at(tab_left + 1, tab_top - 1)[3] == 0 and at(tab_left + 3, top - 1)[3] > 0,
          "its top corners are round and its bottom ones square")
    icon = pixels[int(cy - half - y0):int(cy + half - y0), int(cx - half - x0):int(cx + half - x0)]
    white = (icon[..., 0] > 0.9) & (icon[..., 1] > 0.9)
    mid = white.shape[0] // 2
    # Rows run bottom first, so white[mid:] is the upper half.
    rising = white[mid:, mid:].mean() + white[:mid, :mid].mean()
    falling = white[mid:, :mid].mean() + white[:mid, mid:].mean()
    check(white.mean() > 0.1, f"the chain is drawn inside ({white.mean():.2f} white)")
    check(rising > 1.5 * falling, f"the right way up, rising to the right ({rising:.2f} > {falling:.2f})")
    # A white icon over the tab only lightens it. Blending its
    # premultiplied colour as straight alpha darkens its soft edges.
    darkest = icon[..., :3].min(axis=(0, 1))
    check(np.all(darkest >= np.array(header[:3]) - 2 / 255),
          f"its soft edges are no darker than the tab ({tuple(np.round(darkest, 3))})")
    lone_tab_left = 150 * scale + link_tabs.TAB_INSET * scale
    check(at(lone_tab_left + 3, top + 3)[3] == 0, "an unlinked layer gets no tab")
    link_tabs.release()


guarded(test_what_is_linked)
guarded(test_link_and_sync)
guarded(test_unlink_and_copies)
guarded(test_images)
guarded(test_clipboard)
guarded(test_operators)
guarded(test_ui)
guarded(test_tabs)
finish("LINKED LAYERS TEST")
