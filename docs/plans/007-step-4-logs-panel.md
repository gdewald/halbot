# Step 4 — Logs Panel

**Goal:** replace the Logs placeholder with a live log stream
rendered from `bridge.backlogLogs()` + `bridge.popLogBatch()`.
Filter by level, grep, wrap toggle, autoscroll ("tail") toggle,
clear button.

**Runnable at end:** yes — opening the dashboard shows real daemon
log lines; changing log level via the existing tray menu is visible
in the stream.

## Files you will touch

- `frontend/src/panels/Logs.jsx` (rewrite placeholder)
- `frontend/src/panels/logs/LogRow.jsx` (new)
- `frontend/src/panels/logs/useLogStream.js` (new)

Do not touch `dashboard/`, `halbot/`, or other panels.

## 4.1 `frontend/src/panels/logs/useLogStream.js`

Polls `bridge.popLogBatch()` every 250ms, appends into a bounded
in-memory array (max 2000 lines), exposes a `clear()` callback.
Also calls `bridge.backlogLogs(200)` once on mount.

```js
import { useEffect, useRef, useState, useCallback } from 'react';
import { b } from '../../bridge.js';

const MAX = 2000;
const POLL_MS = 250;

export function useLogStream() {
  const [logs, setLogs] = useState([]);
  const [connected, setConnected] = useState(false);
  const mounted = useRef(true);

  const push = useCallback((batch) => {
    if (!batch || batch.length === 0) return;
    setLogs(prev => {
      const next = prev.concat(batch);
      return next.length > MAX ? next.slice(next.length - MAX) : next;
    });
  }, []);

  useEffect(() => {
    mounted.current = true;
    let timer = null;

    (async () => {
      try {
        const backlog = await b.backlogLogs(400);
        if (!mounted.current) return;
        push(backlog);
        setConnected(backlog.length > 0);
      } catch (e) {
        setConnected(false);
      }
    })();

    const tick = async () => {
      if (!mounted.current) return;
      try {
        const batch = await b.popLogBatch(200);
        if (!mounted.current) return;
        if (batch.length) {
          push(batch);
          setConnected(true);
        }
      } catch (e) {
        setConnected(false);
      } finally {
        if (mounted.current) timer = setTimeout(tick, POLL_MS);
      }
    };
    timer = setTimeout(tick, POLL_MS);

    return () => {
      mounted.current = false;
      if (timer) clearTimeout(timer);
    };
  }, [push]);

  const clear = useCallback(() => setLogs([]), []);

  return { logs, connected, clear };
}
```

Each log record shape matches `dashboard/bridge.py::backlog_logs`:
`{ ts_ns, level, source, message }`.

## 4.2 `frontend/src/panels/logs/LogRow.jsx`

Port of mockup's `LogRow` (lines 288–310). Adapted to real data
shape (ts_ns → formatted HH:MM:SS.mmm):

```jsx
import { useState } from 'react';
import { T } from '../../tokens.js';
import { LevelPill } from '../../components/LevelPill.jsx';

function fmtTs(ts_ns) {
  const d = new Date(Math.floor(ts_ns / 1e6));
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  const s = String(d.getSeconds()).padStart(2, '0');
  const ms = String(d.getMilliseconds()).padStart(3, '0');
  return `${h}:${m}:${s}.${ms}`;
}

export function LogRow({ log, even, wrap }) {
  const [hov, setHov] = useState(false);
  const errLine = log.level === 'ERROR';
  const warnLine = log.level === 'WARN' || log.level === 'WARNING';
  return (
    <div onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
      style={{
        display: 'flex', alignItems: 'baseline', gap: 8,
        padding: '1.5px 12px',
        background: hov ? 'rgba(255,255,255,0.032)'
          : (even ? 'transparent' : 'rgba(255,255,255,0.012)'),
        borderLeft: `2px solid ${errLine ? `${T.red}50` : warnLine ? `${T.yellow}35` : 'transparent'}`,
      }}>
      <span style={{ color: T.dim, fontSize: 9.5, flexShrink: 0, minWidth: 84, fontFamily: 'JetBrains Mono' }}>
        {fmtTs(log.ts_ns)}
      </span>
      <LevelPill level={log.level} />
      <span style={{ color: `${T.blurple}bb`, fontSize: 10, flexShrink: 0, minWidth: 58, fontFamily: 'JetBrains Mono' }}>
        {log.source}
      </span>
      <span style={{
        color: errLine ? T.red : warnLine ? T.yellow : T.text,
        whiteSpace: wrap ? 'pre-wrap' : 'nowrap',
        overflow: wrap ? 'visible' : 'hidden',
        textOverflow: wrap ? 'clip' : 'ellipsis',
        flex: 1, minWidth: 0, fontFamily: 'JetBrains Mono', fontSize: 11.5,
      }}>{log.message}</span>
    </div>
  );
}
```

