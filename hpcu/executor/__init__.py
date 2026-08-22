"""Executor — Action DSL execution.

The Executor consumes prepared Action objects and dispatches them to an
InputInjector: semantic first, physical as a fallback.  It also owns the
settle detector used to wait for the scene to become stable.
"""

from hpcu.executor.executor import Executor, PreparedAction

__all__ = ["Executor", "PreparedAction"]
