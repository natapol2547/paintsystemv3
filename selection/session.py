"""The live selection: what it applies to, and keeping what is derived from it in step (PS-091).

The selection is document data on each tree, but only one is live at a
time: the selection of the active object's active tree. It applies to
the active layer, whatever that is, the way a Photoshop selection stays
put while the user switches layers. Its mask is built at the size of that
layer's image and sampled through that layer's UV map.

Nothing in Blender reports a change to the ops: writing them from an
operator or a script tags no depsgraph update and notifies no message bus
subscriber. So everything that can change what the selection shows calls
`notify()`: the selection operators and tools after an edit,
`context.update_active_image` when the active layer, tree, object or
material changes, the mode and scene subscriptions, a geometry update of
an object (a renamed or removed UV map), and the undo, redo and load
handlers. `notify()` only schedules a timer, which makes it safe from an
operator, a handler, a message bus callback or a draw callback, and
coalesces a burst of calls into one sync.

The timer runs `sync()`. It resolves the target, compares a `State` with
the one it last synced and stops there when nothing changed and the mask
is still cached. Otherwise it builds the mask once, remembering a failure
per digest so a mask that cannot be built is not tried again on every
tick, and retrying `GPU_ERROR` a few times. A built mask that selects
nothing, such as a box dragged over empty background, counts as no
selection (`State.empty`) while its ops stay on the tree. A sync that
gets past the comparison hands the state on to `stencil.sync`, then `overlay.sync`,
and tags the 3D views and image editors for redraw. A consumer that
raises is logged, and the next sync reaches the consumers again.
`notify(force=True)` forgets the last state first, so that sync reaches
everything even when the state is unchanged: undo, redo and a file read
restore the ops and Blender's own settings independently.
"""
import dataclasses
import logging
from dataclasses import dataclass

import bpy

from .. import context as ps_context
from ..gpu_passes import core
from ..gpu_passes.texel_map import resolve_uv_map
from . import overlay, raster, stencil

log = logging.getLogger(__name__)

RETRY_INTERVAL = 0.25
"""Seconds before a build that hit `GPU_ERROR` is tried again."""

RETRY_LIMIT = 4
"""Tries of a transient failure for one state before the message stays up."""

FAILURE_MEMO_LIMIT = 64
"""Failed digests remembered before the memo starts over."""

NO_TREE = "NO_TREE"
NO_LAYER = "NO_LAYER"
NO_IMAGE = "NO_IMAGE"
UDIM = "UDIM"
NO_UV_MAP = "NO_UV_MAP"

_TARGET_MESSAGES = {
    NO_LAYER: "No active layer",
    NO_IMAGE: "Active layer has no image",
    UDIM: "UDIM layers are not supported yet",
    NO_UV_MAP: "Layer's UV map is missing",
}

_LABELS = {
    'NO_GPU': "No GPU context",
    'NO_SIZE': "Layer image has no pixels",
    'TOO_LARGE': "Image too large for a selection",
    'UNSUPPORTED': "Operation not supported yet",
    'TOO_COMPLEX': "Lasso too complex",
    'SELF_TEST': "GPU failed the selection self-test",
    'GPU_ERROR': "GPU error, retrying",
    'SURFACE': "Selection's object or UV map is gone",
    'VIEW': "Selection's view is invalid",
    'EDIT_MODE': "Leave Edit Mode to use the selection",
}

NOTHING_SELECTED = "Nothing selected"
"""Label of a selection whose mask selects no texel (`State.empty`)."""

GPU_ERROR_GIVEN_UP = "GPU error, change the selection to retry"
"""Label of `GPU_ERROR` once `RETRY_LIMIT` tries have run and the timer has stopped."""

_REDRAW_AREAS = frozenset(('VIEW_3D', 'IMAGE_EDITOR'))


@dataclass(frozen=True)
class Target:
    """What the live selection applies to, resolved now. Do not keep across ticks."""
    tree: bpy.types.NodeTree
    object: bpy.types.Object | None
    layer: bpy.types.Node
    image: bpy.types.Image
    uv_map: str
    size: tuple[int, int]
    tile: int = 1001