## 4.3 `frontend/src/panels/Logs.jsx`

Replace the placeholder with:

```jsx
import { useEffect, useMemo, useRef, useState } from 'react';
import { T, LC, LBD } from '../tokens.js';
import { ToggleBtn } from '../components/ToggleBtn.jsx';
import { LogRow } from './logs/LogRow.jsx';
import { useLogStream } from './logs/useLogStream.js';

const LEVEL_FILTERS = ['ALL', 'DEBUG', 'INFO', 'WARN', 'ERROR'];

function levelMatch(recordLevel, filter) {
  if (filter === 'ALL') return true;
  if (filter === 'WARN') return recordLevel === 'WARN' || recordLevel === 'WARNING';
  return recordLevel === filter;
}

export function LogsPanel() {
  const { logs, connected, clear } = useLogStream();
  const [filter, setFilter] = useState('ALL');
  const [search, setSearch] = useState('');
  const [autoScroll, setAutoScroll] = useState(true);
  const [wrap, setWrap] = useState(false);
  const scrollRef = useRef(null);
  const [viewOffset, setViewOffset] = useState(0);  // drops everything before this index
  const effectiveLogs = useMemo(() => logs.slice(viewOffset), [logs, viewOffset]);

  const visible = useMemo(() => {
    const q = search.toLowerCase();
    return effectiveLogs.filter(l => {
      if (!levelMatch(l.level, filter)) return false;
      if (q) return l.message.toLowerCase().includes(q) || (l.source || '').toLowerCase().includes(q);
      return true;
    });
  }, [effectiveLogs, filter, search]);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [visible.length, autoScroll]);

  const counts = useMemo(() => {
    const c = { ALL: effectiveLogs.length, DEBUG: 0, INFO: 0, WARN: 0, ERROR: 0 };
    for (const l of effectiveLogs) {
      if (l.level === 'DEBUG') c.DEBUG++;
      else if (l.level === 'INFO') c.INFO++;
      else if (l.level === 'WARN' || l.level === 'WARNING') c.WARN++;
      else if (l.level === 'ERROR') c.ERROR++;
    }
    return c;
  }, [effectiveLogs]);

  const onClear = () => setViewOffset(logs.length);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', animation: 'fadeIn 0.15s ease' }}>
      {/* toolbar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '8px 12px',
        borderBottom: `1px solid ${T.border}`, flexShrink: 0, flexWrap: 'wrap', rowGap: 6,
      }}>
        <div style={{ display: 'flex', gap: 2 }}>
          {LEVEL_FILTERS.map(f => {
            const a = filter === f;
            const color = f === 'ALL' ? T.blurple : LC[f === 'WARN' ? 'WARN' : f];
            return (
              <button key={f} onClick={() => setFilter(f)} style={{
                height: 26, padding: '0 9px', borderRadius: 5,
                border: `1px solid ${a ? color : T.border}`,
                background: a ? (f === 'ALL' ? `${T.blurple}22` : LBD[f]) : 'transparent',
                color: a ? color : T.dim,
                fontSize: 10, fontWeight: 600, cursor: 'pointer',
                fontFamily: 'JetBrains Mono',
                display: 'flex', alignItems: 'center', gap: 4, transition: 'all 0.1s',
              }}>
                {f}
                <span style={{ fontSize: 9, opacity: 0.65, background: 'rgba(255,255,255,0.08)', borderRadius: 3, padding: '0 3px' }}>
                  {counts[f]}
                </span>
              </button>
            );
          })}
        </div>
        <div style={{ flex: 1 }} />
        {/* search */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6, background: T.panel,
          border: `1px solid ${search ? T.blurple : T.border2}`, borderRadius: 5,
          padding: '0 9px', height: 26, transition: 'border-color 0.15s',
        }}>
          <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
            <circle cx="5" cy="5" r="3.5" stroke={T.sub} strokeWidth="1.3" />
            <path d="M8 8l2.5 2.5" stroke={T.sub} strokeWidth="1.3" strokeLinecap="round" />
          </svg>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="grep…"
            style={{ background: 'none', border: 'none', outline: 'none', color: T.text,
              fontSize: 11, width: 120, fontFamily: 'JetBrains Mono' }} />
          {search && <button onClick={() => setSearch('')} style={{
            background: 'none', border: 'none', color: T.dim, cursor: 'pointer', fontSize: 11, padding: 0,
          }}>✕</button>}
        </div>
        <ToggleBtn active={wrap} onClick={() => setWrap(w => !w)} label="wrap" />
        <ToggleBtn active={autoScroll} onClick={() => setAutoScroll(a => !a)} label="tail" accent={T.green} />
        <button onClick={onClear} style={{
          height: 26, padding: '0 9px', borderRadius: 5, border: `1px solid ${T.border}`,
          background: 'transparent', color: T.dim, fontSize: 10, cursor: 'pointer', fontFamily: 'DM Sans',
        }}>clear</button>
      </div>

      {/* log output */}
      <div ref={scrollRef} style={{ flex: 1, overflow: 'auto' }}>
        {visible.length === 0 && (
          <div style={{ padding: '20px 12px', color: T.dim, fontSize: 11, fontFamily: 'JetBrains Mono' }}>
            {connected ? 'no matching lines' : 'waiting for daemon log stream…'}
          </div>
        )}
        {visible.map((log, i) => (
          <LogRow key={`${log.ts_ns}-${i}`} log={log} even={i % 2 === 0} wrap={wrap} />
        ))}
        <div style={{ height: 12 }} />
      </div>

      {/* status bar */}
      <div style={{
        height: 22, flexShrink: 0, borderTop: `1px solid ${T.border}`,
        background: T.surface, display: 'flex', alignItems: 'center', padding: '0 12px', gap: 16,
      }}>
        <span style={{ fontSize: 10, color: T.dim, fontFamily: 'JetBrains Mono' }}>
          {visible.length} lines{search ? ` matching "${search}"` : ''}
          {!connected && ' · disconnected'}
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: T.dim, fontFamily: 'JetBrains Mono' }}>
          {counts.ERROR} errors · {counts.WARN} warnings
        </span>
      </div>
    </div>
  );
}
```

