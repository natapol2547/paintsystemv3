"""The selection is document data on the tree (PS-091).

The point of storing the ops rather than the mask is that Blender's own
undo and its file format then cover the selection for free, the way they
cover mesh selection. These tests hold that: ops survive undo and a save
and reload, and `ops_hash` changes when and only when the mask would.
"""
import os
import sys
import tempfile

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, guarded, import_from, register_addon, section  # noqa: E402

register_addon()
selection_props = import_from("props.selection")

TREE = "PS Selection Tree"


def tree():
    """A Paint System tree, made once."""
    existing = bpy.data.node_groups.get(TREE)
    if existing is not None:
        return existing
    made = bpy.data.node_groups.new(TREE, 'PaintSystemNodeTree')
    made.initialize()
    return made


def fresh_selection():
    got = tree().selection
    got.clear()
    return got


def test_ops_are_ordered_and_replace_truncates():
    section("ops keep their order and a replace drops what came before")
    sel = fresh_selection()
    check(sel.is_empty, "a new selection has no ops")

    sel.add_op('BOX', 'REPLACE', points=[(0.1, 0.1), (0.5, 0.5)])
    sel.add_op('ELLIPSE', 'ADD', points=[(0.2, 0.2), (0.4, 0.4)])
    sel.add_op('INVERT', 'ADD')
    check([op.kind for op in sel.ops] == ['BOX', 'ELLIPSE', 'INVERT'],
          f"three ops in the order they were added {[op.kind for op in sel.ops]}")

    sel.add_op('LASSO', 'REPLACE', points=[(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)])
    check([op.kind for op in sel.ops] == ['LASSO'],
          f"a replace leaves only itself {[op.kind for op in sel.ops]}")
    check(sel.ops[0].mode == 'REPLACE', "and it keeps its own mode")


def test_points_round_trip():
    section("outlines round-trip through the ID property")
    sel = fresh_selection()
    points = [(0.0, 0.0), (1.0, 0.25), (0.5, 1.0), (0.125, 0.75)]
    op = sel.add_op('LASSO', 'REPLACE', points=points)
    got = op.get_points()
    check(len(got) == len(points), f"{len(got)} points came back, {len(points)} went in")
    check(all(abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6
              for a, b in zip(got, points)),
          f"every coordinate survived {got}")
    check(sel.add_op('INVERT', 'ADD').get_points() == [],
          "an op with no outline reports an empty list, not None")


def test_defaults_come_from_the_selection():
    section("new ops take the selection's feather and anti-alias")
    sel = fresh_selection()
    sel.feather = 6.5
    sel.antialias = False
    op = sel.add_op('BOX', 'REPLACE', points=[(0, 0), (1, 1)])
    check(abs(op.feather - 6.5) < 1e-6, f"feather came across as {op.feather}")
    check(op.antialias is False, "anti-alias came across")
    explicit = sel.add_op('BOX', 'ADD', points=[(0, 0), (1, 1)], feather=1.0)
    check(abs(explicit.feather - 1.0) < 1e-6,
          f"an explicit value wins over the default, {explicit.feather}")
    sel.feather = 0.0
    sel.antialias = True


def test_hash_tracks_what_the_mask_depends_on():
    section("ops_hash changes when, and only when, the mask would")
    sel = fresh_selection()
    check(sel.ops_hash() == "", "an empty selection hashes to the empty string")

    op = sel.add_op('BOX', 'REPLACE', space='UV', points=[(0.1, 0.1), (0.5, 0.5)])
    first = sel.ops_hash()
    check(first != "", "a selection with an op has a hash")
    check(sel.ops_hash() == first, "the hash is stable when nothing changes")

    op.feather = 4.0
    check(sel.ops_hash() != first, "changing the feather changes the hash")
    op.feather = 0.0
    check(sel.ops_hash() == first, "and changing it back restores it")

    op.set_points([(0.1, 0.1), (0.6, 0.5)])
    check(sel.ops_hash() != first, "moving a point changes the hash")
    op.set_points([(0.1, 0.1), (0.5, 0.5)])

    # A UV op does not care what the viewport is doing.
    op.view_matrix = [2.0] * 16
    op.region_size = (1920, 1080)
    check(sel.ops_hash() == first,
          "the view matrices do not touch the hash of a UV op")

    op.space = 'VIEW'
    view = sel.ops_hash()
    check(view != first, "the same op in view space hashes differently")
    op.view_matrix = [3.0] * 16
    check(sel.ops_hash() != view, "and now the view matrix does count")

    sel.clear()
    sel.add_op('BOX', 'REPLACE', space='UV', points=[(0.1, 0.1), (0.5, 0.5)])
    check(sel.ops_hash() == first, "an identical selection rebuilt from scratch hashes the same")