@dataclass(frozen=True)
class State:
    """What the derived outputs depend on, as values that survive undo.

    `selected` is True when the live tree's selection has ops. `digest`
    is the mask's cache key, empty without a target. `reason` and
    `message` are empty when the mask is available, else a target problem
    (`NO_LAYER`, `NO_IMAGE`, `UDIM`, `NO_UV_MAP`) or a `MaskUnavailable`
    reason, with its UI message. `empty` is True when the mask was built
    and selects nothing (`SelectionMask.is_empty`): the ops stay, but it
    counts as no selection, so painting is not clipped and nothing is
    drawn.
    """
    scene_uid: int = 0
    tree_uid: int = 0
    object_uid: int = 0
    image_uid: int = 0
    uv_map: str = ""
    size: tuple[int, int] = (0, 0)
    tile: int = 1001
    selected: bool = False
    digest: bytes = b""
    paint_mode: bool = False
    reason: str = ""
    message: str = ""
    empty: bool = False

    @property
    def active(self) -> bool:
        """A selection exists, and its mask is built, cached and selects something."""
        return self.selected and not self.reason and not self.empty


EMPTY = State()

_last: State | None = None
_failures: dict[bytes, tuple[str, str]] = {}
_retries: dict[bytes, int] = {}
_pending_force = False
_consumer_failed = False


def resolve_target(context) -> tuple[Target | None, str]:
    """The target of the active tree's selection, or None and a reason."""
    ps = ps_context.parse_context(context)
    if ps.tree is None:
        return None, NO_TREE
    layer = ps.layer
    if layer is None:
        return None, NO_LAYER
    image = getattr(layer, 'paint_image', None)
    if image is None:
        return None, NO_IMAGE
    if image.source == 'TILED':
        return None, UDIM
    uv_map = ""
    if ps.ps_object is not None:
        uv_map = resolve_uv_map(ps.ps_object, getattr(layer, 'uv_map', ''))
        if uv_map is None:
            return None, NO_UV_MAP
    return Target(ps.tree, ps.ps_object, layer, image, uv_map, raster.image_size(image)), ""


def _state(context) -> tuple[State, Target | None]:
    scene_uid = context.scene.session_uid
    paint_mode = context.mode == 'PAINT_TEXTURE'
    target, reason = resolve_target(context)
    if target is None:
        tree = ps_context.get_active_tree(context)
        if tree is None or not len(tree.selection.ops):
            return State(scene_uid, tree.session_uid if tree is not None else 0, paint_mode=paint_mode), None
        return State(scene_uid, tree.session_uid, selected=True, paint_mode=paint_mode,
                     reason=reason, message=_TARGET_MESSAGES[reason]), None
    selection = target.tree.selection
    selected = len(selection.ops) > 0
    # The provider is called for outlined VIEW ops only, so a selection
    # drawn in UV space resolves no surface.
    digest = (selection.prefix_digests(*target.size, target.tile, surface_key=raster.view_key)[-1]
              if selected else b"")
    reason, message = _failures.get(digest, ("", "")) if selected else ("", "")
    return State(scene_uid, target.tree.session_uid,
                 target.object.session_uid if target.object is not None else 0,
                 target.image.session_uid, target.uv_map, target.size, target.tile, selected, digest,
                 paint_mode, reason, message), target


