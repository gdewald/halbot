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
    "llm_backend": "ollama",
    "llm_url": "http://localhost:11434/v1/chat/completions",
    "llm_model": "",
    "llm_max_tokens_text": "512",
    "llm_max_tokens_voice": "256",
    "voice_wake_word": "halbot",
    "voice_idle_timeout_seconds": "1800",
    "voice_energy_threshold": "0.02",
    "voice_history_turns": "10",
    "voice_llm_combine_calls": "true",
    "tts_engine": "kokoro",
    "tts_voice": "af_heart",
    "tts_lang": "a",
    "tts_speed": "1.0",
    "analytics_retention_days": "90",
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


# Field schema drives Config panel widgets over the wire.
# Keys must mirror DEFAULTS exactly. Missing key = hidden in UI.
SCHEMA: Dict[str, Dict[str, Any]] = {
    "log_level": {
        "type": "SELECT", "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
        "description": "Minimum log level emitted",
        "group": "general", "label": "LOG_LEVEL",
    },
    "llm_backend": {
        "type": "SELECT", "options": ["ollama", "lmstudio"],
        "description": "LLM backend driver",
        "group": "llm", "label": "LLM_BACKEND",
    },
    "llm_url": {
        "type": "URL",
        "description": "LLM API endpoint",
        "group": "llm", "label": "LLM_URL",
    },
    "llm_model": {
        "type": "STRING",
        "description": "Model name as shown by the backend",
        "group": "llm", "label": "LLM_MODEL",
    },
    "llm_max_tokens_text": {
        "type": "NUMBER", "min": 16.0, "max": 4096.0, "step": 16.0,
        "description": "Max output tokens for text LLM calls",
        "group": "llm", "label": "LLM_MAX_TOKENS_TEXT",
    },
    "llm_max_tokens_voice": {
        "type": "NUMBER", "min": 16.0, "max": 2048.0, "step": 16.0,
        "description": "Max output tokens for voice LLM calls",
        "group": "llm", "label": "LLM_MAX_TOKENS_VOICE",
    },
    "voice_wake_word": {
        "type": "STRING",
        "description": "Wake word phrase",
        "group": "voice", "label": "VOICE_WAKE_WORD",
    },
    "voice_idle_timeout_seconds": {
        "type": "NUMBER", "min": 30.0, "max": 7200.0, "step": 30.0,
        "description": "Seconds before bot leaves empty channel",
        "group": "voice", "label": "VOICE_IDLE_TIMEOUT_SECONDS",
    },
    "voice_energy_threshold": {
        "type": "RANGE", "min": 0.0, "max": 0.2, "step": 0.005,
        "description": "Voice activity detection threshold",
        "group": "voice", "label": "VOICE_ENERGY_THRESHOLD",
    },
    "voice_history_turns": {
        "type": "NUMBER", "min": 0.0, "max": 50.0, "step": 1.0,
        "description": "Conversation turns kept in voice LLM context",
        "group": "voice", "label": "VOICE_HISTORY_TURNS",
    },
    "voice_llm_combine_calls": {
        "type": "BOOL",
        "description": "Batch voice LLM calls for lower latency",
        "group": "voice", "label": "VOICE_LLM_COMBINE_CALLS",
    },
    "tts_engine": {
        "type": "SELECT", "options": ["kokoro", "espeak", "piper"],
        "description": "TTS backend",
        "group": "tts", "label": "TTS_ENGINE",
    },
    "tts_voice": {
        "type": "STRING",
        "description": "TTS voice ID",
        "group": "tts", "label": "TTS_VOICE",
    },
    "tts_lang": {
        "type": "STRING",
        "description": "TTS language code",
        "group": "tts", "label": "TTS_LANG",
    },
    "tts_speed": {
        "type": "RANGE", "min": 0.5, "max": 2.0, "step": 0.05,
        "description": "TTS playback speed multiplier",
        "group": "tts", "label": "TTS_SPEED",
    },
    "analytics_retention_days": {
        "type": "NUMBER", "min": 1.0, "max": 3650.0, "step": 1.0,
        "description": "Days of analytics history retained on disk",
        "group": "general", "label": "ANALYTICS_RETENTION_DAYS",
    },
}
