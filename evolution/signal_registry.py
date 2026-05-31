"""
evolution/signal_registry.py — Unified Signal Registry
=======================================================

A thread-safe singleton registry that manages all signals — both
hand-crafted adapters and LLM-evolved signals — under a single interface.

Design:
  • Singleton: one global registry instance per process.
  • Thread-safe: all mutations protected by threading.Lock.
  • Source tagging: every signal is tagged 'adapted' or 'evolved'.
  • Decorator API: @registry.register makes registration declarative.
  • No circular imports: registry does NOT import any signal module;
    signal modules import the registry.

Usage
-----
    from evolution.signal_registry import registry

    # Register via decorator (in signal module)
    @registry.register("my_signal", source="evolved", version="0.1.0")
    class MySignal(BaseSignal):
        ...

    # Register manually
    registry.register_cls("my_signal", MySignal, source="adapted")

    # Retrieve
    sig_cls = registry.get("my_signal")
    sig = sig_cls()

    # List all
    for entry in registry.list_all():
        print(entry["name"], entry["source"], entry["version"])
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Type

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# RegistryEntry
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RegistryEntry:
    """Metadata + class for one registered signal."""
    name:        str
    cls:         type                    # BaseSignal subclass (not instantiated)
    source:      str = "evolved"         # 'adapted' | 'evolved'
    version:     str = "0.1.0"
    description: str = ""
    tags:        List[str] = field(default_factory=list)

    def instantiate(self, **kwargs) -> object:
        """Create a fresh instance of this signal."""
        return self.cls(**kwargs)

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "source":      self.source,
            "version":     self.version,
            "description": self.description,
            "tags":        self.tags,
            "cls":         f"{self.cls.__module__}.{self.cls.__qualname__}",
        }


# ─────────────────────────────────────────────────────────────────────────────
# SignalRegistry
# ─────────────────────────────────────────────────────────────────────────────

class SignalRegistry:
    """
    Thread-safe singleton registry for all BaseSignal subclasses.

    Do not instantiate directly — use the module-level `registry` singleton.
    """

    _instance: Optional["SignalRegistry"] = None
    _init_lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "SignalRegistry":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._store: Dict[str, RegistryEntry] = {}
                    inst._lock  = threading.Lock()
                    cls._instance = inst
        return cls._instance

    # ── Registration ──────────────────────────────────────────────────────────

    def register_cls(
        self,
        name: str,
        cls: type,
        source: str = "evolved",
        version: str = "0.1.0",
        description: str = "",
        tags: Optional[List[str]] = None,
        overwrite: bool = False,
    ) -> None:
        """
        Register a signal class.

        Args:
            name:        Unique signal name (used for CLI commands and file naming).
            cls:         The BaseSignal subclass (not an instance).
            source:      'adapted' or 'evolved'.
            version:     Semantic version string.
            description: One-line description (shown in `evolve list`).
            tags:        Optional list of tags (e.g. ['momentum', 'volume']).
            overwrite:   If True, silently replace an existing entry.
                         If False (default), raise ValueError on duplicate.

        Raises:
            ValueError: If name already registered and overwrite=False.
            TypeError:  If cls is not a BaseSignal subclass.
        """
        # Deferred import to avoid circular dependency
        from evolution.base_signal import BaseSignal
        if not (isinstance(cls, type) and issubclass(cls, BaseSignal)):
            raise TypeError(
                f"registry.register_cls: '{name}' — cls must be a BaseSignal "
                f"subclass, got {cls!r}"
            )

        with self._lock:
            if name in self._store and not overwrite:
                raise ValueError(
                    f"Signal '{name}' already registered. "
                    f"Use overwrite=True to replace, or choose a different name."
                )
            entry = RegistryEntry(
                name=name,
                cls=cls,
                source=source,
                version=version,
                description=description or (cls.__doc__ or "").strip().split("\n")[0],
                tags=tags or [],
            )
            self._store[name] = entry

        logger.info(
            "SignalRegistry: registered '%s' (source=%s, v=%s)",
            name, source, version,
        )

    def register(
        self,
        name: str,
        source: str = "evolved",
        version: str = "0.1.0",
        description: str = "",
        tags: Optional[List[str]] = None,
        overwrite: bool = False,
    ):
        """
        Decorator that registers a BaseSignal subclass.

        Usage:
            @registry.register("my_signal", source="evolved", version="0.2.0")
            class MySignal(BaseSignal):
                ...
        """
        def decorator(cls: type) -> type:
            self.register_cls(
                name=name,
                cls=cls,
                source=source,
                version=version,
                description=description,
                tags=tags,
                overwrite=overwrite,
            )
            return cls
        return decorator

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def get(self, name: str) -> type:
        """
        Retrieve a signal class by name.

        Returns:
            The BaseSignal subclass (not an instance).

        Raises:
            KeyError: If no signal with that name is registered.
        """
        with self._lock:
            if name not in self._store:
                available = sorted(self._store.keys())
                raise KeyError(
                    f"Signal '{name}' not found in registry. "
                    f"Available: {available}"
                )
            return self._store[name].cls

    def get_entry(self, name: str) -> RegistryEntry:
        """Retrieve a RegistryEntry by name (includes metadata)."""
        with self._lock:
            if name not in self._store:
                raise KeyError(f"Signal '{name}' not found.")
            return self._store[name]

    def instantiate(self, name: str, **kwargs) -> object:
        """
        Retrieve and instantiate a signal by name.

        Equivalent to: registry.get(name)(**kwargs)
        """
        return self.get(name)(**kwargs)

    def contains(self, name: str) -> bool:
        """Return True if a signal with this name is registered."""
        with self._lock:
            return name in self._store

    # ── Listing ───────────────────────────────────────────────────────────────

    def list_all(self) -> List[dict]:
        """
        Return summary dicts for all registered signals.

        Returns:
            List of dicts, sorted by (source, name):
            [{name, source, version, description, tags, cls}, ...]
        """
        with self._lock:
            entries = list(self._store.values())
        return [
            e.to_dict()
            for e in sorted(entries, key=lambda e: (e.source, e.name))
        ]

    def list_by_source(self, source: str) -> List[dict]:
        """
        Return signals filtered by source ('adapted' or 'evolved').

        Args:
            source: 'adapted' | 'evolved'
        """
        return [e for e in self.list_all() if e["source"] == source]

    def list_names(self) -> List[str]:
        """Return all registered signal names, sorted."""
        with self._lock:
            return sorted(self._store.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            names = sorted(self._store.keys())
        return iter(names)

    def __contains__(self, name: str) -> bool:
        return self.contains(name)

    def __repr__(self) -> str:
        n_adapted = len(self.list_by_source("adapted"))
        n_evolved = len(self.list_by_source("evolved"))
        return (
            f"<SignalRegistry total={len(self)} "
            f"adapted={n_adapted} evolved={n_evolved}>"
        )

    # ── Dev helpers ───────────────────────────────────────────────────────────

    def unregister(self, name: str) -> None:
        """
        Remove a signal from the registry.
        Intended for testing only — not for production use.
        """
        with self._lock:
            if name in self._store:
                del self._store[name]
                logger.debug("SignalRegistry: unregistered '%s'", name)

    def clear(self) -> None:
        """
        Remove ALL registered signals.
        Testing only — clears the singleton's store.
        """
        with self._lock:
            self._store.clear()
        logger.warning("SignalRegistry.clear() called — all signals removed.")

    def print_table(self) -> None:
        """Pretty-print the registry contents to stdout."""
        entries = self.list_all()
        if not entries:
            print("(no signals registered)")
            return
        print(f"{'NAME':<25} {'SOURCE':<10} {'VERSION':<10} DESCRIPTION")
        print("─" * 80)
        for e in entries:
            print(
                f"{e['name']:<25} {e['source']:<10} {e['version']:<10} "
                f"{e['description'][:40]}"
            )


# ── Module-level singleton ────────────────────────────────────────────────────

registry = SignalRegistry()
"""
The global SignalRegistry singleton.

Import this in signal modules:
    from evolution.signal_registry import registry
"""
