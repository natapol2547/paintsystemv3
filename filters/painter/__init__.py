# SPDX-License-Identifier: GPL-3.0-or-later
"""The brush painter filter layer kind (PS-053).

- `plan` decides, in numpy, where each stamp goes and how it looks.
- `brushes` loads the brush images the stamps use.
- `build` is the kind's build hook. It runs the GPU passes that analyse
  the picture, read it at the stamp centres, and draw the planned stamps.
"""
