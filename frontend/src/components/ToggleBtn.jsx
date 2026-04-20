import { T } from '../tokens.js';

export function ToggleBtn({ active, onClick, label, accent }) {
  const ac = accent || T.blurple;
  return (
    <button onClick={onClick} style={{
      height: 26, padding: '0 9px', borderRadius: 5,
      border: `1px solid ${active ? ac : T.border}`,
      background: active ? `${ac}18` : 'transparent',
      color: active ? ac : T.dim, fontSize: 10, cursor: 'pointer',
      fontFamily: 'DM Sans', fontWeight: 500, transition: 'all 0.1s',
      display: 'flex', alignItems: 'center', gap: 5,
    }}>
      <span style={{ fontSize: 8 }}>●</span>{label}
    </button>
  );
}
