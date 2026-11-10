"""Anchor — the context engine that pins facts before it compresses.

Exports ``AnchorEngine``, a ``ContextEngine`` subclass, and ``register()``
which calls ``ctx.register_context_engine(...)``. Installed as a Hermes
plugin directory (``$HERMES_HOME/plugins/anchor/``) or registered through
the general plugin system. Activate with::

    context:
      engine: "anchor"
"""

try:
    from .engine import AnchorEngine, register
except ImportError:  # flat-module load (plugin host isolation)
    from engine import AnchorEngine, register  # type: ignore[no-redef]

__all__ = ["AnchorEngine", "register"]
