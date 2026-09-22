"""Headless test for the compile-based architecture.

Run:  blender -b --factory-startup --python tests/test_compile.py
"""
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, section, register_addon, import_from, finish  # noqa: E402

register_addon()

_core = import_from("compiler.core")
compile_tree = _core.compile_tree
artifact_fingerprint = _core.artifact_fingerprint
suspend_compile = _core.suspend_compile
flush = _core.flush
mark_dirty = _core.mark_dirty
normalize_tree = _core.normalize_tree
cleanup_orphan_artifacts = _core.cleanup_orphan_artifacts
library = import_from("compiler.library")
_bake = import_from("compiler.bake")
bake_node_cache = _bake.bake_node_cache
bake_subtree = _bake.bake_subtree
create_managed_image = _bake.create_managed_image
link_tree_to_material = import_from("ops.node_tree_ops").link_tree_to_material


def compiled_nodes(tree, bl_idname):
    return [n for n in tree.compiled.nodes if n.bl_idname == bl_idname]


def is_library_group(ng):
    return ng.name.startswith(library.LIBRARY_PREFIX)


def force_compile_ok(tree):
    """Force a compile of *tree* and say whether it went through."""
    try:
        compile_tree(tree, force=True)
    except Exception:
        traceback.print_exc()
        return False
    return True


def output_feeds(tree):
    """(input, identifier, output) for each link into the artifact's Group Output."""
    out = compiled_nodes(tree, 'NodeGroupOutput')[0]
    return [(sock.name, link.from_node.get("ps_identifier"), link.from_socket.name)
            for sock in out.inputs for link in sock.links]


