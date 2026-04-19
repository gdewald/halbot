"""Service Start/Stop/Restart with minimal-rights open.

Default `win32serviceutil.StopService` opens with SERVICE_ALL_ACCESS,
which non-admin users lack even when granted STOP/START via sc sdset.
Open explicitly with the minimum rights we were granted.
"""

from __future__ import annotations

import logging
import time

import win32service

log = logging.getLogger(__name__)

SERVICE_NAME = "halbot"

_ACCESS = (
    win32service.SERVICE_START
    | win32service.SERVICE_STOP
    | win32service.SERVICE_QUERY_STATUS
)


def _open():
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        svc = win32service.OpenService(scm, SERVICE_NAME, _ACCESS)
    except Exception:
        win32service.CloseServiceHandle(scm)
        raise
    return scm, svc


def _close(scm, svc) -> None:
    try:
        win32service.CloseServiceHandle(svc)
    finally:
        win32service.CloseServiceHandle(scm)


def _wait_state(svc, target: int, timeout: float = 15.0) -> int:
    end = time.time() + timeout
    last = 0
    while time.time() < end:
        status = win32service.QueryServiceStatus(svc)
        last = status[1]
        if last == target:
            return last
        time.sleep(0.3)
    return last


def start() -> None:
    scm, svc = _open()
    try:
        win32service.StartService(svc, None)
        _wait_state(svc, win32service.SERVICE_RUNNING)
    finally:
        _close(scm, svc)


def stop() -> None:
    scm, svc = _open()
    try:
        win32service.ControlService(svc, win32service.SERVICE_CONTROL_STOP)
        _wait_state(svc, win32service.SERVICE_STOPPED)
    finally:
        _close(scm, svc)


def restart() -> None:
    stop()
    time.sleep(0.5)
    start()


def status() -> str:
    try:
        scm, svc = _open()
    except Exception as e:
        return f"unknown ({e})"
    try:
        state = win32service.QueryServiceStatus(svc)[1]
    finally:
        _close(scm, svc)
    return {
        1: "stopped",
        2: "start-pending",
        3: "stop-pending",
        4: "running",
        5: "continue-pending",
        6: "pause-pending",
        7: "paused",
    }.get(state, str(state))


def query() -> dict:
    """Return {'state': str, 'pid': int}. pid=0 if not running."""
    try:
        scm, svc = _open()
    except Exception as e:
        return {"state": f"unknown ({e})", "pid": 0}
    try:
        info = win32service.QueryServiceStatusEx(svc)
        state_code = info["CurrentState"]
        pid = int(info.get("ProcessId", 0) or 0)
    finally:
        _close(scm, svc)
    name = {
        1: "stopped", 2: "start-pending", 3: "stop-pending",
        4: "running", 5: "continue-pending", 6: "pause-pending",
        7: "paused",
    }.get(state_code, str(state_code))
    return {"state": name, "pid": pid}
