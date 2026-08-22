"""Plugin registry — lazy-load, constructor-injected modules.

The registry is the sole coupling point between the runtime core
and concrete platform implementations.  Modules never register themselves.
"""

from typing import Any, Callable


class PluginRegistry:
    """A simple name-to-factory registry.

    Modules are registered by bootstrap code only.  A module that
    registers itself is a contract violation.
    """

    def __init__(self):
        self._factories: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, factory: Callable[..., Any]) -> None:
        """Register a factory function under a name.

        Args:
            name: Unique name for this plugin (e.g. "capture.browser-cdp").
            factory: A callable that returns an instance of the plugin.
        """
        self._factories[name] = factory

    def get(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Instantiate and return a plugin by name.

        Args:
            name: The plugin name.
            *args, **kwargs: Passed to the factory.

        Raises:
            KeyError: If the name is not registered.
        """
        factory = self._factories.get(name)
        if factory is None:
            raise KeyError(f"Plugin not registered: {name}")
        return factory(*args, **kwargs)

    def has(self, name: str) -> bool:
        """Check whether a plugin is registered."""
        return name in self._factories

    def remove(self, name: str) -> None:
        """Remove a plugin from the registry.

        Removing a plugin must not crash the control loop.
        Core modules that call `registry.get(name)` must handle
        KeyError with a fallback or skip.
        """
        self._factories.pop(name, None)

    def list(self) -> tuple[str, ...]:
        """Return all registered plugin names."""
        return tuple(self._factories.keys())