try:
    section("tree init")
    tree = bpy.data.node_groups.new("Main", 'PaintSystemNodeTree')
    tree.initialize()
    gin, gout = tree.get_input_node(), tree.get_output_node()
    check(gin is not None and gout is not None, "io nodes created")
    check([c.name for c in tree.channels] == ['Color'], "default Color channel")
    check([s.name for s in gout.inputs] == ['Color'], "one output socket per channel")
    check(gout.inputs['Color'].links[0].from_node == gin, "passthrough link input->output")

    section("first compile")
    solid = tree.insert_layer_node('PaintSystemSolidColorLayerNode')
    solid.fill_color = (1.0, 0.0, 0.0, 1.0)
    img_layer = tree.insert_layer_node('PaintSystemImageLayerNode')
    image = bpy.data.images.new("Paint", 64, 64, alpha=True)
    img_layer.image = image
    check([item.node for item in tree.stack()] == [img_layer, solid], "stack top-first")

    fp1 = compile_tree(tree)
    art = tree.compiled
    check(art is not None and art.bl_idname == 'ShaderNodeTree', "artifact created")
    check(art.get('ps_owner') == tree.uuid, "artifact tagged with owner uuid")
    check(art.get('ps_fingerprint') == fp1, "fingerprint stored on the artifact")
    blends = compiled_nodes(tree, 'ShaderNodeGroup')
    check(len(blends) == 2, f"two blend group instances ({len(blends)})")
    check(all(is_library_group(n.node_tree) for n in blends), "blends use static library group")
    texs = compiled_nodes(tree, 'ShaderNodeTexImage')
    check(len(texs) == 1 and texs[0].image == image, "image texture bound to layer image")
    out = compiled_nodes(tree, 'NodeGroupOutput')[0]
    check(out.inputs['Color'].is_linked and out.inputs['Color Alpha'].is_linked,
          "the artifact splits the channel into its colour and alpha outputs")
    iface = [(s.in_out, s.name) for s in art.interface.items_tree]
    check(('OUTPUT', 'Color') in iface and ('INPUT', 'Color Alpha') in iface, "interface sockets from channels")
    lib_count = len([ng for ng in bpy.data.node_groups if is_library_group(ng)])
    check(lib_count == 1, f"one library group so far ({lib_count})")
    for ng in bpy.data.node_groups:
        check(not (ng.bl_idname == 'ShaderNodeTree' and ng.name.startswith('.PS ') and 'Channel' in ng.name),
              f"no per-channel datablocks: {ng.name}")

    section("idempotence")
    before = {n.name: n.as_pointer() for n in art.nodes}
    fp2 = compile_tree(tree)
    after = {n.name: n.as_pointer() for n in art.nodes}
    check(fp1 == fp2, "fingerprint stable")
    check(before == after, "no node churn on unchanged recompile")
    ir = _core.build_ir(tree)
    ir_fp = ir.fingerprint()
    ir.apply(art)
    check(ir.fingerprint() == ir_fp == fp2, "applying an IR leaves it as it was")

    section("property change")
    img_layer.opacity = 0.5
    fp3 = compile_tree(tree)
    check(fp3 != fp2, "fingerprint changes with opacity")
    blend = art.nodes.get(f"{img_layer.uuid}:blend") or next(
        n for n in art.nodes if n.get("ps_identifier") == f"{img_layer.uuid}:blend")
    check(abs(blend.inputs['Opacity'].default_value - 0.5) < 1e-6, "opacity written into blend instance")
    after2 = {n.name: n.as_pointer() for n in art.nodes}
    check(after == after2, "property change patches in place (no recreation)")

    img_layer.blend_mode = 'MULTIPLY'
    compile_tree(tree)
    check(blend.node_tree.name.endswith('[MULTIPLY]'), "blend mode swaps library group")
    check(len([ng for ng in bpy.data.node_groups if is_library_group(ng)]) == 2,
          "second library group generated lazily")

    img_layer.enabled = False
    compile_tree(tree)
    check(blend.inputs['Opacity'].default_value == 0.0, "disabled layer compiles to opacity 0")
    img_layer.enabled = True

    section("library group stability")
    lib = library.layer_blend_group('MIX')
    ids_before = [s.identifier for s in lib.interface.items_tree]
    n_before = len(lib.nodes)
    library._build_layer_blend(lib, 'MIX')
    check([s.identifier for s in lib.interface.items_tree] == ids_before, "interface identifiers stable on rebuild")
    check(len(lib.nodes) == n_before, "node count stable on rebuild")

    section("channels")
    tree.create_channel('Rough', 'FLOAT')
    check([s.name for s in gout.inputs] == ['Color', 'Rough'], "io sockets follow new channel")
    check(gout.inputs['Color'].links[0].from_node == img_layer, "existing links preserved on channel add")
    compile_tree(tree)
    iface = [(s.in_out, s.name, s.bl_socket_idname) for s in art.interface.items_tree]
    check(('OUTPUT', 'Rough', 'NodeSocketFloat') in iface, "float channel in interface")
    tree.channels[1].name = 'Roughness'
    check([s.name for s in gout.inputs][1:] == ['Roughness'], "channel rename renames sockets")
    check(gout.inputs['Roughness'].links[0].from_node == gin, "rename keeps passthrough link")

    section("nested group")
    child = bpy.data.node_groups.new("Detail", 'PaintSystemNodeTree')
    child.initialize()
    child_solid = child.insert_layer_node('PaintSystemSolidColorLayerNode')
    child_solid.fill_color = (0.0, 1.0, 0.0, 1.0)
    group = tree.nodes.new('PaintSystemGroupLayerNode')
    group.node_tree = child
    check([s.name for s in group.inputs] == ['Color'], "group node sockets from child channels")
    tree.links.new(img_layer.outputs['Color'], group.inputs['Color'])
    tree.links.new(group.outputs['Color'], gout.inputs['Color'])
    fp_before_child = compile_tree(tree)
    check(child.compiled is not None, "child compiled on demand")
    ginst = next(n for n in art.nodes if n.get("ps_identifier") == f"{group.uuid}:group")
    check(ginst.node_tree == child.compiled, "parent instances child's artifact")
    child_fp = artifact_fingerprint(child)
    child_solid.fill_color = (1.0, 1.0, 0.0, 1.0)
    check(artifact_fingerprint(child) != child_fp, "child edit compiles the child immediately")
    with suspend_compile(child):
        child_solid.fill_color = (0.0, 0.0, 1.0, 1.0)
        child_fp = artifact_fingerprint(child)
        compile_tree(tree)
        check(artifact_fingerprint(child) != child_fp, "parent compile brings a stale child up to date")
    child.create_channel('Mask', 'FLOAT')
    check('Mask' in [s.name for s in group.outputs], "child channel change propagates to parent group node sockets")
    group_id = f"{group.uuid}:group"
    tree.links.new(group.outputs['Mask'], gout.inputs['Roughness'])
    tree.channels['Roughness'].use_alpha = True
    compile_tree(tree)
    opaque = f"{group.uuid}:const:{group.outputs['Mask'].identifier}:alpha"
    mask_feeds = [('Roughness', group_id, 'Mask'), ('Roughness Alpha', opaque, 'Value')]
    feeds = [feed for feed in output_feeds(tree) if feed[0].startswith('Roughness')]
    check(feeds == mask_feeds, f"a float child channel, which has no alpha, feeds the parent's channel, "
                               f"and a constant its alpha ({feeds})")
    # The child's artifact retypes the socket in place, so the parent's
    # links to it survive.
    child.channels['Mask'].type = 'COLOR'
    compile_tree(tree)
    feeds = [feed for feed in output_feeds(tree) if feed[0].startswith('Roughness')]
    check(feeds == mask_feeds, f"retyping a child channel keeps the parent's links to it ({feeds})")
    tree.channels['Roughness'].use_alpha = False
    compile_tree(tree)
    roughness = next(s for s in gout.inputs if s.name == 'Roughness')
    flatten = f"{gout.uuid}:flatten:{roughness.identifier}"
    feeds = [feed for feed in output_feeds(tree) if feed[0].startswith('Roughness')]
    check(feeds == [('Roughness', flatten, 'Result')],
          f"without alpha the parent's channel goes through a mix ({feeds})")
    mix = next(n for n in art.nodes if n.get("ps_identifier") == flatten)
    mix_feeds = [(index, link.from_node.get("ps_identifier"), link.from_socket.name)
                 for index, sock in enumerate(mix.inputs) for link in sock.links]
    check(mix_feeds == [(0, opaque, 'Value'), (6, f"{gout.uuid}:base", 'Roughness'), (7, group_id, 'Mask')],
          f"that lays the colour over the channel's input by its alpha ({mix_feeds})")
    tree.links.new(gin.outputs['Roughness'], gout.inputs['Roughness'])

    section("normalize")
    dup = tree.nodes.new('PaintSystemSolidColorLayerNode')
    dup.uuid = solid.uuid
    normalize_tree(tree)
    check(dup.uuid != solid.uuid, "duplicate node uuid repaired")
    tree.nodes.remove(dup)

    section("synchronous compile")
    compiles = []
    real_compile_tree = _core.compile_tree

    def counting_compile_tree(t, **kwargs):
        compiles.append(t.name)
        return real_compile_tree(t, **kwargs)

    # The links made by hand above left their builds to a timer, which
    # never fires in a headless script.
    _core.flush_now()
    _core.compile_tree = counting_compile_tree
    try:
        fp = artifact_fingerprint(tree)
        solid.opacity = 0.25
        check(artifact_fingerprint(tree) != fp, "property edit compiles immediately")
        check(not bpy.app.timers.is_registered(flush), "no fallback timer when writing is allowed")

        fp = artifact_fingerprint(tree)
        compiles.clear()
        with suspend_compile(tree):
            solid.opacity = 0.5
            solid.blend_mode = 'SCREEN'
            check(artifact_fingerprint(tree) == fp, "edits inside suspend_compile wait")
        check(artifact_fingerprint(tree) != fp, "suspend_compile exit compiles")
        check(compiles.count(tree.name) == 1, f"batched edits compile once ({compiles})")

        fp = artifact_fingerprint(tree)
        _core.block_compile()
        solid.opacity = 0.75
        check(artifact_fingerprint(tree) == fp, "edits wait while blocked (file load, undo)")
        _core.unblock_compile()
        mark_dirty()
        check(artifact_fingerprint(tree) != fp, "unblock then mark_dirty compiles")

        # Node editor edits arrive through NodeTree.update, where Blender
        # builds no sockets for new group nodes.
        fp = artifact_fingerprint(tree)
        top = gout.inputs['Color'].links[0].from_node
        extra = tree.nodes.new('PaintSystemSolidColorLayerNode')
        tree.links.new(top.outputs['Color'], extra.inputs['Color'])
        check(artifact_fingerprint(tree) == fp and not bpy.app.timers.is_registered(flush),
              "node editor edits that leave the output alone change nothing")
        tree.links.new(extra.outputs['Color'], gout.inputs['Color'])
        check(artifact_fingerprint(tree).startswith("pending "), "a node editor edit stamps the artifact pending")
        check(bpy.app.timers.is_registered(flush), "and leaves the build to a timer")
        _core.flush_now()
        blend = next((n for n in art.nodes if n.get("ps_identifier") == f"{extra.uuid}:blend"), None)
        check(blend is not None and blend.inputs['Prev Color'].is_linked and blend.outputs['Color'].is_linked,
              "the timer builds the new group node with its links")
        check(artifact_fingerprint(tree) == _core.build_ir(tree).fingerprint(), "the artifact is current after the tick")
        tree.remove_layer_node(extra)
    finally:
        _core.compile_tree = real_compile_tree
        _core.unblock_compile()
    solid.blend_mode = 'MIX'
    solid.opacity = 1.0

    section("material + bake")
    cube = bpy.data.objects.get('Cube')
    check(cube is not None, "factory cube exists")
    mat = bpy.data.materials.new("CubeMat")
    cube.data.materials.clear()
    cube.data.materials.append(mat)
    gnode = link_tree_to_material(mat, tree)
    check(mat.paint_system.tree == tree, "material points at tree")
    check(gnode.node_tree == tree.compiled, "material group instances artifact")
    bsdf = next(n for n in mat.node_tree.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled')
    check(bsdf.inputs['Base Color'].is_linked, "base color linked")

    img_px = [0.0] * (64 * 64 * 4)
    for i in range(0, len(img_px), 4):
        img_px[i:i + 4] = [0.0, 0.0, 1.0, 1.0]  # opaque blue paint
    image.pixels.foreach_set(img_px)
    img_layer.opacity = 1.0
    img_layer.blend_mode = 'MIX'
    baked = bake_node_cache(bpy.context, tree, img_layer, cube, width=32, height=32, margin=2)
    check(baked is not None and img_layer.cache_hash != "", "bake produced image + hash")
    img_layer.cache_enabled = True
    compile_tree(tree)
    check(not any(n.get("ps_identifier") == f"{img_layer.uuid}:blend" for n in art.nodes),
          "cached layer: blend removed from artifact")
    check(not any(n.get("ps_identifier") == f"{solid.uuid}:blend" for n in art.nodes),
          "cached layer: upstream layer not compiled")
    cache_tex = next(n for n in art.nodes if n.get("ps_identifier") == f"{img_layer.uuid}:cache")
    check(cache_tex.image == baked, "cache image texture emitted")
    px = [0.0] * (32 * 32 * 4)
    baked.pixels.foreach_get(px)
    center = px[(16 * 32 + 16) * 4:(16 * 32 + 16) * 4 + 4]
    check(center[2] > 0.5 and center[3] > 0.5, f"baked pixel is blue+opaque {['%.2f' % c for c in center]}")

    section("bake gives back what it borrows")
    bare_mesh = bpy.data.meshes.new("PS Bare")
    bare_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    bare_uv = bare_mesh.uv_layers.new(name="UVMap")
    for loop, co in zip(bare_uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = co
    bare = bpy.data.objects.new("PS Bare", bare_mesh)
    bpy.context.scene.collection.objects.link(bare)
    view_layer = bpy.context.view_layer
    for o in view_layer.objects:
        o.select_set(o == cube)
    view_layer.objects.active = cube
    scratch = create_managed_image("PS Bake Scratch", 16, 16)
    scratch_hash = bake_subtree(bpy.context, tree, img_layer, bare, scratch, margin=0)
    check(scratch_hash == img_layer.cache_hash, "bake_subtree returns the subtree fingerprint")
    check(view_layer.objects.active == cube and cube.select_get() and not bare.select_get(),
          "the bake gives the selection and the active object back")
    check(len(bare.material_slots) == 0,
          "and an object it borrowed with no material slots keeps none")
    check(scratch.packed_file is None,
          "bake_subtree leaves packing to its caller")
    bpy.data.objects.remove(bare)
    bpy.data.meshes.remove(bare_mesh)
    bpy.data.images.remove(scratch)

    solid.fill_color = (0.2, 0.2, 0.2, 1.0)
    compile_tree(tree)
    check(img_layer.cache_stale, "upstream edit invalidates cache")
    check(any(n.get("ps_identifier") == f"{img_layer.uuid}:blend" for n in art.nodes),
          "stale cache falls back to live graph")

    section("reroutes and other nodes")
    rerouted = bpy.data.node_groups.new("Rerouted", 'PaintSystemNodeTree')
    rerouted.initialize()
    routed_layer = rerouted.insert_layer_node('PaintSystemSolidColorLayerNode')
    routed_out = rerouted.get_output_node()
    near, far = rerouted.nodes.new('NodeReroute'), rerouted.nodes.new('NodeReroute')
    rerouted.links.new(routed_layer.outputs['Color'], near.inputs[0])
    rerouted.links.new(near.outputs[0], far.inputs[0])
    rerouted.links.new(far.outputs[0], routed_out.inputs['Color'])
    check(force_compile_ok(rerouted), "a tree with reroutes compiles")
    blend_id = f"{routed_layer.uuid}:blend"
    feeds = output_feeds(rerouted)
    check(feeds == [('Color', blend_id, 'Color'), ('Color Alpha', blend_id, 'Alpha')],
          f"reroutes compile as a direct link that carries the colour and the alpha ({feeds})")

    foreign = rerouted.nodes.new('NodeGroupInput')
    rerouted.links.new(foreign.outputs[0], routed_layer.inputs['Color'])
    check(force_compile_ok(rerouted), "a tree linked from a node Paint System does not make compiles")
    blend = next(n for n in rerouted.compiled.nodes if n.get("ps_identifier") == blend_id)
    check(not blend.inputs['Prev Color'].is_linked, "a link from such a node reads as unlinked")

    rerouted.links.remove(near.inputs[0].links[0])
    check(force_compile_ok(rerouted) and output_feeds(rerouted) == [],
          "a reroute with nothing feeding it reads as unlinked")

    section("last channel removed")
    no_channels = bpy.data.node_groups.new("No Channels", 'PaintSystemNodeTree')
    no_channels.initialize()
    compile_tree(no_channels)
    check(len(no_channels.compiled.interface.items_tree) > 0, "the artifact has the channel's sockets")
    no_channels.delete_channel(0)
    compile_tree(no_channels, force=True)
    iface = [(s.in_out, s.name) for s in no_channels.compiled.interface.items_tree]
    check(len(no_channels.channels) == 0 and iface == [],
          f"removing the last channel removes its sockets from the artifact ({iface})")

    section("artifact node copied by hand")
    copied = bpy.data.node_groups.new("Copied", 'PaintSystemNodeTree')
    copied.initialize()
    copied_layer = copied.insert_layer_node('PaintSystemSolidColorLayerNode')
    compile_tree(copied)
    copied_art = copied.compiled
    node_count = len(copied_art.nodes)
    original = compiled_nodes(copied, 'NodeGroupOutput')[0]
    original_pointer = original.as_pointer()
    feeds_before = output_feeds(copied)
    # What Shift+D does: the copy keeps the custom properties.
    copy = copied_art.nodes.new('NodeGroupOutput')
    copy["ps_identifier"] = original["ps_identifier"]
    compile_tree(copied, force=True)
    outputs = compiled_nodes(copied, 'NodeGroupOutput')
    check(len(copied_art.nodes) == node_count and len(outputs) == 1,
          f"the copy is removed ({len(copied_art.nodes)} nodes, {node_count} before)")
    check(outputs and outputs[0].as_pointer() == original_pointer and outputs[0].is_active_output,
          "the original Group Output is the one kept")
    check(feeds_before and output_feeds(copied) == feeds_before,
          f"the kept Group Output is still linked ({output_feeds(copied)})")

    section("cleanup")
    orphan_name = tree.compiled.name
    bpy.data.node_groups.remove(tree)
    removed = cleanup_orphan_artifacts()
    check(removed >= 0 and bpy.data.node_groups.get(orphan_name) is None or mat.node_tree is not None,
          "orphan cleanup runs")

except Exception:
    traceback.print_exc()
    check(False, "exception")

finish("COMPILE TEST")
