# Step 5 — Daemon Panel

**Goal:** replace the Daemon placeholder with service status,
Start/Stop/Restart controls, live uptime/memory/CPU, NSSM
auto-restart toggle (hidden if NSSM missing), event-history list
stubbed with a single `mock` row.

**Runnable at end:** yes — Start/Stop/Restart round-trip the real
Windows service.

## Files you will touch

- `frontend/src/panels/Daemon.jsx` (rewrite placeholder)
- `frontend/src/panels/daemon/ActionBtn.jsx` (new)
- `frontend/src/panels/daemon/MockBadge.jsx` (new — reused by step 6 + 7)

Do not touch `dashboard/`, `halbot/`, or other panels.

## 5.1 `frontend/src/panels/daemon/MockBadge.jsx`

Small shared pill for "not yet implemented" markers. Shared across
panels 5 / 6 / 7.

```jsx
import { T } from '../../tokens.js';

export function MockBadge({ label = 'mock', title }) {
  return (
    <span title={title || 'not yet implemented — mocked for design review'}
      style={{
        fontFamily: 'JetBrains Mono', fontSize: 9, fontWeight: 600,
        color: T.yellow, background: `${T.yellow}14`,
        border: `1px solid ${T.yellow}35`,
        borderRadius: 3, padding: '1px 5px',
        letterSpacing: '0.06em', textTransform: 'uppercase',
      }}>{label}</span>
  );
}
```

## 5.2 `frontend/src/panels/daemon/ActionBtn.jsx`

Mockup lines 467–480, verbatim with `T` import:

```jsx
import { useState } from 'react';
import { T } from '../../tokens.js';

export function ActionBtn({ label, icon, color, onClick, disabled }) {
  const [h, setH] = useState(false);
  return (
    <button onClick={onClick} disabled={disabled}
      onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)}
      style={{
        display: 'flex', alignItems: 'center', gap: 5, height: 32, padding: '0 12px',
        borderRadius: 7, border: `1px solid ${color}35`,
        background: h ? `${color}22` : `${color}10`,
        color, fontSize: 12, fontWeight: 600, cursor: disabled ? 'default' : 'pointer',
        transition: 'all 0.12s', opacity: disabled ? 0.45 : 1, fontFamily: 'DM Sans',
      }}>
      <span style={{ fontSize: 13 }}>{icon}</span>{label}
    </button>
  );
}
```

## 5.3 `frontend/src/panels/Daemon.jsx`

