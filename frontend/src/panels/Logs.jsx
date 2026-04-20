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
  const [viewOffset, setViewOffset] = useState(0);
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
