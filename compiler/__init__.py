"""The compiler package. It re-exports nothing: callers import the module they
need (compiler.core, compiler.ir, ...) directly.

``core`` is imported inside the hooks rather than at the top, so importing
any one compiler module does not also load ``core`` and everything it imports.
That matters for ``nodetree.stack_ops``: it imports ``compiler.builder``, and
``core`` imports ``stack_ops``, so an eager import here would find
``stack_ops`` half loaded whenever it is imported first.
"""


def register():
    from . import core
    core.reset_state()


def unregister():
    from . import core
    core.reset_state()
