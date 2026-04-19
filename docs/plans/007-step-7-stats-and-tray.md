# Step 7 — Stats Panel + Tray Wiring

**Goal:** port the full Stats panel from the mockup (6 sections,
stat cards, latency cards, soundboard table, wake-history list)
and render it dimmed behind a single full-panel mock overlay
when `GetStats().mock` is true — which it is for all of phase 1.
Wire the tray icon's "Open dashboard" menu item; delete the old
Tkinter log viewer.

**Runnable at end:** yes — from the tray menu, "Open dashboard"
opens the full-featured window with all four panels working.

## Files you will touch

- `frontend/src/panels/Stats.jsx` (rewrite placeholder)
- `frontend/src/panels/stats/StatCard.jsx` (new)
- `frontend/src/panels/stats/LatencyCard.jsx` (new)
- `frontend/src/panels/stats/MockData.js` (new — mockup seed data)
- `tray/tray.py` (edit — add "Open dashboard" menu item, remove log viewer)
- `tray/log_viewer.py` (delete)

## 7.1 `frontend/src/panels/stats/MockData.js`

The mockup has seed arrays for sounds + wake history and literal
numbers everywhere else. Move them into one file so the panel
can import + render behind the overlay without the panel source
itself becoming the source of "fake numbers."

```js
// Mock data for the Stats panel preview. Values surface behind a
// visible "preview only" overlay because the backing subsystems
// (soundboard, voice, wake-word, STT, TTS, LLM) are not yet
// implemented. Do not remove the overlay without replacing these
// values with real telemetry from GetStats.

export const SOUND_LIST = [
  { name: 'airhorn',   emoji: '📯', desc: 'Classic airhorn blast', plays: 142, lastPlayed: '10 min ago', size: '48 KB' },
  { name: 'bruh',      emoji: '😐', desc: '',                      plays: 89,  lastPlayed: '2 hrs ago',  size: '32 KB' },
  { name: 'vine boom', emoji: '💥', desc: 'Vine era impact sound', plays: 211, lastPlayed: 'just now',   size: '61 KB' },
  { name: 'rizz',      emoji: '😎', desc: '',                      plays: 34,  lastPlayed: 'yesterday',  size: '29 KB' },
  { name: 'nope',      emoji: '🚫', desc: '',                      plays: 17,  lastPlayed: '3 days ago', size: '18 KB' },
  { name: 'tada',      emoji: '🎉', desc: 'Tada fanfare',          plays: 55,  lastPlayed: '1 hr ago',   size: '72 KB' },
];

export const WAKE_HISTORY = [
  { time: '09:41:22', phrase: 'hey hal', action: 'Joined #gaming, played vine_boom.mp3', ok: true },
  { time: '09:28:07', phrase: 'hey hal', action: 'Joined #general, played airhorn.mp3',  ok: true },
  { time: '08:55:44', phrase: 'hey hal', action: 'False positive — no voice channel',    ok: false },
  { time: '07:12:03', phrase: 'hey hal', action: 'Joined #music, played bruh.mp3',       ok: true },
];

// Aggregate numbers used in the stat cards. Single source of truth
// so the overlay can be unambiguous about "none of these are real."
export const MOCK_NUMBERS = {
  soundboard: { backedUp: 847, storageMb: 214, lastSync: '4m ago',      lastSyncTs: '09:38:12 today', newSince: 12 },
  voice:      { playedToday: 38, playedAllTime: 2841, sessionToday: '1h 12m', avgResponseMs: 340 },
  wake:       { detectionsToday: 4, detectionsAllTime: 312, falsePositivesToday: 1, falsePctToday: '0.3%', avgJoinMs: 620 },
  stt:        { latencyAvg: 210, latencyP95: 480, chunkAvg: 85, chunkP95: 140, segmentsToday: 187, avgUtteranceSec: 3.2 },
  tts:        { firstChunkAvg: 128, firstChunkP95: 290, fullAvg: 340, fullP95: 680, rendersToday: 42, fallbacksToday: 3 },
  llm:        {
    responseAvgMs: 1240, responseP95Ms: 4800,
    ttftAvgMs: 320, ttftP95Ms: 890, tokPerSec: 38,
    requestsToday: 52, avgTokensOut: 184, ctxUsagePct: 62, timeoutsToday: 2,
  },
};
```