def test_order_matters_to_the_hash():
    section("two orderings of the same ops hash differently")
    sel = fresh_selection()
    sel.add_op('BOX', 'REPLACE', points=[(0.0, 0.0), (0.5, 0.5)])
    sel.add_op('ELLIPSE', 'SUBTRACT', points=[(0.2, 0.2), (0.8, 0.8)])
    forward = sel.ops_hash()

    sel.clear()
    sel.add_op('ELLIPSE', 'REPLACE', points=[(0.2, 0.2), (0.8, 0.8)])
    sel.add_op('BOX', 'SUBTRACT', points=[(0.0, 0.0), (0.5, 0.5)])
    check(sel.ops_hash() != forward, "swapping the ops changes the hash")


def test_undo_restores_the_ops():
    section("Blender's undo covers the selection")
    sel = fresh_selection()
    sel.add_op('BOX', 'REPLACE', points=[(0.1, 0.1), (0.5, 0.5)])
    before = sel.ops_hash()
    bpy.ops.ed.undo_push(message="one op")

    tree().selection.add_op('ELLIPSE', 'ADD', points=[(0.2, 0.2), (0.4, 0.4)])
    after = tree().selection.ops_hash()
    bpy.ops.ed.undo_push(message="two ops")
    check(after != before and len(tree().selection.ops) == 2, "the second op is in place")

    bpy.ops.ed.undo()
    restored = bpy.data.node_groups.get(TREE)
    check(restored is not None, "the tree is still there after undo")
    if restored is None:
        return
    check(len(restored.selection.ops) == 1,
          f"undo took the second op back, {len(restored.selection.ops)} left")
    check(restored.selection.ops_hash() == before,
          "and the selection hashes as it did before the op was added")

    bpy.ops.ed.redo()
    restored = bpy.data.node_groups.get(TREE)
    check(restored is not None and restored.selection.ops_hash() == after,
          "redo puts it back")


def test_save_and_reload_keeps_the_ops():
    section("the selection is saved with the file")
    sel = fresh_selection()
    sel.add_op('LASSO', 'REPLACE', space='VIEW',
               points=[(10.0, 10.0), (200.0, 40.0), (120.0, 300.0)],
               feather=3.0, through=True)
    sel.ops[0].region_size = (1920, 1080)
    sel.ops[0].view_matrix = [float(i) for i in range(16)]
    before = sel.ops_hash()
    points_before = sel.ops[0].get_points()
    # In use a tree is referenced by a material; this one has no users, and
    # Blender does not write a zero-user datablock.
    tree().use_fake_user = True

    path = os.path.join(tempfile.mkdtemp(prefix="ps_selection_"), "selection.blend")
    check(bpy.ops.wm.save_as_mainfile(filepath=path) == {'FINISHED'}, "saved")
    check(bpy.ops.wm.open_mainfile(filepath=path) == {'FINISHED'}, "reopened")

    reloaded = bpy.data.node_groups.get(TREE)
    check(reloaded is not None, "the tree came back")
    if reloaded is None:
        return
    check(len(reloaded.selection.ops) == 1,
          f"one op came back, {len(reloaded.selection.ops)}")
    op = reloaded.selection.ops[0]
    check(op.kind == 'LASSO' and op.space == 'VIEW' and op.through,
          f"its fields came back ({op.kind}, {op.space}, through={op.through})")
    check(op.get_points() == points_before,
          f"the outline in the ID property came back {op.get_points()}")
    check(reloaded.selection.ops_hash() == before,
          "so the mask built from the reopened file is the same mask")


for test in (test_ops_are_ordered_and_replace_truncates,
             test_points_round_trip,
             test_defaults_come_from_the_selection,
             test_hash_tracks_what_the_mask_depends_on,
             test_order_matters_to_the_hash,
             test_undo_restores_the_ops,
             test_save_and_reload_keeps_the_ops):
    guarded(test)

finish("SELECTION MODEL TEST")