```jsx
import { useEffect, useState, useCallback } from 'react';
import { T } from '../tokens.js';
import { b } from '../bridge.js';
import { ActionBtn } from './daemon/ActionBtn.jsx';
import { MockBadge } from './daemon/MockBadge.jsx';

const POLL_MS = 2000;

function fmtUptime(sec) {
  if (!sec || sec < 0) return '—';
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}

export function DaemonPanel() {
  const [svc, setSvc] = useState({ state: 'unknown', pid: 0 });
  const [health, setHealth] = useState(null);
  const [proc, setProc] = useState({ memory_mb: 0, cpu_pct: 0 });
  const [loading, setLoading] = useState(false);
  const [autoRestart, setAutoRestart] = useState(null);  // null = NSSM unknown/missing

  const running = svc.state === 'running';

  const refresh = useCallback(async () => {
    try { setSvc(await b.serviceQuery()); } catch {}
    try { setHealth(await b.health()); } catch { setHealth(null); }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    if (!running || !svc.pid) { setProc({ memory_mb: 0, cpu_pct: 0 }); return; }
    let alive = true;
    const tick = async () => {
      try {
        const p = await b.procStats(svc.pid);
        if (alive) setProc(p);
      } catch {}
    };
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [running, svc.pid]);

  useEffect(() => {
    (async () => { try { setAutoRestart(await b.nssmGet()); } catch { setAutoRestart(null); } })();
  }, []);

  const doAction = async (act) => {
    setLoading(true);
    try {
      if (act === 'start') await b.serviceStart();
      else if (act === 'stop') await b.serviceStop();
      else if (act === 'restart') await b.serviceRestart();
      await refresh();
    } finally {
      setLoading(false);
    }
  };

  const toggleAutoRestart = async () => {
    if (autoRestart === null) return;  // NSSM missing
    const next = !autoRestart;
    const ok = await b.nssmSet(next);
    if (ok) setAutoRestart(next);
  };

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16, overflow: 'auto', height: '100%', animation: 'fadeIn 0.15s ease' }}>

      {/* Status card */}
      <div style={{
        background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10, padding: 18,
        display: 'flex', alignItems: 'center', gap: 16,
      }}>
        <div style={{ position: 'relative' }}>
          <div style={{
            width: 52, height: 52, borderRadius: 12,
            background: running ? `${T.green}12` : `${T.red}12`,
            border: `1px solid ${running ? T.green : T.red}28`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            {loading ? (
              <div style={{
                width: 18, height: 18, border: `2px solid rgba(255,255,255,0.15)`,
                borderTopColor: T.blurple, borderRadius: '50%',
                animation: 'spin 0.7s linear infinite',
              }} />
            ) : (
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                {running ? (
                  <>
                    <circle cx="10" cy="10" r="8" stroke={T.green} strokeWidth="1.5" />
                    <rect x="7.5" y="7.5" width="1.8" height="5" rx=".9" fill={T.green} />
                    <rect x="10.7" y="7.5" width="1.8" height="5" rx=".9" fill={T.green} />
                  </>
                ) : (
                  <>
                    <circle cx="10" cy="10" r="8" stroke={T.red} strokeWidth="1.5" />
                    <polygon points="8.5,7 14,10 8.5,13" fill={T.red} />
                  </>
                )}
              </svg>
            )}
          </div>
          <div style={{
            position: 'absolute', top: -2, right: -2, width: 11, height: 11, borderRadius: '50%',
            background: running ? T.green : T.red, border: `2px solid ${T.bg}`,
            animation: running ? 'pulse 2s ease-in-out infinite' : 'none',
          }} />
        </div>

        <div>
          <div style={{ fontSize: 16, fontWeight: 600, color: T.text, display: 'flex', alignItems: 'center', gap: 8 }}>
            halbot daemon
            <span style={{
              fontSize: 10, fontWeight: 500, color: running ? T.green : T.red,
              background: running ? `${T.green}14` : `${T.red}14`,
              padding: '2px 7px', borderRadius: 20,
              border: `1px solid ${running ? T.green : T.red}28`,
            }}>{running ? 'RUNNING' : svc.state.toUpperCase()}</span>
          </div>
          <div style={{ fontSize: 11, color: T.sub, marginTop: 3, fontFamily: 'JetBrains Mono' }}>
            headless discord bot service · PID {svc.pid || '—'}
            {health?.daemon_version && <> · v{health.daemon_version}</>}
          </div>
        </div>

        <div style={{ flex: 1 }} />

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {autoRestart !== null && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginRight: 8 }}>
              <span style={{ fontSize: 11, color: T.sub }}>auto-restart</span>
              <div onClick={toggleAutoRestart} style={{
                width: 34, height: 18, borderRadius: 9, cursor: 'pointer',
                background: autoRestart ? T.blurple : 'rgba(255,255,255,0.12)',
                transition: 'background 0.2s', position: 'relative',
              }}>
                <div style={{
                  position: 'absolute', top: 2, left: autoRestart ? 16 : 2,
                  width: 14, height: 14, borderRadius: '50%', background: '#fff',
                  transition: 'left 0.2s', boxShadow: '0 1px 3px rgba(0,0,0,0.4)',
                }} />
              </div>
            </div>
          )}
          {running ? (
            <>
              <ActionBtn onClick={() => doAction('restart')} label="Restart" icon="↺" color={T.yellow} disabled={loading} />
              <ActionBtn onClick={() => doAction('stop')}    label="Stop"    icon="■" color={T.red}    disabled={loading} />
            </>
          ) : (
            <ActionBtn onClick={() => doAction('start')} label="Start" icon="▶" color={T.green} disabled={loading} />
          )}
        </div>
      </div>

      {/* Stats grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 10 }}>
        {[
          { label: 'Uptime', value: running ? fmtUptime(health?.uptime_seconds) : '—', mono: true },
          { label: 'Memory', value: running ? String(proc.memory_mb) : '—', unit: 'MB' },
          { label: 'CPU',    value: running ? proc.cpu_pct.toFixed(1)  : '—', unit: '%' },
          { label: 'Guilds', value: '—', mock: true },
        ].map(s => (
          <div key={s.label} style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 9, padding: '12px 14px', position: 'relative',
          }}>
            <div style={{
              fontSize: 9, color: T.dim, textTransform: 'uppercase',
              letterSpacing: '0.09em', marginBottom: 5,
              display: 'flex', alignItems: 'center', gap: 6,
            }}>
              {s.label} {s.mock && <MockBadge />}
            </div>
            <div style={{
              fontSize: 20, fontWeight: 600, color: T.text,
              fontFamily: s.mono ? 'JetBrains Mono' : 'DM Sans',
            }}>
              {s.value}
              {s.unit && <span style={{ fontSize: 11, color: T.sub, fontWeight: 400 }}> {s.unit}</span>}
            </div>
          </div>
        ))}
      </div>

      {/* Event history — mocked in phase 1 */}
      <div>
        <div style={{
          fontSize: 10, color: T.dim, textTransform: 'uppercase',
          letterSpacing: '0.09em', marginBottom: 8,
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          Event Log <MockBadge />
        </div>
        <div style={{
          background: T.surface, border: `1px solid ${T.border}`,
          borderRadius: 9, padding: '16px 14px',
          fontSize: 12, color: T.sub, fontFamily: 'DM Sans',
        }}>
          Event history wires up in a later phase once the daemon
          exposes crash / start / stop transitions via a dedicated RPC.
        </div>
      </div>

    </div>
  );
}
```

