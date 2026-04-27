import { useEffect, useRef, useState, useCallback } from 'react';
import { T } from '../tokens.js';
import { b } from '../bridge.js';
import { ActionBtn } from './daemon/ActionBtn.jsx';

const POLL_MS = 2000;
const EVENT_POLL_MS = 500;
const MAX_EVENTS = 50;
const LOCAL_STORAGE_KEY = 'halbot_daemon_events_v1';

// Kinds emitted server-side + synthetic client-observed transitions.
const DAEMON_KINDS = new Set(['daemon_boot', 'daemon_shutdown']);

function fmtUptime(sec) {
  if (!sec || sec < 0) return '—';
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}

function fmtEventTime(ns) {
  const d = new Date(Math.floor(ns / 1_000_000));
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const time = d.toTimeString().slice(0, 8);
  if (sameDay) return time;
  return `${d.toISOString().slice(5, 10)} ${time}`;
}

function parseMeta(raw) {
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

const EVENT_LABELS = {
  daemon_boot:         { label: 'Daemon booted',     color: '#5cff9e', icon: '▶' },
  daemon_shutdown:     { label: 'Daemon stopped',    color: '#ff6b6b', icon: '■' },
  service_start:       { label: 'Service started',   color: '#5cff9e', icon: '▶' },
  service_stop:        { label: 'Service stopped',   color: '#ff6b6b', icon: '■' },
  service_state:       { label: 'State changed',     color: '#ffd966', icon: '↻' },
  pid_change:          { label: 'Process restarted', color: '#ffd966', icon: '↻' },
  version_change:      { label: 'Version deployed',  color: '#8caaff', icon: '⇧' },
  llm_reachable:       { label: 'LLM reachable',     color: '#5cff9e', icon: '✓' },
  llm_unreachable:     { label: 'LLM unreachable',   color: '#ff6b6b', icon: '✗' },
  whisper_loaded:      { label: 'Whisper loaded',    color: '#5cff9e', icon: '✓' },
  whisper_unloaded:    { label: 'Whisper unloaded',  color: '#8a93a8', icon: '○' },
  tts_loaded:          { label: 'TTS loaded',        color: '#5cff9e', icon: '✓' },
  tts_unloaded:        { label: 'TTS unloaded',      color: '#8a93a8', icon: '○' },
};

function describeEvent(ev) {
  const meta = EVENT_LABELS[ev.kind] || { label: ev.kind, color: '#8a93a8', icon: '•' };
  const m = parseMeta(ev.meta_json) || {};
  let detail = '';
  if (ev.kind === 'daemon_boot' || ev.kind === 'daemon_shutdown') {
    detail = ev.target ? `v${ev.target}` : '';
    if (m.pid) detail += detail ? ` · pid ${m.pid}` : `pid ${m.pid}`;
  } else if (ev.kind === 'service_state') {
    detail = m.from && m.to ? `${m.from} → ${m.to}` : (m.to || '');
  } else if (ev.kind === 'pid_change') {
    detail = m.from && m.to ? `${m.from} → ${m.to}` : (m.to ? `pid ${m.to}` : '');
  } else if (ev.kind === 'version_change') {
    detail = m.from && m.to ? `v${m.from} → v${m.to}` : (m.to ? `v${m.to}` : '');
  }
  return { ...meta, detail };
}

function loadLocalEvents() {
  try {
    const raw = localStorage.getItem(LOCAL_STORAGE_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch { return []; }
}

function saveLocalEvents(events) {
  try {
    localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(events.slice(0, MAX_EVENTS)));
  } catch { /* quota or perms */ }
}

export function DaemonPanel() {
  const [svc, setSvc] = useState({ state: 'unknown', pid: 0 });
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(false);
  const [autoRestart, setAutoRestart] = useState(null);
  const [events, setEvents] = useState(() => loadLocalEvents());
  // Track previous observed values to synthesize transition events
  const prevObs = useRef({ state: null, pid: null, version: null, llm: null, whisper: null, tts: null });

  const pushLocalEvent = useCallback((kind, meta = {}) => {
    setEvents(prev => {
      const ev = {
        ts_ns: Date.now() * 1_000_000,
        kind,
        target: '',
        meta_json: JSON.stringify(meta),
        _local: true,
      };
      const next = [ev, ...prev].slice(0, MAX_EVENTS);
      saveLocalEvents(next);
      return next;
    });
  }, []);

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
    (async () => { try { setAutoRestart(await b.nssmGet()); } catch { setAutoRestart(null); } })();
  }, []);

  // Detect client-observed transitions from polled state
  useEffect(() => {
    const p = prevObs.current;
    const state = svc.state;
    const pid = svc.pid || 0;
    const version = health?.daemon_version || null;
    const llm = health?.llm_reachable ?? null;
    const whisper = health?.whisper_loaded ?? null;
    const tts = health?.tts_loaded ?? null;

    if (p.state !== null && state !== 'unknown' && p.state !== state) {
      if (state === 'running') pushLocalEvent('service_start', { from: p.state, to: state });
      else if (p.state === 'running') pushLocalEvent('service_stop', { from: p.state, to: state });
      else pushLocalEvent('service_state', { from: p.state, to: state });
    }
    if (p.pid !== null && pid && p.pid && pid !== p.pid && state === 'running') {
      pushLocalEvent('pid_change', { from: p.pid, to: pid });
    }
    if (p.version !== null && version && p.version && version !== p.version) {
      pushLocalEvent('version_change', { from: p.version, to: version });
    }
    if (p.llm !== null && llm !== null && p.llm !== llm) {
      pushLocalEvent(llm ? 'llm_reachable' : 'llm_unreachable');
    }
    if (p.whisper !== null && whisper !== null && p.whisper !== whisper) {
      pushLocalEvent(whisper ? 'whisper_loaded' : 'whisper_unloaded');
    }
    if (p.tts !== null && tts !== null && p.tts !== tts) {
      pushLocalEvent(tts ? 'tts_loaded' : 'tts_unloaded');
    }

    prevObs.current = { state, pid, version, llm, whisper, tts };
  }, [svc.state, svc.pid, health, pushLocalEvent]);

  // Subscribe to analytics event stream for daemon_* events (persisted server-side)
  useEffect(() => {
    let cancelled = false;
    let iv = null;
    const ingest = (batch) => {
      if (!Array.isArray(batch) || !batch.length) return;
      const daemonEvents = batch.filter(e => DAEMON_KINDS.has(e.kind));
      if (!daemonEvents.length) return;
      setEvents(prev => {
        // dedupe by (ts_ns, kind)
        const keys = new Set(prev.map(e => `${e.ts_ns}:${e.kind}`));
        const fresh = daemonEvents.filter(e => !keys.has(`${e.ts_ns}:${e.kind}`));
        if (!fresh.length) return prev;
        const next = [...fresh, ...prev]
          .sort((a, b) => b.ts_ns - a.ts_ns)
          .slice(0, MAX_EVENTS);
        saveLocalEvents(next);
        return next;
      });
    };
    (async () => {
      try {
        const back = await b.backlogEvents(100);
        if (!cancelled) ingest(back);
      } catch {}
      iv = setInterval(async () => {
        try {
          const batch = await b.popEventBatch(100);
          if (!cancelled) ingest(batch);
        } catch {}
      }, EVENT_POLL_MS);
    })();
    return () => { cancelled = true; if (iv) clearInterval(iv); };
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
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 10 }}>
        {[
          { label: 'Uptime', value: running ? fmtUptime(health?.uptime_seconds) : '—', mono: true },
          { label: 'Memory', value: running && health?.rss_bytes ? (health.rss_bytes / (1024 * 1024)).toFixed(0) : '—', unit: running && health?.rss_bytes ? 'MB' : '' },
          { label: 'CPU',    value: running && (health?.cpu_percent ?? null) !== null ? Number(health.cpu_percent).toFixed(1) : '—', unit: running && (health?.cpu_percent ?? null) !== null ? '%' : '' },
          { label: 'Guilds', value: running ? String(health?.guild_count ?? 0) : '—', mono: true },
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
              {s.label}
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

      {/* Event log */}
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div style={{
          fontSize: 10, color: T.dim, textTransform: 'uppercase',
          letterSpacing: '0.09em', marginBottom: 8,
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span>Event Log</span>
          <span style={{ color: T.sub, textTransform: 'none', letterSpacing: 0, fontSize: 10 }}>
            {events.length > 0 && `· ${events.length}`}
          </span>
          <div style={{ flex: 1 }} />
          {events.length > 0 && (
            <button
              onClick={() => { setEvents([]); saveLocalEvents([]); }}
              title="Clear local event history (does not affect daemon-side analytics)"
              style={{
                background: 'transparent', border: `1px solid ${T.border}`,
                color: T.dim, fontSize: 9, padding: '3px 8px', borderRadius: 4,
                cursor: 'pointer', fontFamily: 'JetBrains Mono', letterSpacing: '0.05em',
                textTransform: 'uppercase',
              }}>clear</button>
          )}
        </div>
        <div style={{
          background: T.surface, border: `1px solid ${T.border}`,
          borderRadius: 9, overflow: 'hidden', maxHeight: 280, overflowY: 'auto',
        }}>
          {events.length === 0 ? (
            <div style={{
              padding: '16px 14px', fontSize: 12, color: T.dim,
              fontFamily: 'DM Sans', fontStyle: 'italic',
            }}>
              No events yet. State transitions and daemon boots are recorded here.
            </div>
          ) : events.map((ev, i) => {
            const info = describeEvent(ev);
            return (
              <div key={`${ev.ts_ns}-${ev.kind}-${i}`} style={{
                display: 'grid', gridTemplateColumns: '20px 130px 1fr auto',
                alignItems: 'center', gap: 10, padding: '7px 12px',
                borderBottom: i < events.length - 1 ? `1px solid ${T.border}` : 'none',
                fontSize: 11,
              }}>
                <span style={{
                  color: info.color, fontFamily: 'JetBrains Mono',
                  fontSize: 12, textAlign: 'center',
                }}>{info.icon}</span>
                <span style={{
                  color: info.color, fontWeight: 500,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>{info.label}</span>
                <span style={{
                  color: T.sub, fontFamily: 'JetBrains Mono', fontSize: 10,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>{info.detail}</span>
                <span style={{
                  color: T.dim, fontFamily: 'JetBrains Mono', fontSize: 10,
                  whiteSpace: 'nowrap',
                }}>
                  {fmtEventTime(ev.ts_ns)}
                  {ev._local && <span title="client-observed" style={{ marginLeft: 6, opacity: 0.6 }}>◯</span>}
                </span>
              </div>
            );
          })}
        </div>
      </div>

    </div>
  );
}
