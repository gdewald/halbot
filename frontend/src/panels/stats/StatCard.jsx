import { T } from '../../tokens.js';

export function StatCard({ label, value, unit, sub, accent }) {
  const ac = accent || T.blurple;
  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`,
      borderRadius: 9, padding: '14px 16px',
      position: 'relative', overflow: 'hidden',
    }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: `linear-gradient(90deg,${ac},${ac}44)`,
      }} />
      <div style={{
        fontSize: 9, color: T.dim, textTransform: 'uppercase',
        letterSpacing: '0.09em', marginBottom: 6,
      }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 600, color: T.text, lineHeight: 1 }}>
        {value}
        {unit && <span style={{ fontSize: 12, color: T.sub, fontWeight: 400, marginLeft: 3 }}>{unit}</span>}
      </div>
      {sub && <div style={{ fontSize: 10, color: T.dim, marginTop: 5 }}>{sub}</div>}
    </div>
  );
}

export function MiniBar({ value, max, color }) {
  return (
    <div style={{ flex: 1, height: 4, background: 'rgba(255,255,255,0.07)', borderRadius: 2, overflow: 'hidden' }}>
      <div style={{
        width: `${Math.min(100, (value / max) * 100)}%`, height: '100%',
        background: color || T.blurple, borderRadius: 2, transition: 'width 0.4s ease',
      }} />
    </div>
  );
}

export function SectionHeader({ label, icon }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
      <span style={{ fontSize: 14 }}>{icon}</span>
      <span style={{
        fontSize: 11, fontWeight: 600, color: T.text,
        textTransform: 'uppercase', letterSpacing: '0.07em',
      }}>{label}</span>
      <div style={{ flex: 1, height: 1, background: T.border, marginLeft: 4 }} />
    </div>
  );
}
