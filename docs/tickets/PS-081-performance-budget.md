# PS-081 Performance budget and profiling

Epic I. Size M. Milestone M3.

## Goal

Keep the compile step invisible during painting and layer editing.

## Budget

| Operation | Target |
|---|---|
| Compile of an unchanged 100-layer tree (fingerprint hit) | < 5 ms |
| Property patch (opacity slider drag) on 100 layers | < 15 ms per flush |
| Structural change (insert layer) on 100 layers | < 60 ms |
| `stack()` + `refresh_layer_rows` | < 2 ms |
| UIList draw of 100 rows | no per-row graph walks |

## Work

- `compiler/profile.py`: `PS_PROFILE=1` env var logs per-phase timings
  (normalize, build_ir, fingerprint, apply, arrange).
- Skip `arrange` on value-only patches (builder reports whether nodes were
  created).
- Cache `node_state` per node per compile; avoid `bl_rna` iteration in
  the hot loop by caching property name lists per class.
- `subtree_hash` memoised per compile.
- Timer flush coalescing verified under slider drags (one compile per
  redraw at most).

## Acceptance

- `tests/test_perf.py` builds a 100-layer tree and asserts the budget
  with a generous CI multiplier.
