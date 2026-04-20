import { T } from '../tokens.js';
import { NAV_ITEMS } from './navItems.jsx';

export function SidebarWide({ active, onChange }) {
  return (
    <div style={{
      width: 160, height: '100%', background: T.surface,
      borderRight: `1px solid ${T.border}`,
      flexShrink: 0, display: 'flex', flexDirection: 'column', padding: '8px 8px',
    }}>
      <div style={{ marginBottom: 16, padding: '4px 8px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{
          width: 22, height: 22, borderRadius: 5,
          background: `linear-gradient(135deg,${T.blurple},${T.blurpleL})`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 10, fontWeight: 600, color: '#fff',
        }}>H</div>
        <span style={{ fontSize: 12, fontWeight: 600, color: T.text }}>halbot</span>
      </div>
      <div style={{
        fontSize: 9, fontWeight: 600, color: T.dim, letterSpacing: '0.1em',
        textTransform: 'uppercase', padding: '0 8px', marginBottom: 4,
      }}>Navigation</div>
      {NAV_ITEMS.map(n => {
        const a = active === n.id;
        return (
          <button key={n.id} onClick={() => onChange(n.id)} style={{
            display: 'flex', alignItems: 'center', gap: 9,
            height: 34, padding: '0 10px', borderRadius: 7, border: 'none', cursor: 'pointer',
            background: a ? `${T.blurple}1a` : 'transparent',
            color: a ? T.blurple : T.sub, fontSize: 13, fontWeight: a ? 600 : 400,
            transition: 'all 0.12s', textAlign: 'left', position: 'relative', width: '100%',
          }}>
            {n.icon(a)}
            {n.label}
            {a && <div style={{
              position: 'absolute', left: 0, top: '50%', transform: 'translateY(-50%)',
              width: 2.5, height: 18, borderRadius: '0 2px 2px 0', background: T.blurple,
            }} />}
          </button>
        );
      })}
    </div>
  );
}
