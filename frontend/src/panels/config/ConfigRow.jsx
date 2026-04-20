import { useState } from 'react';
import { T } from '../../tokens.js';
import { FieldInput } from './FieldInput.jsx';

export function ConfigRow({ field, isLast, onChange, onRevert }) {
  const [hov, setHov] = useState(false);
  return (
    <div onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
      style={{
        display: 'grid', gridTemplateColumns: '260px 1fr auto',
        alignItems: 'center', gap: 0,
        borderBottom: isLast ? 'none' : `1px solid ${T.border}`,
        background: field.dirty ? `${T.blurple}06`
          : hov ? 'rgba(255,255,255,0.018)' : 'transparent',
        transition: 'background 0.1s',
      }}>
      <div style={{ padding: '10px 14px', borderRight: `1px solid ${T.border}` }}>
        <div style={{
          fontFamily: 'JetBrains Mono', fontSize: 11, color: T.cyan,
          display: 'flex', alignItems: 'center', gap: 5,
        }}>
          {field.dirty && <span style={{ color: T.blurple, fontSize: 8 }}>●</span>}
          {field.label}
        </div>
        <div style={{ fontSize: 10, color: T.dim, marginTop: 2 }}>{field.description}</div>
      </div>
      <div style={{ padding: '8px 14px' }}>
        <FieldInput field={field} onChange={onChange} />
      </div>
      <div style={{ padding: '0 10px', display: 'flex', alignItems: 'center' }}>
        {field.dirty && (
          <button onClick={onRevert} title="revert" style={{
            width: 24, height: 24, borderRadius: 5,
            border: `1px solid ${T.border2}`,
            background: 'transparent', color: T.yellow, fontSize: 13, cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>↺</button>
        )}
      </div>
    </div>
  );
}
