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
