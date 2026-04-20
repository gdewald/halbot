import { T } from '../tokens.js';
import { NAV_ITEMS } from './navItems.jsx';

export function TopTabBar({ active, onChange }) {
  return (
    <div style={{
      height: 40, flexShrink: 0, background: T.surface,
      borderBottom: `1px solid ${T.border}`,
      display: 'flex', alignItems: 'center', paddingLeft: 12, gap: 2,
    }}>
      {NAV_ITEMS.map(n => {
        const a = active === n.id;
        return (
          <button key={n.id} onClick={() => onChange(n.id)} style={{
            display: 'flex', alignItems: 'center', gap: 7,
            height: 32, padding: '0 14px', borderRadius: 6, border: 'none', cursor: 'pointer',
            background: a ? `${T.blurple}1a` : 'transparent',
            color: a ? T.blurple : T.sub, fontSize: 12, fontWeight: a ? 600 : 400,
            transition: 'all 0.12s', position: 'relative',
          }}>
            {n.icon(a)}{n.label}
            {a && <div style={{
              position: 'absolute', bottom: 0, left: '50%', transform: 'translateX(-50%)',
              width: 24, height: 2, borderRadius: '2px 2px 0 0', background: T.blurple,
            }} />}
          </button>
        );
      })}
    </div>
  );
}
