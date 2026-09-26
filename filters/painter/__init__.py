# SPDX-License-Identifier: GPL-3.0-or-later
"""The brush painter filter layer kind (PS-053).

- `plan` decides, in numpy, where each stamp goes and how it looks.
- `resizing` resizes a brush to a step's size and counts what it covers.
- `color_jitter` shifts a stamp's colour in HSV.
- `drawing` lays the brushes out in an atlas and turns stamps into quads.
- `brushes` loads the brush images the stamps use.
- `build` is the kind's build hook. It runs the GPU passes that analyse
  the picture, read it at the stamp centres, and draw the planned stamps.
"""