## 7.2 `frontend/src/panels/stats/StatCard.jsx`

Mockup lines 695–710.

```jsx
import { T } from '../../tokens.js';

export function StatCard({ label, value, unit, sub, accent }) {
  const ac = accent || T.blurple;
  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`,
      borderRadius: 9, padding: '14px 16px',
      position: 'relative', overflow: 'hidden',
    }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: `linear-gradient(90deg,${ac},${ac}44)`,
      }} />
      <div style={{
        fontSize: 9, color: T.dim, textTransform: 'uppercase',
        letterSpacing: '0.09em', marginBottom: 6,
      }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 600, color: T.text, lineHeight: 1 }}>
        {value}
        {unit && <span style={{ fontSize: 12, color: T.sub, fontWeight: 400, marginLeft: 3 }}>{unit}</span>}
      </div>
      {sub && <div style={{ fontSize: 10, color: T.dim, marginTop: 5 }}>{sub}</div>}
    </div>
  );
}

export function MiniBar({ value, max, color }) {
  return (
    <div style={{ flex: 1, height: 4, background: 'rgba(255,255,255,0.07)', borderRadius: 2, overflow: 'hidden' }}>
      <div style={{
        width: `${Math.min(100, (value / max) * 100)}%`, height: '100%',
        background: color || T.blurple, borderRadius: 2, transition: 'width 0.4s ease',
      }} />
    </div>
  );
}

export function SectionHeader({ label, icon }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
      <span style={{ fontSize: 14 }}>{icon}</span>
      <span style={{
        fontSize: 11, fontWeight: 600, color: T.text,
        textTransform: 'uppercase', letterSpacing: '0.07em',
      }}>{label}</span>
      <div style={{ flex: 1, height: 1, background: T.border, marginLeft: 4 }} />
    </div>
  );
}
```

## 7.3 `frontend/src/panels/stats/LatencyCard.jsx`

Mockup lines 722–752.

```jsx
import { T } from '../../tokens.js';

function LatencyBar({ avg, p95, max, color }) {
  const ac = color || T.blurple;
  return (
    <div style={{
      position: 'relative', height: 6,
      background: 'rgba(255,255,255,0.06)', borderRadius: 3, marginTop: 8,
    }}>
      <div style={{
        position: 'absolute', left: 0, top: 0, bottom: 0,
        width: `${Math.min(100, (avg / max) * 100)}%`,
        background: ac, borderRadius: 3, opacity: 0.9,
      }} />
      <div style={{
        position: 'absolute', top: -2, bottom: -2,
        left: `${Math.min(99, (p95 / max) * 100)}%`,
        width: 2, background: ac, borderRadius: 1, opacity: 0.5,
      }} />
    </div>
  );
}

