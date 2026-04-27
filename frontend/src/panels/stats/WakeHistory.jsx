import { useEffect, useState } from 'react';
import { T } from '../../tokens.js';
import { b, IS_SNAPSHOT } from '../../bridge.js';

const REFRESH_MS = 15_000;

function fmtTime(unix) {
  if (!unix) return '—';
  const d = new Date(unix * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  if (sameDay) return `${hh}:${mm}:${ss}`;
  return `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}`;
}

const OUTCOME_LABEL = {
  no_match: 'no match',
  matched: 'matched',
};
function fmtOutcome(o) { return OUTCOME_LABEL[o] || o || '—'; }

export function WakeHistory() {
  const [rows, setRows] = useState([]);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const r = await b.wakeHistory(25);
        if (!cancelled) {
          setRows(Array.isArray(r) ? r : []);
          setLoaded(true);
        }
      } catch {
        if (!cancelled) setLoaded(true);
      }
    };
    refresh();
    if (IS_SNAPSHOT) return () => { cancelled = true; };
    const iv = setInterval(refresh, REFRESH_MS);
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  if (!loaded || rows.length === 0) return null;

  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`,
      borderRadius: 9, overflow: 'hidden', marginBottom: 18,
    }}>
      <div style={{
        padding: '7px 14px', borderBottom: `1px solid ${T.border}`,
        display: 'grid', gridTemplateColumns: '90px 1fr 140px', gap: 8,
        fontSize: 9, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em',
      }}>
        <span>Time</span><span>Phrase</span><span>Outcome</span>
      </div>
      {rows.map((w, i) => (
        <div key={`${w.ts}-${i}`} style={{
          display: 'grid', gridTemplateColumns: '90px 1fr 140px',
          alignItems: 'center', gap: 8, padding: '7px 14px',
          borderBottom: i < rows.length - 1 ? `1px solid ${T.border}` : 'none',
        }}>
          <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: T.dim }}>{fmtTime(w.ts)}</span>
          <span style={{
            fontSize: 12, color: T.text,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            fontStyle: w.phrase ? 'normal' : 'italic',
          }}>{w.phrase || '(no transcript)'}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
            <div style={{
              width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
              background: w.ok ? T.green : T.yellow,
            }} />
            <span style={{
              fontSize: 11, color: w.ok ? T.text : T.yellow,
              fontFamily: 'JetBrains Mono',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>{fmtOutcome(w.outcome)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
