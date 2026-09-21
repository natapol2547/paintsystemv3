"""The compiler package.

It re-exports nothing. Callers import the module they need, such as
``compiler.core`` or ``compiler.ir``, directly.

``core`` is imported inside ``register`` and ``unregister``, not at the
top. So importing one compiler module does not also load ``core`` and
everything it imports. This avoids a circular import.
``nodetree.stack_ops`` imports ``compiler.builder``, and ``core`` imports
``stack_ops``. If this file imported ``core`` at the top, it would find
``stack_ops`` half loaded whenever ``stack_ops`` is imported first.
"""


def register():
    from . import core
    core.reset_state()


def unregister():
    from . import core
    core.reset_state()
