# SPDX-License-Identifier: GPL-3.0-or-later
"""The brush painter, as a filter layer kind (PS-053).

`plan` decides where every stamp goes and what it looks like, in numpy.
`brushes` reads the brushes it stamps with. `build` is the kind's build
hook: the GPU passes that analyse the picture, read it at the stamp
centres, and draw the stamps `plan` returns.
"""
