"""Pure-function coverage for halbot.stats_publisher.

Stdlib unittest — no pytest dep. Run via:
    uv run python -m unittest tests.test_stats_publisher

Covers: HTML injection escaping, head-tag insertion point, user-label
fallback when the Discord client returns nothing, treatment-mode dispatch,
and publish_now's in-process throttle (with the publisher + dist tree
swapped for fakes).
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Dict
from unittest import mock

from halbot import stats_publisher


class JsStringLiteralTests(unittest.TestCase):
    def test_round_trips_basic_payload(self):
        payload = '{"hello":"world"}'
        literal = stats_publisher._js_string_literal(payload)
        # Eval as JSON (since json.dumps -> JSON string is a valid JS string literal)
        self.assertEqual(json.loads(literal), payload)

    def test_escapes_close_script(self):
        payload = 'oops</script><img>'
        literal = stats_publisher._js_string_literal(payload)
        self.assertNotIn("</script>", literal)
        self.assertIn("<\\/script>", literal)
        # Round-trips back to original after JSON.parse + the </ escape
        # (the escape is a no-op for the JS-string-literal level since
        # JS treats <\/ identically to </ once parsed).
        decoded = json.loads(literal).replace("<\\/", "</")
        self.assertEqual(decoded, payload)

    def test_escapes_line_separators(self):
        payload = "line\u2028break\u2029"
        literal = stats_publisher._js_string_literal(payload)
        self.assertNotIn("\u2028", literal)
        self.assertNotIn("\u2029", literal)
        self.assertIn("\\u2028", literal)
        self.assertIn("\\u2029", literal)


class RenderSnapshotHtmlTests(unittest.TestCase):
    def test_injects_before_head_close(self):
        html = "<html><head><title>x</title></head><body>hi</body></html>"
        out = stats_publisher.render_snapshot_html(html, {"schema_version": 1})
        self.assertIn("window.__STATS_SNAPSHOT__", out)
        # Injection sits before </head>, after <title>
        head_close = out.find("</head>")
        snap_idx = out.find("window.__STATS_SNAPSHOT__")
        self.assertLess(snap_idx, head_close)
        self.assertGreater(snap_idx, out.find("<title>"))

    def test_injection_payload_round_trips(self):
        snap = {"schema_version": 1, "data": "</script><h1>x"}
        html = "<head></head>"
        out = stats_publisher.render_snapshot_html(html, snap)
        # Extract the JSON.parse(<literal>) content and decode.
        marker = "JSON.parse("
        i = out.find(marker) + len(marker)
        j = out.find(")", i)
        literal = out[i:j]
        # JS-string-literal -> JSON string. Reverse the </ escape first.
        json_str = json.loads(literal).replace("<\\/", "</")
        self.assertEqual(json.loads(json_str), snap)

    def test_no_head_tag_falls_back_to_prepend(self):
        html = "<body>raw</body>"
        out = stats_publisher.render_snapshot_html(html, {"x": 1})
        self.assertTrue(out.startswith("<script>"))
        self.assertIn("<body>raw</body>", out)


class UserLabelTests(unittest.TestCase):
    def test_returns_empty_for_zero(self):
        self.assertEqual(stats_publisher._user_label(SimpleNamespace(), 0, {}), "")

    def test_falls_back_to_short_id_when_no_client(self):
        client = SimpleNamespace(get_user=lambda _i: None, guilds=[])
        cache: dict = {}
        label = stats_publisher._user_label(client, 1234567890, cache)
        self.assertEqual(label, "user_7890")
        # Cache populated
        self.assertEqual(cache[1234567890], "user_7890")

    def test_uses_display_name_from_get_user(self):
        client = SimpleNamespace(
            get_user=lambda _i: SimpleNamespace(display_name="Alice"),
            guilds=[],
        )
        self.assertEqual(stats_publisher._user_label(client, 42, {}), "Alice")

    def test_walks_guild_members_when_get_user_misses(self):
        member = SimpleNamespace(display_name="Bob")
        guild = SimpleNamespace(get_member=lambda i: member if i == 99 else None)
        client = SimpleNamespace(get_user=lambda _i: None, guilds=[guild])
        self.assertEqual(stats_publisher._user_label(client, 99, {}), "Bob")


class TreatUserRowsTests(unittest.TestCase):
    def _client(self):
        return SimpleNamespace(
            get_user=lambda i: SimpleNamespace(display_name=f"name{i}"),
            guilds=[],
        )

    def _rows(self):
        return [{"key": "111", "count": 5}, {"key": "222", "count": 3}]

    def test_display_name_default(self):
        with mock.patch.object(stats_publisher.config, "get", return_value="display_name"):
            out = stats_publisher._treat_user_rows(self._client(), self._rows(), {})
        self.assertEqual([r["key"] for r in out], ["name111", "name222"])

    def test_raw_passthrough(self):
        with mock.patch.object(stats_publisher.config, "get", return_value="raw"):
            out = stats_publisher._treat_user_rows(self._client(), self._rows(), {})
        self.assertEqual([r["key"] for r in out], ["111", "222"])

    def test_omit_blanks_key(self):
        with mock.patch.object(stats_publisher.config, "get", return_value="omit"):
            out = stats_publisher._treat_user_rows(self._client(), self._rows(), {})
        self.assertEqual([r["key"] for r in out], ["", ""])

    def test_hash_uses_short_form(self):
        with mock.patch.object(stats_publisher.config, "get", return_value="hash"):
            out = stats_publisher._treat_user_rows(self._client(), self._rows(), {})
        self.assertEqual([r["key"] for r in out], ["u#0111", "u#0222"])


class PublishNowThrottleTests(unittest.TestCase):
    def setUp(self):
        # Reset module-level throttle state between tests.
        stats_publisher._last_result = None
        stats_publisher._last_publish_ts = 0.0

    def _patches(self, dist_root: Path):
        fake_publisher = mock.Mock()
        fake_publisher.publish.return_value = "https://example.test/index.html"

        def cfg_get(key, default=None):
            return {
                "stats_min_publish_interval_seconds": "60",
                "stats_publisher": "s3",
                "stats_user_id_treatment": "raw",
            }.get(key, default)

        return [
            mock.patch.object(stats_publisher.config, "get", side_effect=cfg_get),
            mock.patch.object(stats_publisher, "get_publisher", return_value=fake_publisher),
            mock.patch.object(stats_publisher.paths, "frontend_dist_dir", return_value=dist_root),
            mock.patch.object(stats_publisher, "snapshot_stats",
                              return_value={"schema_version": 1, "generated_at_utc": "2026-04-26T00:00:00Z"}),
            mock.patch.object(stats_publisher.analytics, "record"),
        ], fake_publisher

    def test_second_call_within_window_returns_cached(self):
        with mock.patch.object(stats_publisher, "tempfile") as tf_mod:
            # Build a tiny "dist" tree
            import tempfile as _tempfile
            real_root = Path(_tempfile.mkdtemp(prefix="halbot-stats-test-"))
            (real_root / "index.html").write_text("<head></head><body></body>")
            try:
                # tempfile.TemporaryDirectory returns a context yielding a path string
                ctx = mock.MagicMock()
                staging_parent = Path(_tempfile.mkdtemp(prefix="halbot-stats-stage-"))
                ctx.__enter__.return_value = str(staging_parent)
                ctx.__exit__.return_value = False
                tf_mod.TemporaryDirectory.return_value = ctx

                patches, pub = self._patches(real_root)
                for p in patches:
                    p.start()
                try:
                    r1 = stats_publisher.publish_now(client=SimpleNamespace())
                    r2 = stats_publisher.publish_now(client=SimpleNamespace())
                finally:
                    for p in patches:
                        p.stop()

                self.assertFalse(r1.cached)
                self.assertTrue(r2.cached)
                self.assertEqual(r1.url, r2.url)
                # Publisher invoked exactly once across both calls.
                self.assertEqual(pub.publish.call_count, 1)
            finally:
                import shutil as _sh
                _sh.rmtree(real_root, ignore_errors=True)
                _sh.rmtree(staging_parent, ignore_errors=True)


class SnapshotStatsShapeTests(unittest.TestCase):
    """Snapshot dict mirrors the dashboard `compute_dashboard_stats` shape.

    The published `/halbot-stats` page renders the same React Stats panel
    over `window.__STATS_SNAPSHOT__.stats`, so the new STT chunk decode +
    LLM tokens fields must flow through, the dropped wake-join-latency /
    TTFT keys must NOT appear, and the wake-history transcript view must
    be gone (per "transcript view in stats shouldn't exist").
    """

    def _patches(self, dashboard_stats: Dict[str, object]):
        return [
            mock.patch.object(stats_publisher, "_query",
                              return_value={"total_count": 0, "rows": []}),
            mock.patch.object(stats_publisher, "_treat_user_rows",
                              side_effect=lambda _c, rows, _cache: rows),
            mock.patch.object(stats_publisher, "_soundboard_table", return_value=[]),
            mock.patch.object(stats_publisher, "_emoji_table", return_value=[]),
            mock.patch.object(stats_publisher.analytics,
                              "compute_dashboard_stats",
                              return_value=dashboard_stats),
        ]

    def _stub_stats(self) -> Dict[str, object]:
        return {
            "soundboard": {"sounds_backed_up": 0, "storage_bytes": 0,
                           "last_sync_unix": 0, "new_since_last": 0},
            "voice_playback": {"played_today": 0, "played_all_time": 0,
                               "session_seconds_today": 600,
                               "avg_response_ms": 0},
            "wake_word": {"detections_today": 0, "detections_all_time": 0,
                          "false_positives_today": 0},
            "stt": {"avg_ms": 0, "p95_ms": 0, "count_today": 0,
                    "chunk_avg_ms": 50, "chunk_p95_ms": 90,
                    "avg_audio_seconds": 1.5},
            "tts": {"avg_ms": 0, "p95_ms": 0, "count_today": 0},
            "llm": {"response_avg_ms": 0, "response_p95_ms": 0,
                    "tokens_per_sec": 35, "requests_today": 0,
                    "avg_tokens_out": 86, "context_usage_pct": 14,
                    "timeouts_today": 1},
            "mock": False,
        }

    def test_no_wake_history_key(self):
        """Wake-history / transcript view ripped from snapshot."""
        patches = self._patches(self._stub_stats())
        for p in patches:
            p.start()
        try:
            snap = stats_publisher.snapshot_stats(client=SimpleNamespace())
        finally:
            for p in patches:
                p.stop()
        self.assertNotIn("wake_history", snap,
                         "wake_history must be removed from snapshot")

    def test_stats_carries_new_stt_and_llm_fields(self):
        patches = self._patches(self._stub_stats())
        for p in patches:
            p.start()
        try:
            snap = stats_publisher.snapshot_stats(client=SimpleNamespace())
        finally:
            for p in patches:
                p.stop()
        stats = snap["stats"]
        for k in ("chunk_avg_ms", "chunk_p95_ms", "avg_audio_seconds"):
            self.assertIn(k, stats["stt"], f"stt.{k} must appear in snapshot")
        for k in ("tokens_per_sec", "avg_tokens_out",
                  "context_usage_pct", "timeouts_today"):
            self.assertIn(k, stats["llm"], f"llm.{k} must appear in snapshot")
        # Sanity: dropped fields stay dropped.
        self.assertNotIn("ttft_avg_ms", stats["llm"])
        self.assertNotIn("avg_join_latency_ms", stats["wake_word"])
        # Voice session-seconds is real, not the voice_join × 60s placeholder.
        self.assertEqual(stats["voice_playback"]["session_seconds_today"], 600)

    def test_snapshot_round_trips_through_html_injection(self):
        """End-to-end: snapshot → render_snapshot_html → recoverable JSON."""
        patches = self._patches(self._stub_stats())
        for p in patches:
            p.start()
        try:
            snap = stats_publisher.snapshot_stats(client=SimpleNamespace())
        finally:
            for p in patches:
                p.stop()
        out = stats_publisher.render_snapshot_html(
            "<head></head><body></body>", snap,
        )
        marker = "JSON.parse("
        i = out.find(marker) + len(marker)
        j = out.find(")", i)
        literal = out[i:j]
        json_str = json.loads(literal).replace("<\\/", "</")
        recovered = json.loads(json_str)
        self.assertEqual(recovered["stats"]["llm"]["avg_tokens_out"], 86)
        self.assertEqual(recovered["stats"]["stt"]["chunk_avg_ms"], 50)
        self.assertNotIn("wake_history", recovered)


if __name__ == "__main__":
    unittest.main()
