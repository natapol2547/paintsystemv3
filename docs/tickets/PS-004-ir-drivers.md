# PS-004 IR support for drivers

Epic A. Size S. Milestone M2.

## v2 behaviour

Fake-light gradient layers add TRANSFORMS drivers on the Combine XYZ
inputs so the light direction follows the empty's rotation live
(`paintsystem/data.py:921-939`).

## Design

- `IR.driver(node_id, socket_name, index, variables, expression)` records a
  driver spec; `IRNode` gains `drivers: list[IRDriver]`.
- `IR.apply` reconciles drivers after nodes and links: for each spec,
  `socket.driver_add('default_value', index)` if missing, then set
  `expression` and variables (type `TRANSFORMS`, target object, transform
  type, space). Drivers present on an IR-managed socket but not in the IR
  are removed. Drivers on user-owned nodes (PS-003) are left alone.
- Fingerprint includes driver specs. Object targets serialise as
  `["id", "Object", name]` like other IDs.
- Builder helper in `compiler/builder.py`: `NodeTreeBuilder.sync_drivers`.

## Acceptance

- Test: emit a Value node driven by an empty's rotation, rotate the empty,
  evaluate the depsgraph, the socket value follows. Recompile is a no-op
  (fingerprint stable, driver objects not recreated).
- Test: removing the driver spec from the IR removes the driver.

## Alternative considered

Reading the empty's rotation at compile time and writing constants would
require a depsgraph handler to detect object movement and recompile per
frame. Drivers keep the artifact live for free.
