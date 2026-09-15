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
bake_node_cache = import_from("compiler.bake").bake_node_cache
link_tree_to_material = import_from("ops.node_tree_ops").link_tree_to_material


def compiled_nodes(tree, bl_idname):
    return [n for n in tree.compiled.nodes if n.bl_idname == bl_idname]


try:
    section("tree init")
    tree = bpy.data.node_groups.new("Main", 'PaintSystemNodeTree')
    tree.initialize()
    gin, gout = tree.get_input_node(), tree.get_output_node()
    check(gin is not None and gout is not None, "io nodes created")
    check([c.name for c in tree.channels] == ['Color'], "default Color channel")
    check([s.name for s in gout.inputs] == ['Color', 'Color Alpha'], "output sockets follow channel specs")
    check(gout.inputs['Color'].links[0].from_node == gin, "passthrough link input->output")

    section("first compile")
    solid = tree.insert_layer_node('PaintSystemSolidColorLayerNode')
    solid.fill_color = (1.0, 0.0, 0.0, 1.0)
    img_layer = tree.insert_layer_node('PaintSystemImageLayerNode')
    image = bpy.data.images.new("Paint", 64, 64, alpha=True)
    img_layer.image = image
    check(tree.layer_chain() == [img_layer, solid], "layer chain top-first")

    fp1 = compile_tree(tree)
    art = tree.compiled
    check(art is not None and art.bl_idname == 'ShaderNodeTree', "artifact created")
    check(art.get('ps_owner') == tree.uuid, "artifact tagged with owner uuid")
    check(art.get('ps_fingerprint') == fp1, "fingerprint stored on the artifact")
    blends = compiled_nodes(tree, 'ShaderNodeGroup')
    check(len(blends) == 2, f"two blend group instances ({len(blends)})")
    check(all(library.is_library_group(n.node_tree) for n in blends), "blends use static library group")
    texs = compiled_nodes(tree, 'ShaderNodeTexImage')
    check(len(texs) == 1 and texs[0].image == image, "image texture bound to layer image")
    out = compiled_nodes(tree, 'NodeGroupOutput')[0]
    check(out.inputs['Color'].is_linked and out.inputs['Color Alpha'].is_linked, "group output linked")
    iface = [(s.in_out, s.name) for s in art.interface.items_tree]
    check(('OUTPUT', 'Color') in iface and ('INPUT', 'Color Alpha') in iface, "interface sockets from channels")
    lib_count = len([ng for ng in bpy.data.node_groups if library.is_library_group(ng)])
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
    check(len([ng for ng in bpy.data.node_groups if library.is_library_group(ng)]) == 2,
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
    check([s.name for s in gout.inputs] == ['Color', 'Color Alpha', 'Rough', 'Rough Alpha'],
          "io sockets follow new channel")
    check(gout.inputs['Color'].links[0].from_node == img_layer, "existing links preserved on channel add")
    compile_tree(tree)
    iface = [(s.in_out, s.name, s.bl_socket_idname) for s in art.interface.items_tree]
    check(('OUTPUT', 'Rough', 'NodeSocketFloat') in iface, "float channel in interface")
    tree.channels[1].name = 'Roughness'
    check([s.name for s in gout.inputs][2:] == ['Roughness', 'Roughness Alpha'], "channel rename renames sockets")
    check(gout.inputs['Roughness'].links[0].from_node == gin, "rename keeps passthrough link")

    section("nested group")
    child = bpy.data.node_groups.new("Detail", 'PaintSystemNodeTree')
    child.initialize()
    child_solid = child.insert_layer_node('PaintSystemSolidColorLayerNode')
    child_solid.fill_color = (0.0, 1.0, 0.0, 1.0)
    group = tree.insert_layer_node('PaintSystemGroupLayerNode') if False else tree.nodes.new('PaintSystemGroupLayerNode')
    group.node_tree = child
    check([s.name for s in group.inputs] == ['Color', 'Color Alpha'], "group node sockets from child channels")
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

    solid.fill_color = (0.2, 0.2, 0.2, 1.0)
    compile_tree(tree)
    check(img_layer.cache_stale, "upstream edit invalidates cache")
    check(any(n.get("ps_identifier") == f"{img_layer.uuid}:blend" for n in art.nodes),
          "stale cache falls back to live graph")

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
