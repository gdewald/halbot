"""Layered config: default -> registry (HKLM) -> runtime override.

Field source tracked per-field so tray can display provenance.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Optional

REG_PATH = r"SOFTWARE\Halbot\Config"

DEFAULTS: Dict[str, Any] = {
    "log_level": "INFO",
}


class Source(str, Enum):
    DEFAULT = "DEFAULT"
    REGISTRY = "REGISTRY"
    RUNTIME_OVERRIDE = "RUNTIME_OVERRIDE"


@dataclass
class _Store:
    registry: Dict[str, Any] = field(default_factory=dict)
    overrides: Dict[str, Any] = field(default_factory=dict)


_lock = threading.RLock()
_store = _Store()
_listeners: list = []


def _winreg():
    import winreg  # noqa: F401  (Windows-only)
    return winreg


def _read_registry() -> Dict[str, Any]:
    try:
        wr = _winreg()
    except ImportError:
        return {}
    out: Dict[str, Any] = {}
    try:
        with wr.OpenKey(wr.HKEY_LOCAL_MACHINE, REG_PATH, 0, wr.KEY_READ) as k:
            i = 0
            while True:
                try:
                    name, value, _ = wr.EnumValue(k, i)
                except OSError:
                    break
                if name in DEFAULTS:
                    out[name] = value
                i += 1
    except FileNotFoundError:
        pass
    except OSError:
        pass
    return out


def _write_registry(values: Dict[str, Any]) -> None:
    wr = _winreg()
    with wr.CreateKeyEx(wr.HKEY_LOCAL_MACHINE, REG_PATH, 0, wr.KEY_SET_VALUE) as k:
        for name, val in values.items():
            wr.SetValueEx(k, name, 0, wr.REG_SZ, str(val))


def load() -> None:
    """Populate registry layer from disk. Called at daemon startup."""
    with _lock:
        _store.registry = _read_registry()


def subscribe(fn) -> None:
    """Register callback fired after every change. fn(field_name, new_value)."""
    _listeners.append(fn)


def _notify(field_name: str, value: Any) -> None:
    for fn in list(_listeners):
        try:
            fn(field_name, value)
        except Exception:
            pass


def get(name: str) -> Any:
    with _lock:
        if name in _store.overrides:
            return _store.overrides[name]
        if name in _store.registry:
            return _store.registry[name]
        return DEFAULTS[name]


def source_of(name: str) -> Source:
    with _lock:
        if name in _store.overrides:
            return Source.RUNTIME_OVERRIDE
        if name in _store.registry:
            return Source.REGISTRY
        return Source.DEFAULT


def snapshot() -> Dict[str, tuple]:
    """{name: (value, Source)}."""
    return {n: (get(n), source_of(n)) for n in DEFAULTS}


def update(values: Dict[str, Any]) -> None:
    """Runtime override. Notifies listeners for changed fields."""
    with _lock:
        changed = []
        for n, v in values.items():
            if n not in DEFAULTS:
                continue
            old = get(n)
            _store.overrides[n] = v
            if v != old:
                changed.append((n, v))
    for n, v in changed:
        _notify(n, v)


def persist(fields: Optional[Iterable[str]] = None) -> None:
    """Write runtime overrides (or subset) to registry; clears override."""
    with _lock:
        names = list(fields) if fields else list(_store.overrides.keys())
        to_write = {n: _store.overrides[n] for n in names if n in _store.overrides}
        if to_write:
            _write_registry(to_write)
            for n, v in to_write.items():
                _store.registry[n] = v
                _store.overrides.pop(n, None)


def reset(fields: Optional[Iterable[str]] = None) -> None:
    """Drop runtime overrides. Values revert to registry/default."""
    with _lock:
        names = list(fields) if fields else list(_store.overrides.keys())
        changed = []
        for n in names:
            if n in _store.overrides:
                _store.overrides.pop(n, None)
                changed.append((n, get(n)))
    for n, v in changed:
        _notify(n, v)
