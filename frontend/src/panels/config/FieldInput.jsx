import { T } from '../../tokens.js';

const inputBase = {
  background: T.panel, border: `1px solid ${T.border2}`, borderRadius: 6,
  color: T.text, fontSize: 12, outline: 'none',
  fontFamily: 'JetBrains Mono', transition: 'border-color 0.15s',
};

export function FieldInput({ field, onChange }) {
  const { type, draft } = field;

  const focus = e => { e.target.style.borderColor = T.blurple; };
  const blur  = e => { e.target.style.borderColor = 'rgba(255,255,255,0.11)'; };

  if (type === 'BOOL') {
    const val = String(draft).toLowerCase() === 'true';
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div onClick={() => onChange(val ? 'false' : 'true')} style={{
          width: 34, height: 18, borderRadius: 9, cursor: 'pointer',
          background: val ? T.blurple : 'rgba(255,255,255,0.12)',
          transition: 'background 0.2s', position: 'relative', flexShrink: 0,
        }}>
          <div style={{
            position: 'absolute', top: 2, left: val ? 16 : 2, width: 14, height: 14,
            borderRadius: '50%', background: '#fff', transition: 'left 0.2s',
            boxShadow: '0 1px 3px rgba(0,0,0,0.4)',
          }} />
        </div>
        <span style={{ fontSize: 12, color: val ? T.green : T.dim }}>
          {val ? 'enabled' : 'disabled'}
        </span>
      </div>
    );
  }

  if (type === 'SELECT') {
    return (
      <select value={draft} onChange={e => onChange(e.target.value)}
        onFocus={focus} onBlur={blur}
        style={{ ...inputBase, padding: '5px 8px', height: 30, cursor: 'pointer' }}>
        {(field.options || []).map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  }

  if (type === 'RANGE') {
    const min = field.min, max = field.max, step = field.step || 0.01;
    const n = Number(draft);
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <input type="range" min={min} max={max} step={step} value={Number.isFinite(n) ? n : min}
          onChange={e => onChange(e.target.value)}
          style={{ flex: 1, accentColor: T.blurple, height: 4 }} />
        <span style={{
          fontFamily: 'JetBrains Mono', fontSize: 12, color: T.text, minWidth: 56, textAlign: 'right',
        }}>{Number.isFinite(n) ? n.toFixed(step < 0.1 ? 3 : 2) : '—'}</span>
      </div>
    );
  }

  if (type === 'NUMBER') {
    return (
      <input type="number" value={draft} onChange={e => onChange(e.target.value)}
        min={field.min || undefined} max={field.max || undefined} step={field.step || 1}
        onFocus={focus} onBlur={blur}
        style={{ ...inputBase, padding: '5px 9px', height: 30, width: 140 }} />
    );
  }

  // STRING, URL, or unknown → text input
  return (
    <input value={draft} onChange={e => onChange(e.target.value)}
      onFocus={focus} onBlur={blur}
      style={{ ...inputBase, padding: '5px 9px', height: 30, width: '100%', maxWidth: 360 }} />
  );
}
