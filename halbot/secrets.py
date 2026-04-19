"""DPAPI-encrypted secret storage under HKLM\\SOFTWARE\\Halbot\\Secrets.

LocalMachine scope — any process on host can decrypt. Fit for single-host
toy; not a defense against co-resident attackers. Design per 002.

Only key in use this phase: DISCORD_TOKEN.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

REG_KEY = r"SOFTWARE\Halbot\Secrets"

# win32crypt flag
CRYPTPROTECT_LOCAL_MACHINE = 0x04


def _winreg():
    import winreg
    return winreg


def _win32crypt():
    import win32crypt
    return win32crypt


def _encrypt(plaintext: str) -> bytes:
    crypt = _win32crypt()
    blob = crypt.CryptProtectData(
        plaintext.encode("utf-8"),
        "halbot",
        None,
        None,
        None,
        CRYPTPROTECT_LOCAL_MACHINE,
    )
    return bytes(blob)


def _decrypt(blob: bytes) -> str:
    crypt = _win32crypt()
    _desc, data = crypt.CryptUnprotectData(
        blob, None, None, None, CRYPTPROTECT_LOCAL_MACHINE
    )
    return bytes(data).decode("utf-8")


def set_secret(name: str, value: str) -> None:
    """Encrypt `value` via DPAPI (LocalMachine) and persist at HKLM\\...\\Secrets\\<name>."""
    wr = _winreg()
    blob = _encrypt(value)
    with wr.CreateKeyEx(wr.HKEY_LOCAL_MACHINE, REG_KEY, 0, wr.KEY_SET_VALUE) as k:
        wr.SetValueEx(k, name, 0, wr.REG_BINARY, blob)


def get_secret(name: str) -> Optional[str]:
    """Read + decrypt. Returns None if key/value missing or decrypt fails."""
    wr = _winreg()
    try:
        with wr.OpenKey(wr.HKEY_LOCAL_MACHINE, REG_KEY, 0, wr.KEY_READ) as k:
            blob, regtype = wr.QueryValueEx(k, name)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if regtype != wr.REG_BINARY or not blob:
        return None
    try:
        return _decrypt(bytes(blob))
    except Exception:
        log.exception("decrypt failed for %s", name)
        return None


def delete_secret(name: str) -> bool:
    wr = _winreg()
    try:
        with wr.OpenKey(wr.HKEY_LOCAL_MACHINE, REG_KEY, 0, wr.KEY_SET_VALUE) as k:
            wr.DeleteValue(k, name)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def has_secret(name: str) -> bool:
    return get_secret(name) is not None
