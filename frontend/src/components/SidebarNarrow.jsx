import { T } from '../tokens.js';
import { NAV_ITEMS } from './navItems.jsx';

export function SidebarNarrow({ active, onChange }) {
  return (
    <div style={{
      width: 52, height: '100%', background: T.surface,
      borderRight: `1px solid ${T.border}`,
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      paddingTop: 10, gap: 2, flexShrink: 0,
    }}>
      {NAV_ITEMS.map(n => {
        const a = active === n.id;
        return (
          <button key={n.id} onClick={() => onChange(n.id)} title={n.label} style={{
            width: 40, height: 40, borderRadius: 8, border: 'none', cursor: 'pointer',
            background: a ? `${T.blurple}1a` : 'transparent',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            position: 'relative', transition: 'background 0.12s',
          }}>
            {n.icon(a)}
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
