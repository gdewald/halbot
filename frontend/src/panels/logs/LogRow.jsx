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
