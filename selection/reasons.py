"""What the UI says when the live selection cannot be used (PS-091).

Every reason code a selection problem can have is in `TEXTS`, with two
texts:

- `message` is the full sentence. It is `str()` of a
  `raster.MaskUnavailable` and `session.State.message`, which operators
  report and `raster.availability` returns.
- `label` is the short form that fits on a sidebar line
  (`session.label`).

The target reasons come from `session.resolve_target`. The others are
`raster.MaskUnavailable` reasons.
"""
from typing import NamedTuple


class Text(NamedTuple):
    message: str
    label: str


TEXTS = {
    # The live selection has nothing it can apply to.
    'NO_LAYER': Text("No active layer", "No active layer"),
    'NO_IMAGE': Text("Active layer has no image", "Active layer has no image"),
    'UDIM': Text("UDIM layers are not supported yet", "UDIM layers are not supported yet"),
    'NO_UV_MAP': Text("Layer's UV map is missing", "Layer's UV map is missing"),
    # The mask cannot be built.
    'NO_GPU': Text("This Blender session has no GPU context, so the selection cannot be built",
                   "No GPU context"),
    'NO_SIZE': Text("The selection has no image with pixels to take its size from", "Layer image has no pixels"),
    'TOO_LARGE': Text("The image is too large for a selection mask", "Image too large for a selection"),
    'UNSUPPORTED': Text("This selection operation cannot be built", "Operation not supported yet"),
    'TOO_COMPLEX': Text("The lasso outline is too complex to build", "Lasso too complex"),
    'SELF_TEST': Text("The GPU failed the selection self-test, so selections are disabled in this session",
                      "GPU failed the selection self-test"),
    'GPU_ERROR': Text("The GPU could not build the selection right now; try again", "GPU error, retrying"),
    'SURFACE': Text("The object or UV map this selection was drawn on is gone", "Selection's object or UV map is gone"),
    'VIEW': Text("This selection's view is invalid", "Selection's view is invalid"),
    'EDIT_MODE': Text("Leave Edit Mode to use this selection", "Leave Edit Mode to use the selection"),
}

MALFORMED_POINTS = "A selection operation has malformed points"
