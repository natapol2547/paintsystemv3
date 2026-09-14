# PS-066 Multi-object and multi-material operator base

Epic G. Size S. Milestone M2.

## v2 behaviour

`MultiMaterialOperator` (`operators/common.py:32`): `execute` fans out
over the paint object plus selected meshes (excluding "PS Camera
Plane"), per material slot or active material, deduplicated, calling
`process_material` under `temp_override(object, active_material)`,
counting failures and reporting a tally. Used by the layer creation
operators so one click adds a layer to every selected object's
material.

## v3 design

Port to `ops/mixins.py::MultiMaterialOperator` resolving the tree from
each material's active slot (PS-040). Operators that create nodes run
inside `suspend_compile` so the fan-out compiles once per tree.

## Acceptance

- Two cubes with different materials selected: adding an image layer
  creates one layer per tree; a cube without a Paint System tree is
  skipped with a warning count.
