# SPDX-License-Identifier: GPL-3.0-or-later
"""The kinds of filter a filter layer can run (PS-057).

Each kind is a `LayerFilterSpec`, listed in `LAYER_FILTERS`. Adding a
kind means a spec here, a settings group in
`nodes.layers.filter_settings` and a pointer to it on
`PaintSystemFilterLayerNode`, listed in its ``ps_unhashed_props``.
Nothing else in the filter layer code changes.

- Each kind owns a group of settings on the layer, such as
  ``node.blur``, and a spec names it. The node draws the kind's group,
  and a build fingerprint records what the kind reads from it.
- A kind says which `filters.core.FilterSpec` passes it runs, and with
  what push constants. Node properties and push constants are kept
  separate on purpose. A node property is what the user sets. A push
  constant is what the shader reads. A kind may derive one from the
  other, or hold a push constant fixed.
- A kind runs a list of passes, not one. A separable blur is two passes
  per iteration, and the number of iterations depends on its width, so
  the list is computed from the node like the push constants. An empty
  list leaves the stack below unchanged, which is how a blur set to
  zero costs nothing.
- A kind that is not a list of passes builds its result itself. The
  painter is one: its stamps are geometry planned one by one on the CPU,
  not a pass over every texel. It gives `build` instead, `settings_of`
  for what `build` is handed, and `fingerprint_of` for what the pixels
  depend on, since there are no passes to read that from.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import registry
from .core import FilterSpec, Refused
from .painter import build as painter_build
from .painter.plan import Settings


def _no_passes(node) -> list[tuple[FilterSpec, dict]]:
    return []


@dataclass(frozen=True)
class LayerFilterSpec:
    """One entry of a filter layer's ``filter_type`` enum."""

    # The enum identifier, which is also what a build fingerprint records.
    name: str
    label: str
    description: str
    # The node property holding this kind's settings group.
    settings: str
    # The passes this kind runs over the stack below, in order, as
    # ``(spec, push constants)`` read off the node.
    passes_of: Callable[[Any], list[tuple[FilterSpec, dict]]] = _no_passes
    # Names of the settings in the group, in draw order. An entry may
    # also be ``(heading, names)``, drawn as a column of its own.
    params: tuple = ()
    # ``build(settings, texture, pool)``: a generator that yields
    # ``(label, fraction)``, takes the composited stack below, and
    # returns the result. Both textures are scene linear with straight
    # alpha, and both belong to the pool. None for a kind whose passes
    # are the whole build.
    build: Callable | None = None
    # Reads what `build` gets as *settings* from the node. Called when
    # the build starts, together with the fingerprint, not later by the
    # hook. Otherwise a setting changed in between could reach pixels
    # stamped as built without it.
    settings_of: Callable[[Any], Any] | None = None
    # What a build fingerprint records for this kind, as JSON-compatible
    # data. None records the passes, which is right for any kind that has
    # them.
    fingerprint_of: Callable[[Any], list] | None = None

    def fingerprint(self, node) -> list:
        """What the pixels of a build of *node* depend on, besides the stack.

        By default, the passes as the build would run them, not the node
        properties behind them. A kind may derive one from the other, and
        the pixels depend on the derived form.
        """
        if self.fingerprint_of is not None:
            return self.fingerprint_of(node)
        return [[spec.name, params] for spec, params in self.passes_of(node)]


def _invert_passes(node) -> list[tuple[FilterSpec, dict]]:
    return [(registry.INVERT, {
        # The stack below is scene linear, and inverting that directly
        # turns a mid grey almost white. `encode` inverts the sRGB
        # encoding instead, which is what a paint program means by
        # Invert.
        "encode": 1,
        "channels": (1.0, 1.0, 1.0, 1.0 if node.invert.alpha else 0.0),
    })]


INVERT = LayerFilterSpec(
    name='INVERT',
    label="Invert",
    description="Invert the colours of everything below this layer",
    settings='invert',
    passes_of=_invert_passes,
    params=('alpha',),
)


def _blur_passes(node) -> list[tuple[FilterSpec, dict]]:
    return [(registry.BLUR, params) for params in registry.blur_passes(node.blur.sigma)]


BLUR = LayerFilterSpec(
    name='BLUR',
    label="Blur",
    description="Blur everything below this layer",
    settings='blur',
    passes_of=_blur_passes,
    params=('sigma',),
)

def _sharpen_passes(node) -> list[tuple[FilterSpec, dict]]:
    """Blur the stack below, then add back what the blur took away.

    The sharpen pass reads the unblurred stack through `second`, which
    `filters.layer_build` binds, because by the time it runs the chain's
    own texture holds the blur.
    """
    passes = [(registry.BLUR, params)
              for params in registry.blur_passes(node.sharpen.radius)]
    # The stack below is scene linear, so the difference is taken on its
    # sRGB encoding, like Invert and for the same reason.
    passes.append((registry.SHARPEN, {"strength": node.sharpen.strength, "encode": 1}))
    return passes


SHARPEN = LayerFilterSpec(
    name='SHARPEN',
    label="Sharpen",
    description="Bring out the detail of everything below this layer",
    settings='sharpen',
    passes_of=_sharpen_passes,
    params=('radius', 'strength'),
)

def _painterly_fingerprint(node) -> list:
    # The brush is recorded by name. A preset's images ship with the
    # addon, and changing them means increasing `derived.FILTER_VERSION`.
    # The settings are recorded as the build reads them, with the blur in
    # texels, so a change of resolution is also a change of blur.
    return [["painterly", Settings.of(node).as_dict()]]


PAINTERLY = LayerFilterSpec(
    name='PAINTERLY',
    label="Painterly",
    description="Repaint everything below this layer in brush strokes that follow its edges",
    settings='painter',
    params=(
        'brush',
        ("Strokes", ('largest_stroke', 'smallest_stroke', 'passes', 'first_opacity', 'last_opacity')),
        ("Placement", ('coverage', 'edge_threshold')),
        ("Direction", ('smoothing', 'rotation', 'random_rotation')),
        ("Colour Variation", ('hue', 'saturation', 'value')),
        'seed',
    ),
    build=painter_build.build,
    settings_of=Settings.of,
    fingerprint_of=_painterly_fingerprint,
)

LAYER_FILTERS = {spec.name: spec for spec in (INVERT, BLUR, SHARPEN, PAINTERLY)}


def layer_filter_items() -> list[tuple[str, str, str]]:
    """``EnumProperty`` items for choosing what a filter layer does."""
    return [(spec.name, spec.label, spec.description) for spec in LAYER_FILTERS.values()]


def layer_filter_kind(node) -> LayerFilterSpec:
    """The kind of filter *node* is set to.

    Raises `Refused` for a kind that is not registered, instead of
    falling back to another kind. A file saved by a version with a kind
    this version lacks should say so, not quietly filter with something
    else.
    """
    spec = LAYER_FILTERS.get(node.filter_type)
    if spec is None:
        raise Refused(f"'{node.name}' asks for a filter this build does not have "
                      f"({node.filter_type})")
    return spec
