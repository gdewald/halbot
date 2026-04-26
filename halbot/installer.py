"""Windows installer: NSSM service, HKLM registry, ProgramData ACLs.

Run elevated. No per-user Run key this phase.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

SERVICE_NAME = "halbot"
REG_KEY = r"SOFTWARE\Halbot\Config"
SECRETS_KEY = r"SOFTWARE\Halbot\Secrets"


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _install_root() -> Path:
    """Resolve `%ProgramFiles%\\Halbot\\` from the env."""
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    return Path(program_files) / "Halbot"


def _service_exe() -> Path:
    """Path to the venv's python.exe NSSM points at."""
    return _install_root() / ".venv" / "Scripts" / "python.exe"


def _service_workdir() -> Path:
    """`AppDirectory` for NSSM (so `python -m halbot.daemon` resolves)."""
    return _install_root() / "src"


def _current_user() -> str:
    return os.environ.get("USERNAME", "")


def _run(cmd: list, check: bool = True) -> subprocess.CompletedProcess:
    log.info("run: %s", " ".join(cmd))
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _find_nssm() -> str:
    exe = shutil.which("nssm") or shutil.which("nssm.exe")
    if exe:
        return exe
    # `install.ps1` drops nssm.exe at the install root; the daemon process
    # may be running from anywhere (dev or installed). Look in both.
    for candidate in (_install_root() / "nssm.exe", Path(sys.executable).parent / "nssm.exe"):
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("nssm.exe not found on PATH or under %ProgramFiles%\\Halbot\\")


def _create_data_dirs() -> None:
    paths.log_dir()


def _grant_registry(user: str, subkey: str) -> None:
    """Grant user KEY_WRITE on HKLM\\<subkey>."""
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, subkey, 0, winreg.KEY_ALL_ACCESS):
        pass
    try:
        import winreg
        import win32api
        import win32security
        KEY_ALL_ACCESS = winreg.KEY_ALL_ACCESS
        KEY_READ = winreg.KEY_READ
        KEY_WRITE = winreg.KEY_WRITE
        key = win32api.RegOpenKeyEx(
            0x80000002,  # HKEY_LOCAL_MACHINE
            subkey, 0, KEY_ALL_ACCESS,
        )
        sd = win32api.RegGetKeySecurity(key, win32security.DACL_SECURITY_INFORMATION)
        dacl = sd.GetSecurityDescriptorDacl()
        sid, _, _ = win32security.LookupAccountName(None, user)
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, KEY_WRITE | KEY_READ, sid
        )
        sd.SetSecurityDescriptorDacl(1, dacl, 0)
        win32api.RegSetKeySecurity(key, win32security.DACL_SECURITY_INFORMATION, sd)
        win32api.RegCloseKey(key)
    except Exception as e:
        log.warning("registry ACL grant for %s skipped: %s", subkey, e)


def _grant_service_control(user: str) -> None:
    """Grant user start/stop/query on service via sc sdset."""
    # Build SDDL: default + allow user RPWP (start/stop) + RC (query).
    sid = subprocess.run(
        ["powershell", "-Command",
         f"(New-Object System.Security.Principal.NTAccount('{user}')).Translate([System.Security.Principal.SecurityIdentifier]).Value"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    # Get current SDDL
    cur = _run(["sc", "sdshow", SERVICE_NAME]).stdout.strip()
    # Insert user ACE granting RPWPDTRC
    ace = f"(A;;RPWPDTRC;;;{sid})"
    if ace not in cur:
        # Insert before first S: or at end of D: section.
        if "S:" in cur:
            new = cur.replace("S:", ace + "S:", 1)
        else:
            new = cur + ace
        _run(["sc", "sdset", SERVICE_NAME, new])


def _grant_programdata(user: str) -> None:
    data = str(paths.data_dir())
    _run(["icacls", data, "/grant", f"{user}:(OI)(CI)M"], check=False)


def install() -> int:
    if not _is_admin():
        print("setup --install requires elevated shell", file=sys.stderr)
        return 1

    py_exe = _service_exe()
    workdir = _service_workdir()
    if not py_exe.exists():
        raise RuntimeError(f"venv python missing: {py_exe} -- run install.ps1 first")
    if not workdir.exists():
        raise RuntimeError(f"src dir missing: {workdir} -- run install.ps1 first")
    nssm = _find_nssm()
    user = _current_user()

    _create_data_dirs()
    _grant_programdata(user)

    # NSSM service create. `python.exe -m halbot.daemon run` from src/.
    _run([nssm, "install", SERVICE_NAME, str(py_exe), "-m", "halbot.daemon", "run"])
    _run([nssm, "set", SERVICE_NAME, "AppDirectory", str(workdir)])
    _run([nssm, "set", SERVICE_NAME, "AppThrottle", "1500"])
    _run([nssm, "set", SERVICE_NAME, "AppRestartDelay", "30000"])
    _run([nssm, "set", SERVICE_NAME, "AppExit", "Default", "Restart"])
    log_path = str(paths.log_dir() / "halbot-service.log")
    _run([nssm, "set", SERVICE_NAME, "AppStdout", log_path])
    _run([nssm, "set", SERVICE_NAME, "AppStderr", log_path])
    _run([nssm, "set", SERVICE_NAME, "Start", "SERVICE_AUTO_START"])

    _grant_registry(user, REG_KEY)
    _grant_registry(user, SECRETS_KEY)
    if user:
        try:
            _grant_service_control(user)
        except Exception as e:
            log.warning("service ACL grant skipped: %s", e)

    _run([nssm, "start", SERVICE_NAME], check=False)
    print(f"installed service '{SERVICE_NAME}' and granted '{user}'")
    return 0


def uninstall() -> int:
    if not _is_admin():
        print("setup --uninstall requires elevated shell", file=sys.stderr)
        return 1
    try:
        nssm = _find_nssm()
    except RuntimeError:
        nssm = None

    if nssm:
        _run([nssm, "stop", SERVICE_NAME], check=False)
        _run([nssm, "remove", SERVICE_NAME, "confirm"], check=False)
    else:
        _run(["sc", "stop", SERVICE_NAME], check=False)
        _run(["sc", "delete", SERVICE_NAME], check=False)

    # Delete registry key tree (Config + Secrets).
    try:
        import winreg
        _delete_key_tree(winreg.HKEY_LOCAL_MACHINE, REG_KEY)
        _delete_key_tree(winreg.HKEY_LOCAL_MACHINE, SECRETS_KEY)
        _delete_key_tree(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Halbot")
    except Exception as e:
        log.warning("registry delete skipped: %s", e)

    # Remove ProgramData\Halbot.
    data = paths.data_dir()
    try:
        shutil.rmtree(data, ignore_errors=True)
    except Exception as e:
        log.warning("data dir delete skipped: %s", e)

    print("uninstalled")
    return 0


def _delete_key_tree(root, sub) -> None:
    import winreg
    try:
        with winreg.OpenKey(root, sub, 0, winreg.KEY_READ) as k:
            while True:
                try:
                    child = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_key_tree(root, sub + "\\" + child)
    except FileNotFoundError:
        return
    try:
        winreg.DeleteKey(root, sub)
    except FileNotFoundError:
        pass
