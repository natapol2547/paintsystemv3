"""The selection is document data on the tree (PS-091).

The point of storing the ops rather than the mask is that Blender's own
undo and its file format then cover the selection for free, the way they
cover mesh selection. These tests hold that: ops survive undo and a save
and reload, and `prefix_digests` and `ops_hash` change when and only when
the mask would. The pinned digests catch an accidental change to what goes
into them; a deliberate one updates the pins.
"""
import os
import sys
import tempfile

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, fail, finish, guarded, import_from, register_addon, section  # noqa: E402

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

    for kind in ('INVERT', 'TRANSFORM'):
        op = sel.add_op(kind, 'REPLACE')
        check(len(sel.ops) == 2 and op.mode == 'ADD',
              f"{kind} with REPLACE keeps what came before and is stored as ADD ({len(sel.ops)}, {op.mode})")
        sel.ops.remove(1)


def test_invert_toggles():
    section("invert toggles a trailing INVERT")
    sel = fresh_selection()
    sel.add_op('BOX', 'REPLACE', points=[(0.1, 0.1), (0.5, 0.5)])
    before = sel.prefix_digests(64, 64, 1001)[-1]
    sel.invert()
    check([op.kind for op in sel.ops] == ['BOX', 'INVERT'] and sel.ops[-1].mode == 'ADD',
          f"the first invert appends INVERT {[op.kind for op in sel.ops]}")
    check(sel.prefix_digests(64, 64, 1001)[-1] != before, "and changes the digest")
    sel.invert()
    check([op.kind for op in sel.ops] == ['BOX'], f"the second removes it {[op.kind for op in sel.ops]}")
    check(sel.prefix_digests(64, 64, 1001)[-1] == before, "and the digest is the original again")
    sel.clear()
    sel.invert()
    check([op.kind for op in sel.ops] == ['ALL'] and sel.ops[0].mode == 'REPLACE',
          f"inverting an empty selection selects all {[op.kind for op in sel.ops]}")
    sel.invert()
    check(sel.is_empty, "inverting all leaves no selection rather than an empty mask")
    sel.add_op('BOX', 'REPLACE', points=[(0.1, 0.1), (0.5, 0.5)])
    sel.add_op('ALL', 'ADD')
    sel.invert()
    check(sel.is_empty, "so does inverting ops that end in an added ALL")
    sel.add_op('BOX', 'REPLACE', points=[(0.1, 0.1), (0.5, 0.5)])
    sel.add_op('ALL', 'INTERSECT')
    sel.invert()
    check([op.kind for op in sel.ops] == ['BOX', 'ALL', 'INVERT'],
          f"an intersected ALL keeps what came before, so it is inverted {[op.kind for op in sel.ops]}")


def test_selection_has_no_image():
    section("the selection applies to the active layer, not a stored image")
    check('image' not in selection_props.PaintSystemSelection.bl_rna.properties,
          "PaintSystemSelection has no image property")


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


def test_digests_are_exact():
    section("digests see every bit of a value and ignore what a replace hides")
    sel = fresh_selection()
    op = sel.add_op('LASSO', 'REPLACE', points=[(0.1, 0.1), (0.9, 0.1), (0.5, 0.9)], feather=1.00001)
    first = sel.ops_hash()
    op.feather = 1.00002
    check(sel.ops_hash() != first, "feather 1.00001 and 1.00002 differ")
    op.feather = 1.00001
    op.set_points([(0.1, 0.1), (0.9, 0.1), (0.5, 0.9 + 1e-9)])
    check(sel.ops_hash() != first, "a point moved by 1e-9 differs")
    op.feather = 5000.0
    check(op.feather == 1024.0, f"feather is clamped to 1024 ({op.feather})")

    sel.add_op('ELLIPSE', 'ADD', points=[(0.2, 0.2), (0.6, 0.6)])
    sel.add_op('INVERT')
    check(len(sel.prefix_digests()) == 3 and sel.prefix_digests()[-1].hex() == sel.ops_hash(),
          "one digest per op, the last of them being ops_hash")

    hidden = fresh_selection()
    hidden.add_op('BOX', points=[(0.0, 0.0), (0.5, 0.5)])
    replace = hidden.ops.add()
    replace.kind = 'BOX'
    replace.set_points([(0.2, 0.2), (0.4, 0.4)])
    hidden.add_op('INVERT')
    shown = hidden.prefix_digests(64, 64, 1001)
    hidden.ops.remove(0)
    check(hidden.prefix_digests(64, 64, 1001) == shown[1:], "ops before a replacing REPLACE change no digest")
    check(hidden.chain_start() == 0, f"chain_start of the trimmed ops is 0 ({hidden.chain_start()})")
    first = hidden.ops.add()
    first.kind = 'ELLIPSE'
    hidden.ops.move(2, 0)
    check(hidden.chain_start() == 1, f"and 1 with an op in front ({hidden.chain_start()})")

    # `add_op` stores INVERT as ADD; ops.add() leaves the default REPLACE.
    sel = fresh_selection()
    sel.add_op('BOX', points=[(0.2, 0.25), (0.6, 0.7)])
    raw_invert = sel.ops.add()
    raw_invert.kind = 'INVERT'
    check(raw_invert.mode == 'REPLACE' and sel.chain_start() == 0,
          f"an INVERT stored with REPLACE does not move chain_start ({raw_invert.mode}, {sel.chain_start()})")
    raw = sel.prefix_digests(64, 64, 1001)
    sel.clear()
    sel.ops.add().kind = 'INVERT'
    lone = sel.prefix_digests(64, 64, 1001)
    sel.clear()
    sel.add_op('BOX', points=[(0.2, 0.25), (0.6, 0.7)])
    sel.add_op('INVERT')
    check(raw[-1] != lone[-1] and raw == sel.prefix_digests(64, 64, 1001),
          "an INVERT stored with REPLACE chains like one stored with ADD")


