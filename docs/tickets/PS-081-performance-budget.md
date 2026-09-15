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
- Compiles are synchronous so the artifact stays inside the edit's undo
  step (ARCHITECTURE.md, Triggers); a timer cannot coalesce slider drags.
  Every drag tick pays a full compile, so value-only edits need a fast
  path: a property whose IR effect is a single socket value can patch
  that socket and the stored fingerprint without rebuilding the IR, as
  long as the result equals what a full compile would produce.

Baseline on 2026-09-15 (Blender 5.2, before any of the above), opacity
edit plus compile: 5 layers 2 ms, 20 layers 14 ms, 50 layers 85 ms;
unchanged-tree fingerprint check: 1 ms, 5 ms and 26 ms.

## Acceptance

- `tests/test_perf.py` builds a 100-layer tree and asserts the budget
  with a generous CI multiplier.
