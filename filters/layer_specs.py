# SPDX-License-Identifier: GPL-3.0-or-later
"""The kinds of filter a filter layer can run (PS-057).

A filter layer holds one flat property per parameter of every kind, so
that the compiler's hashes can see them: `IR._serialize` falls through to
``repr()`` for a PropertyGroup, and ``repr()`` of one is a data path
rather than its contents, so a parameter group would be invisible to
every hash in the addon. A `LayerFilterSpec` says which of those flat
properties belong to which kind, so the node draws the right ones and a
build fingerprint hashes the right ones.

A kind also says which `filters.core.FilterSpec` passes it runs and with
what push constants, so that adding one is a spec here plus its
properties on `PaintSystemFilterLayerNode` and nothing else in the layer
machinery moves. The two vocabularies are deliberately separate: a node
property is what the user sets, a push constant is what the shader reads,
and a kind is free to derive one from the other or to hold it constant.

A kind runs a *list* of passes rather than one. A separable blur is two
per iteration, and how many iterations depends on how wide it is, so the
pass list is a function of the node like the push constants are. A kind
that returns an empty list asks for the stack below unchanged, which is
how a blur set to zero costs nothing.

A kind that is not a list of passes builds its result itself. The
painter is one: its stamps are geometry planned per stamp on the CPU,
not a pass over every texel. It gives `build` instead, `settings_of` for
what that is handed, and a `fingerprint_of` saying what the pixels
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
    # The passes this kind runs over the stack below, in order, as
    # ``(spec, push constants)`` read off the node.
    passes_of: Callable[[Any], list[tuple[FilterSpec, dict]]] = _no_passes
    # Names of the node properties this kind reads, in draw order. An
    # entry may also be ``(heading, names)``, drawn as a group of its own.
    params: tuple = ()
    # ``build(settings, texture, pool)``, a generator of ``(label,
    # fraction)`` that takes the composited stack below and returns the
    # result, both scene linear and straight, both the pool's. None for a
    # kind whose passes are the whole build.
    build: Callable | None = None
    # What `build` is handed as *settings*, read off the node. Called
    # when the build starts, with its fingerprint, rather than by the hook
    # when it gets there: a setting moved in between must not reach
    # pixels stamped as built without it.
    settings_of: Callable[[Any], Any] | None = None
    # What a build fingerprint records for this kind, JSON-able. None to
    # record the passes, which is right for any kind that has them.
    fingerprint_of: Callable[[Any], list] | None = None

    def fingerprint(self, node) -> list:
        """What the pixels of a build of *node* depend on, besides the stack.

        The passes as the build would run them, rather than the node
        properties behind them: a kind is free to derive one from the
        other, and what the pixels depend on is the derived form.
        """
        if self.fingerprint_of is not None:
            return self.fingerprint_of(node)
        return [[spec.name, params] for spec, params in self.passes_of(node)]

    def param_names(self) -> tuple[str, ...]:
        """Every node property this kind reads, groups flattened."""
        names = []
        for entry in self.params:
            names.extend((entry,) if isinstance(entry, str) else entry[1])
        return tuple(names)


def _invert_passes(node) -> list[tuple[FilterSpec, dict]]:
    return [(registry.INVERT, {
        # The stack below arrives scene linear, where inverting directly
        # turns a mid grey almost white. `encode` runs the inversion on
        # the sRGB encoding instead, which is what a paint program means
        # by Invert.
        "encode": 1,
        "channels": (1.0, 1.0, 1.0, 1.0 if node.invert_alpha else 0.0),
    })]


INVERT = LayerFilterSpec(
    name='INVERT',
    label="Invert",
    description="Invert the colours of everything below this layer",
    passes_of=_invert_passes,
    params=('invert_alpha',),
)


def _blur_passes(node) -> list[tuple[FilterSpec, dict]]:
    return [(registry.BLUR, params) for params in registry.blur_passes(node.blur_sigma)]


BLUR = LayerFilterSpec(
    name='BLUR',
    label="Blur",
    description="Blur everything below this layer",
    passes_of=_blur_passes,
    params=('blur_sigma',),
)

def _sharpen_passes(node) -> list[tuple[FilterSpec, dict]]:
    """Blur the stack below, then add back what the blur took away.

    The combine reads the unblurred stack through `second`, which
    `filters.layer_build` binds: by the time it runs, the chain's own
    texture holds the blur.
    """
    passes = [(registry.BLUR, params)
              for params in registry.blur_passes(node.sharpen_radius)]
    # The stack below arrives scene linear, so the difference is taken on
    # its sRGB encoding, as Invert's is and for the same reason.
    passes.append((registry.SHARPEN, {"strength": node.sharpen_strength, "encode": 1}))
    return passes


SHARPEN = LayerFilterSpec(
    name='SHARPEN',
    label="Sharpen",
    description="Bring out the detail of everything below this layer",
    passes_of=_sharpen_passes,
    params=('sharpen_radius', 'sharpen_strength'),
)

def _painterly_fingerprint(node) -> list:
    # The brush by name: a preset's images ship with the add-on, and a
    # change to them comes with a `derived.FILTER_VERSION` of its own.
    return [["painterly", Settings.of(node).as_dict()]]


PAINTERLY = LayerFilterSpec(
    name='PAINTERLY',
    label="Painterly",
    description="Repaint everything below this layer in brush strokes that follow its edges",
    params=(
        'painter_brush',
        ("Strokes", ('painter_steps', 'painter_density', 'painter_max_scale',
                     'painter_min_scale', 'painter_start_opacity', 'painter_end_opacity')),
        ("Direction", ('painter_sigma', 'painter_threshold', 'painter_rotation',
                       'painter_random_rotation', 'painter_rotation_range')),
        ("Colour Variation", ('painter_hue', 'painter_saturation', 'painter_value')),
        'painter_seed',
    ),
    build=painter_build.build,
    settings_of=Settings.of,
    fingerprint_of=_painterly_fingerprint,
)

LAYER_FILTERS = {spec.name: spec for spec in (INVERT, BLUR, SHARPEN, PAINTERLY)}


def layer_filter_items() -> list[tuple[str, str, str]]:
    """``EnumProperty`` items for choosing what a filter layer does."""
    return [(spec.name, spec.label, spec.description) for spec in LAYER_FILTERS.values()]


def layer_filter_params(filter_type: str) -> tuple:
    """What a filter layer of *filter_type* draws, as `LayerFilterSpec.params` holds it."""
    spec = LAYER_FILTERS.get(filter_type)
    return spec.params if spec is not None else ()


def layer_filter_kind(node) -> LayerFilterSpec:
    """The kind of filter *node* is set to.

    Refuses rather than falling back for a kind that is not registered: a
    file saved by a build that had one this build does not is worth
    saying out loud, instead of quietly filtering with something else.
    """
    spec = LAYER_FILTERS.get(node.filter_type)
    if spec is None:
        raise Refused(f"'{node.name}' asks for a filter this build does not have "
                      f"({node.filter_type})")
    return spec