### Important notes for the implementer

- **Auto-restart toggle visibility.** `b.nssmGet()` returns `null`
  when NSSM is not on PATH or the key is unreadable. Do not show
  a disabled toggle in that case — hide it. Lying about a dead
  control is worse than omitting it.
- **PID source.** `svc.pid` comes from
  `QueryServiceStatusEx`. If it is 0 while state is `running`
  (transient start-pending), `procStats` returns zeros gracefully.
- **No fake numbers.** Do not compute a plausible "Guilds" count.
  It renders `—` + `mock` badge until Discord lands.

## 5.4 Rebuild + verify

```powershell
cd frontend
npm run build
cd ..
```

## 5.5 Verification gate

**Terminal 1:**

```powershell
uv run python -m halbot.daemon run
```

(If running via installed service instead, `sc start halbot`.)

**Terminal 2:**

```powershell
uv run python -m dashboard.app
```

Navigate to the Daemon panel. Expected:

- Status pill shows `RUNNING` in green.
- PID matches `sc queryex halbot | findstr PID`.
- Uptime ticks every 2 seconds (poll cadence).
- Memory and CPU show non-zero values.
- Guilds card shows `—` with a yellow `MOCK` badge.
- Clicking **Stop**: spinner in the icon for ~1s, pill flips to
  `STOPPED` in red, Start button appears.
- Clicking **Start**: pill flips back to `RUNNING`.
- If NSSM is installed and service is under NSSM: auto-restart
  toggle is visible. If not: no toggle rendered.
- Event Log card shows the "wires up in a later phase" message
  with the `MOCK` badge next to the section header.

If Stop/Start fails with a permission error, the installing user
lacks `SERVICE_START|STOP|QUERY_STATUS`; re-run
`halbot-daemon setup --install` from an elevated shell.

## Commit

```powershell
git add frontend/src/panels/Daemon.jsx frontend/src/panels/daemon/ActionBtn.jsx frontend/src/panels/daemon/MockBadge.jsx docs/plans/007-step-5-daemon-panel.md
git commit -m "feat(007): daemon panel — service control, uptime, stats"
```
