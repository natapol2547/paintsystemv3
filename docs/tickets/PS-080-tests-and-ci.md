# PS-080 Per-feature tests and CI

Epic I. Size M. Milestone M1, extended by every later ticket.

## Current state

`tests/smoke_compile.py` runs headless through `tests/run.sh` against
Blender 5.2 LTS and covers the compiler core. `.github/workflows` has a
v2-style matrix (`main.yml`, `pull_request.yml`) that must be re-pointed.

## Design

- Split `tests/` into `test_compiler.py`, `test_stack.py`,
  `test_layers_<type>.py`, `test_ui_ops.py`, `test_migration.py`, sharing
  `tests/harness.py` (register addon, `check`, `section`, pixel helpers,
  `render_pixel(obj, uv)` that bakes a 1x1 patch through
  `bake_refs_to_image` for blend parity tests).
- `tests/run.sh` accepts a file glob; CI downloads Blender 5.2 LTS with
  the existing workflow's download step and runs all tests with
  `--factory-startup -b`. GPU filter tests (PS-050) are marked and
  skipped when `gpu.platform.backend_type_get() == 'NONE'`.
- v2 parity tests (PS-001, PS-013, PS-070) install the v2 addon from
  `~/paintsystem` into the same Blender in a separate process and dump
  reference pixels to `tests/fixtures/`; the fixtures are committed so CI
  does not need v2.

## Acceptance

- `tests/run.sh` runs all files and exits non-zero on any failure.
- CI green on push.