export function LatencyCard({ label, avg, p95, unit, color, sub }) {
  const ac = color || T.blurple;
  const max = Math.max(p95 * 1.2, avg * 1.2, 1);
  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`,
      borderRadius: 9, padding: '12px 14px',
      position: 'relative', overflow: 'hidden',
    }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: `linear-gradient(90deg,${ac},${ac}44)`,
      }} />
      <div style={{
        fontSize: 9, color: T.dim, textTransform: 'uppercase',
        letterSpacing: '0.09em', marginBottom: 4,
      }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span style={{ fontSize: 22, fontWeight: 600, color: T.text, fontFamily: 'JetBrains Mono' }}>{avg}</span>
        <span style={{ fontSize: 10, color: T.sub }}>{unit} avg</span>
        <span style={{ fontSize: 11, color: T.dim, marginLeft: 'auto', fontFamily: 'JetBrains Mono' }}>
          p95 {p95}{unit}
        </span>
      </div>
      <LatencyBar avg={avg} p95={p95} max={max} color={ac} />
      {sub && <div style={{ fontSize: 9, color: T.dim, marginTop: 5 }}>{sub}</div>}
    </div>
  );
}
```

## 7.4 `frontend/src/panels/Stats.jsx`

Render the full mockup body from `MOCK_NUMBERS`, then overlay.

```jsx
import { useEffect, useState } from 'react';
import { T } from '../tokens.js';
import { b } from '../bridge.js';
import { StatCard, MiniBar, SectionHeader } from './stats/StatCard.jsx';
import { LatencyCard } from './stats/LatencyCard.jsx';
import { SOUND_LIST, WAKE_HISTORY, MOCK_NUMBERS } from './stats/MockData.js';

export function StatsPanel() {
  const [mock, setMock] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const s = await b.getStats();
        setMock(!!s.mock);
      } catch {
        setMock(true);
      }
    })();
  }, []);

  const maxPlays = Math.max(...SOUND_LIST.map(s => s.plays));
  const N = MOCK_NUMBERS;

  return (
    <div style={{ position: 'relative', height: '100%', animation: 'fadeIn 0.15s ease' }}>
      <div style={{
        height: '100%', overflow: 'auto', padding: '16px',
        filter: mock ? 'blur(2px) saturate(0.7) opacity(0.6)' : 'none',
        pointerEvents: mock ? 'none' : 'auto',
      }}>

        {/* Soundboard */}
        <SectionHeader label="Soundboard Backup" icon="💾" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 12 }}>
          <StatCard label="Sounds backed up"   value={N.soundboard.backedUp}  sub="across all categories"   accent={T.blurple} />
          <StatCard label="Storage used"       value={N.soundboard.storageMb} unit="MB" sub="on disk"       accent={T.blurple} />
          <StatCard label="Last sync"          value={N.soundboard.lastSync}         sub={N.soundboard.lastSyncTs} accent={T.cyan} />
          <StatCard label="New since last run" value={N.soundboard.newSince}  sub="added since yesterday"   accent={T.green} />
        </div>
        <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 9, overflow: 'hidden', marginBottom: 18 }}>
          <div style={{
            padding: '7px 14px', borderBottom: `1px solid ${T.border}`,
            display: 'grid', gridTemplateColumns: '26px 130px 1fr 90px 110px 56px', gap: 8,
            fontSize: 9, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em',
          }}>
            <span /><span>Name</span><span>Description</span><span>Plays</span><span>Last played</span>
            <span style={{ textAlign: 'right' }}>Size</span>
          </div>
          {SOUND_LIST.map((s, i) => (
            <div key={s.name} style={{
              display: 'grid', gridTemplateColumns: '26px 130px 1fr 90px 110px 56px',
              alignItems: 'center', gap: 8, padding: '6px 14px',
              borderBottom: i < SOUND_LIST.length - 1 ? `1px solid ${T.border}` : 'none',
              background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.012)',
            }}>
              <span style={{ fontSize: 14, textAlign: 'center' }}>{s.emoji}</span>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 12, color: T.cyan, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
              <span style={{ fontSize: 11, color: T.dim, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontStyle: s.desc ? 'normal' : 'italic' }}>{s.desc || '—'}</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <MiniBar value={s.plays} max={maxPlays} color={T.blurple} />
                <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: T.text, minWidth: 26, textAlign: 'right' }}>{s.plays}</span>
              </div>
              <span style={{ fontSize: 11, color: T.sub }}>{s.lastPlayed}</span>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: T.dim, textAlign: 'right' }}>{s.size}</span>
            </div>
          ))}
        </div>

        {/* Voice playback */}
        <SectionHeader label="Voice Playback" icon="🔊" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 18 }}>
          <StatCard label="Sounds played today" value={N.voice.playedToday}    sub="across all sessions"  accent={T.cyan} />
          <StatCard label="Total all-time"      value={N.voice.playedAllTime.toLocaleString()}  sub="since bot started"    accent={T.cyan} />
          <StatCard label="Session time today"  value={N.voice.sessionToday}   sub="cumulative voice"     accent={T.yellow} />
          <StatCard label="Avg response time"   value={N.voice.avgResponseMs}  unit="ms" sub="TTS + join" accent={T.green} />
        </div>

        {/* Wake word */}
        <SectionHeader label="Wake Word" icon="🎙" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 10 }}>
          <StatCard label="Detections today" value={N.wake.detectionsToday}  sub="hey hal"        accent={T.green} />
          <StatCard label="Total all-time"   value={N.wake.detectionsAllTime} sub="since tracking" accent={T.green} />
          <StatCard label="False positives"  value={N.wake.falsePositivesToday} unit="today" sub={N.wake.falsePctToday} accent={T.yellow} />
          <StatCard label="Avg join latency" value={N.wake.avgJoinMs} unit="ms" sub="wake → audio" accent={T.blurple} />
        </div>
        <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 9, overflow: 'hidden', marginBottom: 18 }}>
          <div style={{
            padding: '7px 14px', borderBottom: `1px solid ${T.border}`,
            display: 'grid', gridTemplateColumns: '80px 90px 1fr', gap: 8,
            fontSize: 9, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em',
          }}>
            <span>Time</span><span>Phrase</span><span>Action</span>
          </div>
          {WAKE_HISTORY.map((w, i) => (
            <div key={i} style={{
              display: 'grid', gridTemplateColumns: '80px 90px 1fr',
              alignItems: 'center', gap: 8, padding: '7px 14px',
              borderBottom: i < WAKE_HISTORY.length - 1 ? `1px solid ${T.border}` : 'none',
            }}>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: T.dim }}>{w.time}</span>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: T.blurpleL }}>{w.phrase}</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <div style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, background: w.ok ? T.green : T.yellow }} />
                <span style={{ fontSize: 12, color: w.ok ? T.text : T.yellow }}>{w.action}</span>
              </div>
            </div>
          ))}
        </div>

        {/* STT */}
        <SectionHeader label="Speech-to-Text (STT)" icon="👂" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 18 }}>
          <LatencyCard label="Transcription latency" avg={N.stt.latencyAvg} p95={N.stt.latencyP95} unit="ms" color={T.cyan} sub="time from audio end → text" />
          <LatencyCard label="Chunk processing time" avg={N.stt.chunkAvg}   p95={N.stt.chunkP95}   unit="ms" color={T.cyan} sub="per audio segment" />
          <StatCard    label="Segments today"    value={N.stt.segmentsToday}    sub="utterances processed"      accent={T.cyan} />
          <StatCard    label="Avg utterance len" value={N.stt.avgUtteranceSec}  unit="s" sub="mean audio length" accent={T.dim} />
        </div>

        {/* TTS */}
        <SectionHeader label="Text-to-Speech (TTS)" icon="🗣" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 18 }}>
          <LatencyCard label="First audio chunk" avg={N.tts.firstChunkAvg} p95={N.tts.firstChunkP95} unit="ms" color={T.yellow} sub="time to first playable chunk" />
          <LatencyCard label="Full render time"  avg={N.tts.fullAvg}       p95={N.tts.fullP95}       unit="ms" color={T.yellow} sub="complete synthesis time" />
          <StatCard    label="Renders today"     value={N.tts.rendersToday}    sub="TTS invocations"            accent={T.yellow} />
          <StatCard    label="Fallback to espeak" value={N.tts.fallbacksToday} unit="today" sub="kokoro failures" accent={T.red} />
        </div>

        {/* LLM */}
        <SectionHeader label="Text LLM" icon="🧠" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginBottom: 10 }}>
          <LatencyCard label="Response latency"    avg={N.llm.responseAvgMs} p95={N.llm.responseP95Ms} unit="ms" color={T.blurple} sub="full completion time" />
          <LatencyCard label="Time to first token" avg={N.llm.ttftAvgMs}     p95={N.llm.ttftP95Ms}     unit="ms" color={T.blurple} sub="TTFT — key for streaming" />
          <div style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 9, padding: '12px 14px', position: 'relative', overflow: 'hidden',
          }}>
            <div style={{
              position: 'absolute', top: 0, left: 0, right: 0, height: 2,
              background: `linear-gradient(90deg,${T.green},${T.green}44)`,
            }} />
            <div style={{ fontSize: 9, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.09em', marginBottom: 4 }}>Throughput</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <span style={{ fontSize: 22, fontWeight: 600, color: T.text, fontFamily: 'JetBrains Mono' }}>{N.llm.tokPerSec}</span>
              <span style={{ fontSize: 11, color: T.sub }}>tok/s</span>
            </div>
            <div style={{ fontSize: 9, color: T.dim, marginTop: 5 }}>avg generation speed</div>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 20 }}>
          <StatCard label="Requests today" value={N.llm.requestsToday} sub="LLM completions" accent={T.blurple} />
          <StatCard label="Avg tokens out" value={N.llm.avgTokensOut}  sub="per completion"  accent={T.blurple} />
          <StatCard label="Context usage"  value={N.llm.ctxUsagePct}  unit="%" sub="avg window fill" accent={T.yellow} />
          <StatCard label="Timeouts today" value={N.llm.timeoutsToday} sub={`of ${N.llm.requestsToday} requests`} accent={T.red} />
        </div>
        <div style={{ height: 8 }} />
      </div>

      {mock && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex',
          alignItems: 'center', justifyContent: 'center',
          background: 'rgba(12,12,15,0.45)', backdropFilter: 'blur(2px)',
          WebkitBackdropFilter: 'blur(2px)',
        }}>
          <div style={{
            background: T.raised, border: `1px solid ${T.yellow}35`,
            borderRadius: 12, padding: '20px 24px', maxWidth: 480, textAlign: 'center',
            boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
          }}>
            <div style={{
              fontSize: 10, fontWeight: 600, color: T.yellow,
              textTransform: 'uppercase', letterSpacing: '0.12em', marginBottom: 8,
            }}>Preview only</div>
            <div style={{ fontSize: 13, color: T.text, lineHeight: 1.5 }}>
              Stats wire up in a later phase. The numbers shown are static
              mock data from the design mockup — none of the underlying
              subsystems (soundboard, voice, wake-word, STT, TTS, LLM) are
              running yet.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

## 7.5 Tray wiring

Edit `tray/tray.py`:

1. Add import near the other imports:

   ```python
   from dashboard import app as dashboard_app
   ```

2. Add handler below `on_open_log`:

   ```python
   def on_open_dashboard(_icon, _item):
       threading.Thread(target=dashboard_app.open_window, daemon=True).start()
   ```

3. Replace the `menu = Menu(...)` block so "Open dashboard" is the
   first item and "Open log viewer" is removed:

   ```python
   menu = Menu(
       Item("Open dashboard", on_open_dashboard, default=True),
       Item("Service", service_menu),
       Item("Log level", level_menu),
       Item("Reset overrides", on_reset),
       Menu.SEPARATOR,
       Item("Quit", on_quit),
   )
   ```

4. Remove `from .log_viewer import LogViewer` and the
   `viewer = LogViewer(log_path)` line. Remove the `on_open_log`
   handler and its menu entry.

5. Delete `tray/log_viewer.py`.

**Thread model.** `open_window` calls `webview.start()` which
must run on the main thread of *its* process. pystray already
owns the main thread of the tray process. The daemon thread we
spawn is fine on Windows because `webview.start` is re-entrant
against a spawned loop there; if a platform warning surfaces,
switch to spawning a subprocess via
`subprocess.Popen([sys.executable, "-m", "dashboard.app"])`
and accept the extra ~60 MB process overhead.

**Default menu item.** `default=True` makes left-click on the tray
icon open the dashboard directly.

## 7.6 Rebuild + verify

```powershell
cd frontend
npm run build
cd ..
```

## 7.7 Verification gate

1. Daemon running (service or `uv run python -m halbot.daemon run`).
2. Launch tray from source or installed binary:

   ```powershell
   uv run python -m tray.tray
   ```

3. Left-click the tray icon → dashboard window opens.
4. Right-click → menu shows "Open dashboard" first, no "Open log
   viewer". "Log level" sub-menu still works.
5. In the dashboard, navigate to **Stats** → full mockup layout
   visible behind the overlay. Overlay reads "Preview only" with
   the explanatory paragraph.
6. All four panels still work (Logs tail, Daemon service control,
   Config save/revert) — this step should not have regressed any
   earlier panel.

## Commit

```powershell
git add frontend/src/panels/Stats.jsx frontend/src/panels/stats/StatCard.jsx frontend/src/panels/stats/LatencyCard.jsx frontend/src/panels/stats/MockData.js tray/tray.py docs/plans/007-step-7-stats-and-tray.md
git rm tray/log_viewer.py
git commit -m "feat(007): stats panel + tray wiring; drop tkinter log viewer"
```

Do not leave `tray/log_viewer.py` as dead code — delete it.
