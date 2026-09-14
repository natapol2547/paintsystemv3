# PS-063 Layer actions (frame and marker based visibility)

Epic G. Size M. Milestone M2.

## v2 behaviour

`MarkerAction` (`data.py:591-615`): `action_bind` FRAME/MARKER,
`action_type` ENABLE/DISABLE, `frame`, `marker_name`, `enabled`.
`Layer.actions` + `add_action`/`delete_action`
(`layers_operators.py:935, 996`). `frame_change_pre` (`handlers.py:35`)
sorts actions per layer, resolves markers, applies the last applicable
action, writes `layer.enabled` only on change. UI: "Actions" sub-panel
with `PAINTSYSTEM_UL_Actions` (`layers_panels.py:462-492, 899-919`); row
icon KEYTYPE_KEYFRAME_VEC when actions exist.

## v3 design

- `props/action.py::PaintSystemLayerAction` PropertyGroup;
  `PaintSystemLayerNode.actions` CollectionProperty + `active_action_index`.
- Handler ported. Writing `enabled` marks the tree dirty and the compile
  patches one Opacity value, so playback cost is one socket write per
  change; acceptable. If profiling (PS-081) shows the timer flush lagging
  during playback, switch to `flush_now()` inside the handler.
- Operators `add_action` / `delete_action` and the UI ported.

## Acceptance

- Two actions (disable at 10, enable at 20): scrubbing shows the layer
  hidden between frames 10 and 19 and the compile fingerprint changes
  exactly twice.
