"""Stats panel — verifies the post-fix card inventory.

Architecture-incompatible cards were dropped (TTFT, TTS first chunk,
TTS engine fallback, wake join latency). Implementable cards were wired
to real data (STT chunk decode, STT avg utterance length, LLM tokens
out / throughput / context % / timeouts, voice session seconds).

These tests assert the resulting card layout regardless of whether the
daemon is up: the labels are static React, present even when stats
fetches fail.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect


DROPPED_LABELS = [
    "Time to first token",
    "First audio chunk",
    "Engine fallback",
    "Avg join latency",
]

WIRED_LABELS = [
    "Chunk decode time",
    "Avg utterance len",
    "Avg tokens out",
    "Context usage",
    "Timeouts today",
    "Throughput",
    "Session time today",
]


@pytest.fixture
def stats_panel(page: Page, dashboard: dict) -> Page:
    page.goto(dashboard["base_url"], wait_until="domcontentloaded")
    page.get_by_role("button", name="Stats", exact=True).click()
    expect(page.get_by_text(re.compile(r"Speech-to-Text", re.IGNORECASE))).to_be_visible(
        timeout=8_000
    )
    return page


@pytest.mark.parametrize("label", DROPPED_LABELS)
def test_dropped_cards_absent(stats_panel: Page, label: str) -> None:
    """Cards we removed should NOT be in the DOM."""
    assert stats_panel.get_by_text(label, exact=False).count() == 0, (
        f"{label!r} card is still rendered — should have been dropped"
    )


@pytest.mark.parametrize("label", WIRED_LABELS)
def test_wired_cards_present(stats_panel: Page, label: str) -> None:
    """Cards we wired should render their static label."""
    expect(stats_panel.get_by_text(label, exact=False).first).to_be_visible()


def test_missing_data_drawer_hidden(stats_panel: Page) -> None:
    """Drawer auto-hides when GROUPS is empty (post-fix state)."""
    assert stats_panel.get_by_text("Missing data", exact=True).count() == 0


@pytest.mark.live
def test_get_stats_shape(dashboard: dict) -> None:
    """Live daemon returns the new STT chunk/audio + LLM token fields."""
    import httpx

    r = httpx.post(
        f"{dashboard['base_url']}/api/get_stats", json={"args": []}, timeout=5.0
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "stt" in body and "llm" in body
    for key in ("chunk_avg_ms", "chunk_p95_ms", "avg_audio_seconds"):
        assert key in body["stt"], f"stt.{key} missing from get_stats reply"
    for key in ("tokens_per_sec", "avg_tokens_out", "context_usage_pct", "timeouts_today"):
        assert key in body["llm"], f"llm.{key} missing from get_stats reply"
    # Dropped fields should not appear.
    assert "ttft_avg_ms" not in body["llm"], "llm.ttft_avg_ms should be removed"
    assert "avg_join_latency_ms" not in body["wake_word"], (
        "wake_word.avg_join_latency_ms should be removed"
    )