def _build(state: State, target: Target) -> State:
    """Build the mask for *state*, turning a failure into the state's reason and an empty mask into `empty`.

    Failures that depend on the objects a `VIEW` op was drawn on
    (`raster.GEOMETRY_REASONS`) are not remembered: several object states
    share one digest, and fixing the cause changes no op.
    """
    if core.gpu_known() is not True:
        # Never start a background GPU context from here (see gpu_passes.core).
        return dataclasses.replace(state, reason='NO_GPU', message=raster.MESSAGES['NO_GPU'], empty=False)
    try:
        empty = raster.get_mask(target.tree.selection, target.size, target.tile).is_empty()
    except raster.MaskUnavailable as error:
        if error.reason == 'GPU_ERROR':
            _retries[state.digest] = _retries.get(state.digest, 0) + 1
        elif error.reason not in raster.GEOMETRY_REASONS:
            if len(_failures) >= FAILURE_MEMO_LIMIT:
                _failures.clear()
            _failures[state.digest] = (error.reason, str(error))
        return dataclasses.replace(state, reason=error.reason, message=str(error), empty=False)
    except RuntimeError as error:
        # Reading the mask back raises when no GPU context is active.
        log.debug("Selection mask could not be read back: %s", str(error))
        _retries[state.digest] = _retries.get(state.digest, 0) + 1
        return dataclasses.replace(state, reason='GPU_ERROR', message=raster.MESSAGES['GPU_ERROR'], empty=False)
    _retries.pop(state.digest, None)
    return dataclasses.replace(state, empty=empty)


def sync(context=None, force: bool = False) -> State:
    """Bring everything derived from the live selection in step with it.

    What the timer runs; tests and the save handler call it directly.
    Cheap when nothing changed. *force* forgets the last state, so the
    state reaches every consumer even when it is unchanged.
    """
    global _last, _consumer_failed
    context = context or bpy.context
    if force:
        _last = None
        _retries.clear()
    state, target = _state(context)
    built = False
    if target is not None and state.selected and not state.reason:
        if _last is not None and state.digest == _last.digest:
            # The same digest is the same mask, so it is still as empty.
            state = dataclasses.replace(state, empty=_last.empty)
        if state != _last or raster.peek_mask(target.tree.selection, target.size, target.tile) is None:
            state = _build(state, target)
            # A mask rebuilt after an eviction is news even when the state
            # compares equal; a build that failed the same way again is not.
            built = state.active
    if state == _last and not built and not _consumer_failed:
        return state
    _last = state
    _consumer_failed = False
    for consumer in (stencil, overlay):
        try:
            consumer.sync(state, target)
        except Exception:
            # Logged, not raised: the timer would stop. The next sync reaches
            # the consumers again, even with an equal state.
            log.exception("Selection could not update %s", consumer.__name__)
            _consumer_failed = True
    _tag_redraw(context)
    return state


def _tick():
    global _pending_force
    force, _pending_force = _pending_force, False
    state = sync(force=force)
    if state.reason != 'GPU_ERROR':
        return None
    tries = _retries.get(state.digest, 0)
    if tries < RETRY_LIMIT:
        return RETRY_INTERVAL
    log.info("Selection mask still unavailable after %d tries: %s", tries, state.message)
    return None


def notify(force: bool = False) -> None:
    """Schedule a sync on the next timer tick. Safe from anywhere, including draw callbacks.

    *force* makes that sync reach every consumer even when the state is
    unchanged; it holds until the tick runs, whatever later calls pass.
    """
    global _pending_force
    if force:
        _pending_force = True
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.0)


def current() -> State:
    """The state last synced, for draw callbacks and panels, which must not sync."""
    return _last if _last is not None else EMPTY


def label(state: State) -> str:
    """Short text for *state*'s problem that fits a sidebar line; `state.message` has the full sentence.

    `NOTHING_SELECTED` for an empty mask, which is not a problem.
    """
    if state.empty:
        return NOTHING_SELECTED
    if state.reason == 'GPU_ERROR' and _retries.get(state.digest, 0) >= RETRY_LIMIT:
        return GPU_ERROR_GIVEN_UP
    return _TARGET_MESSAGES.get(state.reason) or _LABELS.get(state.reason, state.message)


def forget_failures() -> None:
    """Drop remembered failures; after a file read, with the raster cache."""
    _failures.clear()
    _retries.clear()


def release() -> None:
    """Stop the timer and forget everything; on unregister."""
    global _last, _pending_force, _consumer_failed
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _last = None
    _pending_force = False
    _consumer_failed = False
    forget_failures()


def _tag_redraw(context) -> None:
    window_manager = getattr(context, 'window_manager', None)
    for window in getattr(window_manager, 'windows', ()):
        for area in window.screen.areas:
            if area.type in _REDRAW_AREAS:
                area.tag_redraw()
