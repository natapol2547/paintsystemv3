# SPDX-License-Identifier: GPL-3.0-or-later
"""The kinds of filter a filter layer can run (PS-057).

A filter layer holds one flat property per parameter of every kind, so
that the compiler's hashes can see them: `IR._serialize` falls through to
``repr()`` for a PropertyGroup, and ``repr()`` of one is a data path
rather than its contents, so a parameter group would be invisible to
every hash in the addon. A `LayerFilterSpec` says which of those flat
properties belong to which kind, so the node draws the right ones and a
build fingerprint hashes the right ones.

Adding a kind is a spec here plus its properties on
`PaintSystemFilterLayerNode`; nothing else in the layer machinery moves.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LayerFilterSpec:
    """One entry of a filter layer's ``filter_type`` enum."""

    # The enum identifier, which is also what a build fingerprint records.
    name: str
    label: str
    description: str
    # Names of the node properties this kind reads, in draw order.
    params: tuple[str, ...] = ()


BLUR = LayerFilterSpec(
    name='BLUR',
    label="Gaussian Blur",
    description="Blur everything below this layer",
    params=('blur_sigma',),
)

LAYER_FILTERS = {spec.name: spec for spec in (BLUR,)}


def layer_filter_items() -> list[tuple[str, str, str]]:
    """``EnumProperty`` items for choosing what a filter layer does."""
    return [(spec.name, spec.label, spec.description) for spec in LAYER_FILTERS.values()]


def layer_filter_params(filter_type: str) -> tuple[str, ...]:
    spec = LAYER_FILTERS.get(filter_type)
    return spec.params if spec is not None else ()
