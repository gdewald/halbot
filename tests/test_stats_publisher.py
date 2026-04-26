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


if __name__ == "__main__":
    unittest.main()
