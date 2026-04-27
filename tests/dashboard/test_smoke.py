"""Per-panel render smoke tests.

For each nav entry, click it and assert the panel renders without raising
console errors. Stays transport-agnostic: works against live daemon (real
bridge data) or daemon-down (fetch errors are caught, panels render empty
states).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

PANELS = [
    ("Logs", re.compile(r"level|log|level filter|trace|debug|info", re.IGNORECASE)),
    ("Daemon", re.compile(r"service|halbot|version", re.IGNORECASE)),
    ("Stats", re.compile(r"soundboard|wake|tts|stt|llm|playback", re.IGNORECASE)),
    ("Analytics", re.compile(r"analytics|events|users|commands|kind", re.IGNORECASE)),
    ("Emojis", re.compile(r"emoji", re.IGNORECASE)),
    ("Config", re.compile(r"log_level|llm|voice|tts", re.IGNORECASE)),
]


@pytest.mark.parametrize("label,expected_text", PANELS)
def test_panel_renders(page: Page, dashboard: dict, label: str, expected_text: re.Pattern) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(dashboard["base_url"], wait_until="domcontentloaded")

    nav_button = page.get_by_role("button", name=label, exact=True)
    expect(nav_button).to_be_visible(timeout=10_000)
    nav_button.click()

    # Logs is always-mounted (hidden via display:none when inactive) so the
    # log rows still match any text query. Filter to visible matches only.
    expect(page.get_by_text(expected_text).locator("visible=true").first).to_be_visible(timeout=8_000)
    page.wait_for_timeout(400)

    fatal = [e for e in errors if "404" not in e and "500" not in e]
    assert not fatal, f"console errors on {label}: {fatal}"


def test_root_loads(page: Page, dashboard: dict) -> None:
    page.goto(dashboard["base_url"], wait_until="domcontentloaded")
    expect(page).to_have_title(re.compile("halbot", re.IGNORECASE))
    expect(page.get_by_role("button", name="Logs", exact=True)).to_be_visible(timeout=10_000)


def test_health_endpoint_round_trip(dashboard: dict) -> None:
    """Exercises the HTTP bridge directly (no browser) — confirms JsApi wiring."""
    import httpx
    r = httpx.post(f"{dashboard['base_url']}/api/health", json={"args": []}, timeout=5.0)
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("uptime_seconds", "daemon_version", "llm_reachable", "pid"):
        assert key in body, f"missing key {key} in /api/health response"


def test_unknown_method_returns_404(dashboard: dict) -> None:
    import httpx
    r = httpx.post(f"{dashboard['base_url']}/api/no_such_method", json={"args": []}, timeout=5.0)
    assert r.status_code == 404


def test_dunder_method_blocked(dashboard: dict) -> None:
    import httpx
    r = httpx.post(f"{dashboard['base_url']}/api/__init__", json={"args": []}, timeout=5.0)
    assert r.status_code == 404
