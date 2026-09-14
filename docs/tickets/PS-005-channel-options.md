# PS-005 Channel options: alpha socket, colour space, factor range, defaults

Epic A. Size M. Milestone M2 (needed by templates in M1 for `use_alpha`).

## v2 behaviour

`Channel` (`paintsystem/data.py:2300-2490`) has:

- `type` COLOR/VECTOR/FLOAT (icons from `CHANNEL_TYPE_ENUM`, `data.py:207`).
- `use_alpha`: expose a `<name> Alpha` output on the group.
- `color_space` COLOR/NONCOLOR: bake image colour space and interface
  subtype (`data.py:2339`, `bake_operators.py:232`).
- `use_max_min`, `factor_min`, `factor_max` for FLOAT: interface socket gets
  FACTOR subtype and min/max (`data.py:443-490, 2594`).
- Channel default value is edited on the material group node's unlinked
  input socket (`panels/channels_panels.py:55-63`).
- `alpha_clamp_start` / `alpha_clamp_end` Clamp nodes bracket the alpha
  path (`data.py:1866-1886`).

## v3 design

- Add to `PaintSystemChannel`: `use_alpha` (default True for COLOR, False
  otherwise), `color_space`, `use_max_min`, `factor_min`, `factor_max`,
  `default_value` (FloatVector size 4, used as the interface input default).
- `channel_socket_specs` already emits value + alpha sockets on the Paint
  System side. Keep both internally; `interface_outputs` in
  `compiler/core.py` skips the alpha output when `use_alpha` is False.
- `interface_inputs`/`interface_outputs` set subtype `FACTOR` and min/max
  when `use_max_min`, and `NONE` otherwise. Colour channels with
  `color_space == 'NONCOLOR'` are still `NodeSocketColor`; the value only
  affects bake images and the isolate-channel view transform.
- Alpha clamp: emit one `ShaderNodeClamp` on each channel's alpha before the
  group output (role `"<channel uuid>:alpha_clamp"`).
- The default value edited in the channel list is the material group
  node's input socket, as in v2. `interface_inputs` writes
  `default_value` from the channel so a freshly linked group starts with
  it.

## Acceptance

- Test: toggling `use_alpha` removes/adds the interface output and the
  material link survives if the socket still exists.
- Test: FLOAT channel with min/max yields a FACTOR interface socket with
  the given range; switching it off restores a plain float without
  identifier churn.
- Channel UIList (PS-032) shows the default value widget.
