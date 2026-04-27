"""Fixtures for the dashboard Playwright suite.

Spawns `python -m dashboard.app --dev` once per session on a free localhost
port, probes whether the daemon is also up, and yields {base_url, live} to
the tests. Tests marked @pytest.mark.live skip when the daemon is down.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_INDEX = REPO_ROOT / "frontend" / "dist" / "index.html"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, deadline_s: float = 15.0) -> None:
    end = time.time() + deadline_s
    last_err: Exception | None = None
    while time.time() < end:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return
        except Exception as e:
            last_err = e
        time.sleep(0.2)
    raise RuntimeError(f"dashboard dev server did not come up at {url}: {last_err}")


@pytest.fixture(scope="session")
def dashboard() -> Iterator[dict]:
    if not DIST_INDEX.exists():
        pytest.skip(f"frontend/dist/index.html missing — run `npm --prefix frontend run build`")

    port = _free_port()
    env = {**os.environ, "HALBOT_DASHBOARD_DEV": "1", "HALBOT_DASHBOARD_DEV_PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "dashboard.app"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(REPO_ROOT),
        text=True,
    )

    base_url = f"http://127.0.0.1:{port}"
    try:
        try:
            _wait_for_http(f"{base_url}/")
        except Exception:
            proc.terminate()
            out, _ = proc.communicate(timeout=5)
            raise RuntimeError(f"dashboard server failed to start. Output:\n{out}")

        # Probe daemon liveness via the HTTP bridge.
        live = False
        try:
            r = httpx.post(f"{base_url}/api/health", json={"args": []}, timeout=3.0)
            live = r.status_code == 200 and int(r.json().get("pid") or 0) > 0
        except Exception:
            live = False

        yield {"base_url": base_url, "live": live, "port": port}
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)
        if proc.poll() is None:
            proc.kill()


@pytest.fixture(autouse=True)
def _skip_live_when_dead(request: pytest.FixtureRequest, dashboard: dict) -> None:
    if "live" in request.keywords and not dashboard["live"]:
        pytest.skip("daemon not running — live test skipped")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: requires the halbot daemon to be running")
