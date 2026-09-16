"""Lasso lookup tables for the selection rasteriser (PS-091).

`selection/outline.py` is numpy only, so these run on every Blender
version in the headless job, GPU or not. The tables are small enough here
to state in full; `test_selection_raster.py` checks what the GPU makes of
them on real outlines.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selection_reference as reference  # noqa: E402
from harness import check, finish, guarded, import_from, register_addon, section  # noqa: E402

register_addon()
outline = import_from("selection.outline")

SQUARE = [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]


def test_closed_outline():
    section("outlines drop consecutive repeated points and need three left")
    edges = outline.closed_outline([(0, 0), (0, 0), (4, 0), (4, 4), (0, 0)])
    check(edges is not None and edges[0].tolist() == [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0]],
          f"repeats and the closing point are dropped {None if edges is None else edges[0].tolist()}")
    check(edges is not None and edges[1].tolist() == [[4.0, 0.0], [4.0, 4.0], [0.0, 0.0]],
          "and the last edge closes the outline")
    check(outline.closed_outline([(1, 1), (2, 2), (1, 1)]) is None, "two distinct points enclose nothing")
    check(outline.closed_outline([]) is None, "no points enclose nothing")
    edges = outline.closed_outline([(0, 0), (4, 4), (0, 0), (4, 4)])
    check(edges is not None and len(edges[0]) == 4,
          f"points repeated out of order are kept {None if edges is None else len(edges[0])}")


def test_parity_square():
    section("parity tables of a 2 x 2 square on a 4 x 4 image")
    a, b = outline.closed_outline(SQUARE)
    spans, keys = outline.parity_tables(a, b, 4, 4)
    check(spans.shape == (4, 1, 2) and spans.dtype == np.float32, f"one span per row {spans.shape}")
    check(spans[..., 0].ravel().tolist() == [0.0, 0.0, 2.0, 4.0], f"first keys {spans[..., 0].ravel().tolist()}")
    check(spans[..., 1].ravel().tolist() == [0.0, 4.0, 4.0, 0.0], f"counts and parity {spans[..., 1].ravel().tolist()}")
    check(len(keys) == outline.DATA_WIDTH and keys[:4].tolist() == [1.0, 3.0, 1.0, 3.0],
          f"keys {keys[:4].tolist()}, padded to {len(keys)}")


def test_parity_spans():
    section("a row's keys are split into spans with the parity carried")
    a, b = outline.closed_outline([(10.0, 1.0), (100.0, 1.0), (100.0, 3.0), (10.0, 3.0)])
    spans, keys = outline.parity_tables(a, b, 130, 4)
    check(spans.shape == (4, 3, 2), f"three spans of 64 columns per row {spans.shape}")
    check(spans[..., 0].tolist() == [[0.0, 0.0, 0.0], [0.0, 1.0, 2.0], [2.0, 3.0, 4.0], [4.0, 4.0, 4.0]],
          f"first keys {spans[..., 0].tolist()}")
    check(spans[..., 1].tolist() == [[0.0, 0.0, 0.0], [2.0, 3.0, 0.0], [2.0, 3.0, 0.0], [0.0, 0.0, 0.0]],
          f"counts and parity {spans[..., 1].tolist()}")
    check(keys[:4].tolist() == [10.0, 100.0, 10.0, 100.0], f"keys {keys[:4].tolist()}")


def test_distance_square():
    section("distance tables of the square")
    a, b = outline.closed_outline(SQUARE)
    cells, entries, bounds = outline.distance_tables(a, b, 4, 4, 0.5, outline.cell_size(0.5))
    check(cells.tolist() == [[[0.0, 4.0]]], f"one cell with four entries {cells.tolist()}")
    check(entries[:4].tolist() == [[3.0, 1.0, 3.0, 3.0], [3.0, 3.0, 1.0, 3.0],
                                   [1.0, 1.0, 3.0, 1.0], [1.0, 3.0, 1.0, 1.0]],
          f"entries nearest the cell centre first {entries[:4].tolist()}")
    want = np.array([18.374777, 18.374777, 19.839434, 19.839434], dtype=np.float32)
    check(np.allclose(bounds[:4], want, atol=1e-5), f"bounds {bounds[:4].tolist()}")


def test_distance_clipping():
    section("entries are clipped to their cell grown by the half width")
    a, b = outline.closed_outline([(-5000.0, 10.0), (5000.0, 10.0), (0.0, 5000.0)])
    cells, entries, bounds = outline.distance_tables(a, b, 64, 64, 4.0, outline.cell_size(4.0))
    check(cells.tolist() == [[[0.0, 1.0], [1.0, 1.0]], [[2.0, 0.0], [2.0, 0.0]]],
          f"only the bottom cells list the long edge {cells.tolist()}")
    check(entries[:2].tolist() == [[-5.0, 10.0, 37.0, 10.0], [-5.0, 10.0, 37.0, 10.0]],
          f"clipped to -5..37 in cell coordinates {entries[:2].tolist()}")
    check(bounds[:2].tolist() == [np.float32(5.99).item()] * 2, f"bounds {bounds[:2].tolist()}")


def test_cell_size():
    section("cell size follows the half width")
    got = [outline.cell_size(value) for value in (0.5, 8.0, 8.5, 64.0, 64.5, 512.0)]
    check(got == [32, 32, 64, 64, 128, 128], f"{got}")


def test_limits():
    section("an outline past a limit raises instead of allocating")
    a, b = outline.closed_outline([(0.5, 0.5), (1000.5, 0.5), (500.5, 900.5)])
    for name, value, run in (
            ("MAX_KEYS", 1000, lambda: outline.parity_tables(a, b, 1024, 1024)),
            ("MAX_PAIRS", 1000, lambda: outline.distance_tables(a, b, 1024, 1024, 4.0, 32)),
            ("MAX_ENTRIES", 10, lambda: outline.distance_tables(a, b, 1024, 1024, 4.0, 32))):
        saved = getattr(outline, name)
        setattr(outline, name, value)
        try:
            run()
            check(False, f"{name} = {value} raises OutlineTooComplex")
        except outline.OutlineTooComplex:
            check(True, f"{name} = {value} raises OutlineTooComplex")
        finally:
            setattr(outline, name, saved)
    spans, _ = outline.parity_tables(a, b, 1024, 1024)
    check(spans.shape == (1024, 16, 2), "and the defaults build the same outline")


def test_chunking():
    section("tables built in small chunks match those built at once")
    a, b = outline.closed_outline(np.array(reference.star(150, 0.5, 0.5, 0.45, 0.2, 7)) * 256)
    want = (outline.parity_tables(a, b, 256, 256)
            + outline.distance_tables(a, b, 256, 256, 4.0, outline.cell_size(4.0)))
    saved = outline.CHUNK
    outline.CHUNK = 7
    try:
        got = (outline.parity_tables(a, b, 256, 256)
               + outline.distance_tables(a, b, 256, 256, 4.0, outline.cell_size(4.0)))
    finally:
        outline.CHUNK = saved
    check(all(np.array_equal(x, y) for x, y in zip(want, got)), "tables do not depend on the chunk size")


for test in (test_closed_outline,
             test_parity_square,
             test_parity_spans,
             test_distance_square,
             test_distance_clipping,
             test_cell_size,
             test_limits,
             test_chunking):
    guarded(test)

finish("SELECTION OUTLINE TEST")
