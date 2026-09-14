# PS-072 Legacy v1 data

Epic H. Size S. Milestone M4.

## v2 behaviour

`update_paint_system_data` (`operators/versioning_operators.py:66`)
converted the original (v1) data model into v2 by reading values out of
the old node graphs; the main panel showed a legacy warning box with
"Save As" and the update button (`main_panels.py:152-170`).

## v3 design

Do not port the v1 converter. When v1 data is detected
(`material.paint_system_layers` or whatever v1 property remains
registered), show a box: "This file was made with Paint System 1.x. Open
it with Paint System 2.x to convert, then open it here." Link to the
release page. Register the v1 property group read-only so detection
works.

## Acceptance

- A v1 fixture shows the box and no exception on load.