### Clear semantics

The mockup's `clear` button empties the UI buffer only. Same here:
we set `viewOffset = logs.length`; the in-memory ring continues
to grow with new lines, but everything before the clear mark
disappears from view. Do not truncate the daemon's log file.

## 4.4 Rebuild + verify

```powershell
cd frontend
npm run build
cd ..
```

## 4.5 Verification gate

**Terminal 1:**

```powershell
uv run python -m halbot.daemon run
```

**Terminal 2:**

```powershell
uv run python -m dashboard.app
```

Expected:

- On open, at least 1 backlog log line appears within 1 second.
- After the daemon's next INFO tick (~5s per current logging
  setup), a new row appears live.
- Filter buttons flip active state + filter rows.
- Grep box narrows rows and shows an ✕ to clear.
- Toggling "tail" off pauses autoscroll when new lines arrive.
- Toggling "wrap" changes how long lines render.
- Clear button empties visible lines; a new incoming line then
  appears on its own.
- The status bar shows the correct error + warning counts.

Edge: stop the daemon. Within 2 seconds the status bar should
show "disconnected". Restart the daemon — new lines resume.

## Commit

```powershell
git add frontend/src/panels/Logs.jsx frontend/src/panels/logs/LogRow.jsx frontend/src/panels/logs/useLogStream.js docs/plans/007-step-4-logs-panel.md
git commit -m "feat(007): logs panel with live tail from StreamLogs"
```
