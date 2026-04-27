import { useEffect, useMemo, useRef, useState } from 'react';
import { T, LC } from '../tokens.js';
import { ToggleBtn } from '../components/ToggleBtn.jsx';
import { LogRow } from './logs/LogRow.jsx';
import { useLogStream } from './logs/useLogStream.js';

const LEVELS = ['DEBUG', 'INFO', 'WARN', 'ERROR'];

function normalizeLevel(l) {
  return l === 'WARNING' ? 'WARN' : l;
}

export function LogsPanel() {
  const { logs, connected, clear } = useLogStream();
  const [enabled, setEnabled] = useState({ DEBUG: true, INFO: true, WARN: true, ERROR: true });
  const [search, setSearch] = useState('');
  const [autoScroll, setAutoScroll] = useState(true);
  const [wrap, setWrap] = useState(false);
  const scrollRef = useRef(null);
  const [viewOffset, setViewOffset] = useState(0);
  const effectiveLogs = useMemo(() => logs.slice(viewOffset), [logs, viewOffset]);

  const visible = useMemo(() => {
    const q = search.toLowerCase();
    return effectiveLogs.filter(l => {
      const lvl = normalizeLevel(l.level);
      if (!enabled[lvl]) return false;
      if (q) return l.message.toLowerCase().includes(q) || (l.source || '').toLowerCase().includes(q);
      return true;
    });
  }, [effectiveLogs, enabled, search]);

  // Pin to bottom whenever logs change (or tail re-enabled). Depends on
  // `logs` reference — not `visible.length` — so it keeps firing after the
  // ring buffer caps at MAX (length plateaus but reference updates per batch).
  // rAF defers scroll until after layout reflects new rows.
  useEffect(() => {
    if (!autoScroll) return;
    const el = scrollRef.current;
    if (!el) return;
    const id = requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
    return () => cancelAnimationFrame(id);
  }, [logs, visible.length, autoScroll]);

  // Auto-disable tail when user scrolls up; auto-enable when back at bottom.
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    if (nearBottom && !autoScroll) setAutoScroll(true);
    else if (!nearBottom && autoScroll) setAutoScroll(false);
  };

  const counts = useMemo(() => {
    const c = { DEBUG: 0, INFO: 0, WARN: 0, ERROR: 0 };
    for (const l of effectiveLogs) {
      const lvl = normalizeLevel(l.level);
      if (c[lvl] !== undefined) c[lvl]++;
    }
    return c;
  }, [effectiveLogs]);

  const onCount = LEVELS.filter(l => enabled[l]).length;
  const allOn = onCount === LEVELS.length;
  const toggle = (l) => setEnabled(e => ({ ...e, [l]: !e[l] }));
  const setAll = (v) => setEnabled(LEVELS.reduce((a, l) => ({ ...a, [l]: v }), {}));

  const onClear = () => setViewOffset(logs.length);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', animation: 'fadeIn 0.15s ease' }}>
      {/* toolbar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '8px 12px',
        borderBottom: `1px solid ${T.border}`, flexShrink: 0, flexWrap: 'wrap', rowGap: 6,
      }}>
        {/* multi-select capsule: ALL master + per-level toggles */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6, background: T.panel,
          border: `1px solid ${T.border}`, borderRadius: 7, padding: 2, height: 28,
        }}>
          <button onClick={() => setAll(!allOn)} title={allOn ? 'deselect all' : 'select all'} style={{
            height: 24, padding: '0 10px', borderRadius: 5, border: 'none', cursor: 'pointer',
            background: allOn ? T.blurple : 'transparent',
            color: allOn ? '#fff' : T.sub,
            fontSize: 10, fontWeight: 600, fontFamily: 'JetBrains Mono', letterSpacing: '0.05em',
            display: 'flex', alignItems: 'center', gap: 5,
          }}>
            ALL
            <span style={{
              fontSize: 9, opacity: 0.85,
              background: allOn ? 'rgba(255,255,255,0.18)' : 'rgba(255,255,255,0.06)',
              borderRadius: 3, padding: '0 4px',
            }}>{onCount}/{LEVELS.length}</span>
          </button>
          <div style={{ width: 1, height: 14, background: T.border2 }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
            {LEVELS.map(f => {
              const a = enabled[f];
              const color = LC[f];
              return (
                <button key={f} onClick={() => toggle(f)} style={{
                  height: 24, padding: '0 10px', borderRadius: 5, border: 'none', cursor: 'pointer',
                  background: a ? `${color}30` : 'transparent',
                  color: a ? color : `${T.sub}88`,
                  fontSize: 10, fontWeight: 600, fontFamily: 'JetBrains Mono', letterSpacing: '0.05em',
                  display: 'flex', alignItems: 'center', gap: 5, transition: 'all 0.12s',
                  opacity: a ? 1 : 0.5,
                  textDecoration: a ? 'none' : 'line-through',
                  textDecorationThickness: '1px',
                  textDecorationColor: 'rgba(255,255,255,0.25)',
                }}>
                  {f}
                  <span style={{
                    fontSize: 9, opacity: a ? 0.85 : 0.6,
                    background: 'rgba(255,255,255,0.06)',
                    borderRadius: 3, padding: '0 4px',
                  }}>{counts[f]}</span>
                </button>
              );
            })}
          </div>
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
      <div ref={scrollRef} onScroll={onScroll} style={{ flex: 1, overflow: 'auto' }}>
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
