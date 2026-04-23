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
  const [autoRestart, setAutoRestart] = useState(null);

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
    if (autoRestart === null) return;
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
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
        {[
          { label: 'Uptime', value: running ? fmtUptime(health?.uptime_seconds) : '—', mono: true },
          { label: 'Memory', value: running ? String(proc.memory_mb) : '—', unit: 'MB' },
          { label: 'CPU',    value: running ? proc.cpu_pct.toFixed(1)  : '—', unit: '%' },
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