def test_points_written_directly():
    section("points written straight to the ID property digest without raising")
    key = selection_props.POINTS_KEY
    points_view = selection_props.points_view
    sel = fresh_selection()
    op = sel.add_op('LASSO')
    bare = sel.prefix_digests(64, 64, 1001)

    op.set_points([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    floats = sel.prefix_digests(64, 64, 1001)
    op[key] = [0, 0, 1, 0, 1, 1, 0, 1]
    stored = points_view(op[key]).format
    check(stored != 'd' and sel.prefix_digests(64, 64, 1001) == floats,
          f"integer points (format {stored}) digest the same as the equal float points")

    pairs = [(0.1, 0.1), (0.9, 0.2), (0.5, 0.9)]
    op.set_points(pairs)
    well_formed = sel.prefix_digests(64, 64, 1001)
    check(points_view(op[key]) is not None, "points_view accepts what set_points stores")
    malformed = {
        "a list of pairs": pairs,
        "a string": "0.1 0.1 0.9 0.2 0.5 0.9",
        "an odd length": [0.1, 0.1, 0.9, 0.2, 0.5, 0.9, 0.5],
    }
    for name, value in malformed.items():
        op[key] = value
        check(points_view(op[key]) is None, f"{name}: points_view reports it malformed")
        try:
            digests = sel.prefix_digests(64, 64, 1001)
            sel.ops_hash()
        except Exception as error:
            fail(f"{name}: prefix_digests or ops_hash raised {error!r}")
            continue
        check(digests[-1] != well_formed[-1] and digests[-1] != bare[-1],
              f"{name}: digests unlike the well-formed points and unlike no points")


def test_pinned_digests():
    section("digests are pinned")
    chains = {
        "box feather 16 at 256": ((256, 256, 1001), [
            dict(kind='BOX', points=[(0.25, 0.25), (0.75, 0.5)], feather=16.0)],
            ['4bba1807b9033d4db572840dbdc45761997232b9']),
        "three ops": ((0, 0, 0), [
            dict(kind='BOX', points=[(0.1, 0.1), (0.5, 0.5)]),
            dict(kind='ELLIPSE', mode='ADD', points=[(0.2, 0.2), (0.4, 0.4)], feather=4.0, antialias=False),
            dict(kind='INVERT', mode='ADD')],
            ['18c3367ba57e339ae80babef0f53000bfe90af47', '9e98d625f5845cd04b8a963ae4907d627c647ff6',
             '36cc48d9a09582b7f864824fb7586f65040befaf']),
        "view lasso": ((0, 0, 0), [
            dict(kind='LASSO', space='VIEW', points=[(10.0, 10.0), (200.0, 40.0), (120.0, 300.0)], feather=3.0,
                 through=True, region_size=(1920, 1080), view_matrix=[2.0] * 16)],
            ['97da54f9a281d0eebe169e65aaa2014b81f55cb6']),
        "tile 1022": ((64, 64, 1022), [
            dict(kind='ALL'),
            dict(kind='LASSO', mode='SUBTRACT', points=[(1.25, 2.25), (1.75, 2.25), (1.5, 2.75)], feather=2.5)],
            ['82596415688579a3d4015e99a60a805a3f2354ff', 'e0f1e020c2c68c61e08630513029ab42b999b7e6']),
    }
    for name, (size, ops, want) in chains.items():
        sel = fresh_selection()
        for op in ops:
            values = dict(op)
            sel.add_op(values.pop('kind'), values.pop('mode', 'REPLACE'), values.pop('space', 'UV'), **values)
        got = [digest.hex() for digest in sel.prefix_digests(*size)]
        check(got == want, f"{name}: {got}")


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
             test_invert_toggles,
             test_selection_has_no_image,
             test_points_round_trip,
             test_defaults_come_from_the_selection,
             test_hash_tracks_what_the_mask_depends_on,
             test_digests_are_exact,
             test_points_written_directly,
             test_pinned_digests,
             test_order_matters_to_the_hash,
             test_undo_restores_the_ops,
             test_save_and_reload_keeps_the_ops):
    guarded(test)

finish("SELECTION MODEL TEST")
