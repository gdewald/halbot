# 022 — Design refresh: dashboard + share Stats/Analytics

## Problem

User-supplied mockup (`Downloads/halbot(1).html`, ~1360 lines) is a refreshed
visual pass on the existing dashboard. Codebase already mirrors mockup
component decomposition (SidebarNarrow/Wide/TopTabBar, WinTitleBar, StatusBar,
LevelPill, navItems, tokens.js, panels/{stats,daemon,logs,config}/...). This is
a presentation refresh, not a rewrite.

Two surfaces:
- **Dashboard** (pywebview): all five panels (Logs, Daemon, Config, Stats, Analytics)
- **Share** (`/halbot-stats` snapshot): Stats + Analytics only (snapshot bakes
  same React bundle, `IS_SNAPSHOT` flag swaps data source to
  `window.__STATS_SNAPSHOT__`)

## Shape (deltas vs current code)

### Stats — `frontend/src/panels/Stats.jsx`
- **+ Daemon Health Banner** at top: green/red pulse dot, "Daemon healthy" + uptime + RSS, right column shows avg TTS/LLM ms
- **+ Wake-word history table** under Wake Word section: last N rows of `voice_join_text` events with phrase + action, ok/false dot
- **+ MissingDataDrawer** at bottom: collapsible list of metrics-not-yet-emitted, grouped by section (Wake/STT/TTS/LLM) with the source key (e.g. `llm_ttft_ms`)
- **Drop inline "not yet emitted" sub-labels** on cards (drawer is source of truth)
- Soundboard table, sub-section StatCards, LatencyCard layout: unchanged (already match mockup)

### Analytics — `frontend/src/panels/Analytics.jsx`
- **Header refresh**: "Who's using halbot, what they're doing" + caption "Aggregated event history · click any pill to filter"
- **Window-picker chrome**: distinct panel-bg row with `WINDOW` label + 24h/7d/30d segmented buttons + total events count on right
- **Leaderboards**: BarRow component with rank #N + avatar/emoji + label + bar + count. Avatar = colored circle with first letter (color hashed from handle).
- **EventMix pills**: clickable kind pills with `<emoji> <key> <count>`; active pill = colored bg + border

### Daemon — `frontend/src/panels/Daemon.jsx`
- **Status card refresh**: 52px rounded square icon (running=play/stop SVG, loading=spinner), pulse status dot top-right corner, RUNNING/STOPPED badge, "headless discord bot service · PID NNNNN" subline
- **Stat grid (4 cols)**: Uptime (mono), Memory MB, CPU %, Guilds — pulled from Health RPC
- **+ Event log** (collapsible card): last N service-state transitions with colored dot + time + event text
- **Auto-restart toggle**: stub UI only this pass (NSSM auto-restart already on)

### Logs — `frontend/src/panels/Logs.jsx`
- **Multi-select capsule**: ALL master button + per-level toggles, all in one bordered group with capsule background. ALL shows `N/4` count badge; per-level shows count badge. Off levels show line-through + dim.
- **+ Bottom status bar**: 22px row showing `{visible} lines [matching "{search}"]` left-aligned and `{N} errors · {M} warnings` right-aligned
- LogActionBtn polish: gap/size adjustments per mockup

### Config — `frontend/src/panels/Config.jsx`
- **Group cards** (already exist) gain MODIFIED badge in header when any field in group is dirty
- **Row grid** (already exists): key | value | revert column. Dirty rows get blurple-tinted bg + small `●` dot before label.
- Toolbar: "{N} unsaved changes" left, Revert all + Save to disk right with disabled states

## Files touched

