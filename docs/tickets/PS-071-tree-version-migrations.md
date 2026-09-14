# PS-071 Tree version migration framework

Epic H. Size S. Milestone M4 (put in place early; cheap).

## v2 behaviour

Layer graphs carried a version in the frame label; `versioning.py:111`
and `handlers.py:77` rebuilt outdated ones; library group versions in
`LIBRARY_NODE_TREE_VERSIONS`.

## v3 design

- `PaintSystemNodeTree.version` (exists, currently 2) becomes the
  document schema version. `migration/tree.py` holds an ordered list of
  `(from_version, fn)` steps run by `normalize_tree` when
  `tree.version < CURRENT_TREE_VERSION`, inside `suspend_compile`.
- Node-level changes (renamed properties, new sockets) are handled in
  steps by walking `tree.nodes`; the registry (PS-029) gives the class
  per node.
- Library groups already rebuild on `LIBRARY_VERSION`; artifacts never
  need migration because they are regenerated.
- Steps must be pure and idempotent; add a test that runs each step
  twice on a fixture tree.

## Acceptance

- A fixture tree at version 2 loaded after bumping to 3 with a rename step
  ends at version 3 with the renamed property populated.
