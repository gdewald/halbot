"""Snapshot-mode (`/halbot-stats` published page) parity with the dashboard.

The published static page is the same React build as the tray dashboard
but is gated by `window.__STATS_SNAPSHOT__` set in the served HTML
before the bundle loads. Same Stats panel renders against pre-baked
data instead of live RPCs. After the dashboard cleanup these checks
must hold for the snapshot too:

  - Dropped cards (TTFT, first audio chunk, engine fallback, wake join
    latency) are absent.
  - Wired cards (chunk decode, avg utterance len, LLM tokens / context /
    timeouts, voice session-seconds) render real numbers from the
    snapshot payload.
  - WakeHistory transcript view is gone.

Tests use Playwright's `add_init_script` to set the snapshot global
before navigation, so no R2 / boto3 / Discord client is involved.
"""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, expect


SNAPSHOT_PAYLOAD = {
    "schema_version": 1,
    "generated_at_utc": "2026-04-27T00:00:00Z",
    "window_seconds": 30 * 86400,
    "stats": {
        "soundboard": {
            "sounds_backed_up": 12, "storage_bytes": 1_048_576,
            "last_sync_unix": 1_745_000_000, "new_since_last": 1,
        },
        "voice_playback": {
            "played_today": 5, "played_all_time": 120,
            "session_seconds_today": 4200, "avg_response_ms": 0,
        },
        "wake_word": {
            "detections_today": 3, "detections_all_time": 99,
            "false_positives_today": 1,
        },
        "stt": {
            "avg_ms": 210, "p95_ms": 480, "count_today": 14,
            "chunk_avg_ms": 85, "chunk_p95_ms": 140,
            "avg_audio_seconds": 3.2,
        },
        "tts": {"avg_ms": 340, "p95_ms": 680, "count_today": 7},
        "llm": {
            "response_avg_ms": 1240, "response_p95_ms": 4800,
            "tokens_per_sec": 38, "requests_today": 22,
            "avg_tokens_out": 184, "context_usage_pct": 62,
            "timeouts_today": 2,
        },
        "mock": False,
    },
    "analytics": {
        "top_sounds": {"total_count": 0, "rows": []},
        "top_users": {"total_count": 0, "rows": []},
        "top_commands": {"total_count": 0, "rows": []},
        "kind_mix": {"total_count": 0, "rows": []},
    },
    "soundboard": [],
    "emoji": [],
}


@pytest.fixture
def snapshot_page(page: Page, dashboard: dict) -> Page:
    """Loads the dashboard with __STATS_SNAPSHOT__ pre-set in the page."""
    payload_js = json.dumps(SNAPSHOT_PAYLOAD)
    page.add_init_script(f"window.__STATS_SNAPSHOT__ = {payload_js};")
    page.goto(dashboard["base_url"], wait_until="domcontentloaded")
    page.get_by_role("button", name="Stats", exact=True).click()
    expect(page.get_by_text(re.compile(r"Speech-to-Text", re.IGNORECASE))).to_be_visible(
        timeout=8_000
    )
    return page


DROPPED_LABELS = [
    "Time to first token",
    "First audio chunk",
    "Engine fallback",
    "Avg join latency",
]


@pytest.mark.parametrize("label", DROPPED_LABELS)
def test_snapshot_dropped_cards_absent(snapshot_page: Page, label: str) -> None:
    assert snapshot_page.get_by_text(label, exact=False).count() == 0, (
        f"{label!r} should be gone from the published snapshot too"
    )


def test_snapshot_wake_history_absent(snapshot_page: Page) -> None:
    """Transcript view does not render in snapshot mode."""
    # The old WakeHistory rendered a table with a "Phrase" header.
    assert snapshot_page.get_by_text("Phrase", exact=True).count() == 0


def test_snapshot_renders_real_numbers(snapshot_page: Page) -> None:
    """Hard-coded snapshot values reach the DOM via the React panel."""
    # STT chunk decode 85ms avg
    expect(
        snapshot_page.get_by_text(re.compile(r"\b85\b")).first
    ).to_be_visible()
    # Avg utterance length 3.2s — formatted via toFixed(1)
    expect(snapshot_page.get_by_text("3.2").first).to_be_visible()
    # LLM avg tokens out 184
    expect(snapshot_page.get_by_text("184").first).to_be_visible()
    # Throughput tok/s = 38
    expect(snapshot_page.get_by_text("38").first).to_be_visible()
    # Context usage 62%
    expect(snapshot_page.get_by_text("62").first).to_be_visible()


def test_snapshot_missing_data_drawer_hidden(snapshot_page: Page) -> None:
    assert snapshot_page.get_by_text("Missing data", exact=True).count() == 0


def test_snapshot_health_banner_static_label(snapshot_page: Page) -> None:
    """Snapshot mode swaps health for a `static snapshot` banner string."""
    expect(
        snapshot_page.get_by_text(re.compile(r"static snapshot", re.IGNORECASE)).first
    ).to_be_visible()
