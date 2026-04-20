import { T } from '../tokens.js';
import { b } from '../bridge.js';

export function WinTitleBar({ title, subtitle }) {
  const buttons = [
    { label: '─', hov: 'rgba(255,255,255,0.08)', act: () => b.minimize() },
    { label: '□', hov: 'rgba(255,255,255,0.08)', act: () => b.maximize() },
    { label: '✕', hov: '#c42b1c',               act: () => b.close() },
  ];
  return (
    <div style={{
      height: 32, flexShrink: 0, background: T.surface,
      borderBottom: `1px solid ${T.border}`, display: 'flex',
      alignItems: 'center', paddingLeft: 12, paddingRight: 0,
      userSelect: 'none', WebkitAppRegion: 'drag',
    }}>
      <div style={{
        width: 16, height: 16, borderRadius: 4,
        background: `linear-gradient(135deg,${T.blurple},${T.blurpleL})`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 9, fontWeight: 600, color: '#fff', marginRight: 8, flexShrink: 0,
      }}>H</div>
      <span style={{ fontSize: 12, fontWeight: 600, color: T.text, marginRight: 6 }}>{title}</span>
      {subtitle && <span style={{ fontSize: 10, color: T.dim, fontFamily: 'JetBrains Mono' }}>{subtitle}</span>}
      <div style={{ flex: 1 }} />
      {buttons.map((btn, i) => (
        <button key={i} onClick={btn.act} style={{
          width: 46, height: 32, border: 'none', background: 'transparent',
          color: T.sub, fontSize: i === 2 ? 12 : 11, cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          transition: 'background 0.1s', WebkitAppRegion: 'no-drag',
        }}
          onMouseEnter={e => e.currentTarget.style.background = btn.hov}
          onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
        >{btn.label}</button>
      ))}
    </div>
  );
}
