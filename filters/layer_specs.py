# SPDX-License-Identifier: GPL-3.0-or-later
"""The kinds of filter a filter layer can run (PS-057).

A filter layer holds one flat property per parameter of every kind, so
that the compiler's hashes can see them: `IR._serialize` falls through to
``repr()`` for a PropertyGroup, and ``repr()`` of one is a data path
rather than its contents, so a parameter group would be invisible to
every hash in the addon. A `LayerFilterSpec` says which of those flat
properties belong to which kind, so the node draws the right ones and a
build fingerprint hashes the right ones.

A kind also says which `filters.core.FilterSpec` it runs and with what
push constants, so that adding one is a spec here plus its properties on
`PaintSystemFilterLayerNode` and nothing else in the layer machinery
moves. The two vocabularies are deliberately separate: a node property is
what the user sets, a push constant is what the shader reads, and a kind
is free to derive one from the other or to hold it constant.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import registry
from .core import FilterSpec, Refused


@dataclass(frozen=True)
class LayerFilterSpec:
    """One entry of a filter layer's ``filter_type`` enum."""

    # The enum identifier, which is also what a build fingerprint records.
    name: str
    label: str
    description: str
    # The filter this kind runs, and a function reading the push
    # constants for it off the node.
    filter: FilterSpec
    params_of: Callable[[Any], dict]
    # Names of the node properties this kind reads, in draw order.
    params: tuple[str, ...] = ()


def _invert_params(node) -> dict:
    return {
        # The stack below arrives scene linear, where inverting directly
        # turns a mid grey almost white. `encode` runs the inversion on
        # the sRGB encoding instead, which is what a paint program means
        # by Invert.
        "encode": 1,
        "channels": (1.0, 1.0, 1.0, 1.0 if node.invert_alpha else 0.0),
    }


INVERT = LayerFilterSpec(
    name='INVERT',
    label="Invert",
    description="Invert the colours of everything below this layer",
    filter=registry.INVERT,
    params_of=_invert_params,
    params=('invert_alpha',),
)

LAYER_FILTERS = {spec.name: spec for spec in (INVERT,)}


def layer_filter_items() -> list[tuple[str, str, str]]:
    """``EnumProperty`` items for choosing what a filter layer does."""
    return [(spec.name, spec.label, spec.description) for spec in LAYER_FILTERS.values()]


def layer_filter_params(filter_type: str) -> tuple[str, ...]:
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