- `frontend/src/panels/Stats.jsx` (rework section order, drop inline subs)
- `frontend/src/panels/stats/HealthBanner.jsx` (NEW)
- `frontend/src/panels/stats/WakeHistory.jsx` (NEW)
- `frontend/src/panels/stats/MissingDataDrawer.jsx` (NEW)
- `frontend/src/panels/Analytics.jsx` (header, window picker chrome, BarRow rewrite, EventMix pills)
- `frontend/src/panels/analytics/BarRow.jsx` (NEW)
- `frontend/src/panels/analytics/LeaderHeader.jsx` (NEW)
- `frontend/src/panels/Daemon.jsx` (status card chrome, event-log card)
- `frontend/src/panels/daemon/EventLog.jsx` (NEW, optional — depends on Q1)
- `frontend/src/panels/Logs.jsx` (capsule + bottom status bar)
- `frontend/src/panels/Config.jsx` (MODIFIED badge, dirty row tint)

Reused unchanged: tokens.js, all sidebar/topbar components, LevelPill, ToggleBtn,
StatCard, LatencyCard, ConfigRow, FieldInput, LogRow, useLogStream, bridge.js.

## Locked answers (2026-04-26)

1. **(b)** Extend `HealthReply` proto with `pid`, `rss_bytes`, `cpu_percent`,
   `guild_count`. Wire via `psutil` (already in deps) + `os.getpid` + discord
   client guild list.
2. **(a)** Add `wakeHistory()` readback (server-side query over wake-word events,
   last 25 rows) returning `[{ts, phrase, action, ok}]`. Snapshot pipeline picks
   it up via the same readback.
3. **Drawer-canonical**: drop inline "not yet emitted" subs. Cards with no
   real data show `—`; their source key lives in `MissingDataDrawer`.
4. **Split commits, single branch**. One topic-scoped commit per panel:
   - `feat(proto): expand Health with pid/rss/cpu/guilds`
   - `feat(stats): daemon health banner + missing-data drawer`
   - `feat(stats): wake-word history table`
   - `feat(analytics): chrome refresh — header, picker, BarRow avatars, EventMix`
   - `feat(daemon): status card + stat grid + event log`
   - `feat(logs): multi-select capsule + bottom status bar`
   - `feat(config): MODIFIED group badge + dirty row tint`

## Original open questions (resolved above)

1. **Daemon panel data**: mockup shows PID, Memory MB, CPU %, Guilds, and a
   crash/start history list. Health RPC currently returns `daemon_version` only.
   Options: (a) wire only what Health already provides, stub the rest visually;
   (b) extend Health RPC to include pid/rss/cpu/guild_count this pass; (c) defer
   the whole stat-grid refresh until a later Health expansion. Recommend (a) —
   visual refresh now, real numbers later.

2. **Wake-word history**: mockup table needs last N wake-word detections with
   the action taken. Options: (a) add `QueryStats(kind="voice_join_text", group_by="")`
   readback that returns last N rows; (b) reuse existing event-stream live tail
   and keep ring buffer in JS; (c) skip table this pass, ship just the StatCards.
   Recommend (a) — small SQL query, reads cleanest in the snapshot path too.

3. **MissingDataDrawer source-of-truth**: keep inline "not yet emitted" sub-labels
   on the aspirational cards too, or only in the drawer? Mockup shows both,
   which double-tells. Recommend: drop inline, drawer is canonical. Hover/click
   on a card with no data could highlight its drawer entry — but that's a polish
   pass, not this one.

4. **Scope splitting**: ship as one PR or one-panel-per-PR? Stats + Analytics
   are share-visible (snapshot pipeline) — those are the highest-priority and
   benefit most from coordinated chrome. Daemon/Logs/Config are dashboard-only.
   Recommend: PR1 = Stats + Analytics (share affecting), PR2 = Daemon + Logs +
   Config (dashboard-only chrome). Each PR is one topic-scoped commit.

## Non-goals

- No new analytics event kinds; no proto schema changes (except optional Health pid)
- No bridge.js / IS_SNAPSHOT changes; share path keeps `window.__STATS_SNAPSHOT__` shape
- No layout changes (narrow/wide/tabs chooser + tweaks panel left alone)
- No new RPC methods unless answer to Q2 picks (a)
- No mock data shipped; if a card has no real backing event yet it shows `—`
  and appears in MissingDataDrawer
