# PS-002 Append groups from `library2.blend` through `compiler/library.py`

Epic A. Size S. Milestone M1.

## Goal

Reuse the hand-built v2 node groups that are not worth regenerating in
Python, while keeping the single-owner and versioning rules of the
compiler.

## v2 behaviour

`get_library_nodetree` (`paintsystem/graph/common.py:45-100`) appends a group
by name from `paintsystem/library2.blend` with `bpy.data.libraries.load`,
and `LIBRARY_NODE_TREE_VERSIONS` (`common.py:17-22`) forces re-append of
stale groups in `versioning.py:125-140`.

Groups in the file (name: inputs -> outputs):

- `.PS UV Parallax`: Depth, UV, Tangent, Normal -> Vector, Mask
- `.PS Object Parallax`: Depth -> Vector
- `.PS Projection`: Vector, Rotation, FOV, Scale, Object Space, Enable,
  Falloff -> Vector, Mask
- `.PS Orthographic`: Vector, Rotation, Orthographic Scale, Enable, Fallof
  -> Vector, Mask
- `.PS Occlusion`: TargetPosition, Bias -> IsVisible
- `.PS Tangent Normal`: Custom Normal, Tangent -> Tangent Normal
- `.PS Correct Aspect`: Resolution X, Resolution Y -> Vector
- `.PS Pre Mix`, `.PS Post Mix`, `.PS Porter-Duff Over`, `.Alpha Over`
  (superseded by PS-001)

## v3 design

- Copy `library2.blend` into `paintsystemv3/compiler/library.blend`. Drop
  the mixing groups, the material, objects and `PS Camera Plane Old` from
  the copy so it only carries what v3 appends.
- Add `get_appended_group(name)` next to `get_library_group`. It loads the
  group with `libraries.load(link=False)`, renames it to `.PS Lib <name>`,
  stamps `ps_lib_version = LIBRARY_VERSION` and `ps_lib_source = 'blend'`.
  Both kinds carry the `.PS Lib ` name prefix, which is how library groups
  are recognised. Like a generated group, an appended group gets no fake
  user: a group no artifact uses is dropped when the file is saved and
  appended again on next use.
- Version bump replaces the group in place: append the new copy, then
  `user_remap` the old datablock to it and remove the old one, so existing
  artifact instances keep working without a recompile.
- Expose typed helpers used by later tickets: `projection_group()`,
  `uv_parallax_group()`, `object_parallax_group()`, `correct_aspect_group()`,
  `tangent_normal_group()`, `occlusion_group()`.

## Acceptance

- Headless test appends every helper twice; the second call returns the
  same datablock and `bpy.data.node_groups` count is unchanged.
- Bumping `LIBRARY_VERSION` in the test swaps the datablock and remaps
  users.
- No `Material`, `Object` or `Image` datablocks leak into the file after
  appending.
