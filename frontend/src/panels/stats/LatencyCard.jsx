import { T } from '../../tokens.js';

function LatencyBar({ avg, p95, max, color }) {
  const ac = color || T.blurple;
  return (
    <div style={{
      position: 'relative', height: 6,
      background: 'rgba(255,255,255,0.06)', borderRadius: 3, marginTop: 8,
    }}>
      <div style={{
        position: 'absolute', left: 0, top: 0, bottom: 0,
        width: `${Math.min(100, (avg / max) * 100)}%`,
        background: ac, borderRadius: 3, opacity: 0.9,
      }} />
      <div style={{
        position: 'absolute', top: -2, bottom: -2,
        left: `${Math.min(99, (p95 / max) * 100)}%`,
        width: 2, background: ac, borderRadius: 1, opacity: 0.5,
      }} />
    </div>
  );
}

export function LatencyCard({ label, avg, p95, unit, color, sub }) {
  const ac = color || T.blurple;
  const max = Math.max(p95 * 1.2, avg * 1.2, 1);
  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`,
      borderRadius: 9, padding: '12px 14px',
      position: 'relative', overflow: 'hidden',
    }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: `linear-gradient(90deg,${ac},${ac}44)`,
      }} />
      <div style={{
        fontSize: 9, color: T.dim, textTransform: 'uppercase',
        letterSpacing: '0.09em', marginBottom: 4,
      }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span style={{ fontSize: 22, fontWeight: 600, color: T.text, fontFamily: 'JetBrains Mono' }}>{avg}</span>
        <span style={{ fontSize: 10, color: T.sub }}>{unit} avg</span>
        <span style={{ fontSize: 11, color: T.dim, marginLeft: 'auto', fontFamily: 'JetBrains Mono' }}>
          p95 {p95}{unit}
        </span>
      </div>
      <LatencyBar avg={avg} p95={p95} max={max} color={ac} />
      {sub && <div style={{ fontSize: 9, color: T.dim, marginTop: 5 }}>{sub}</div>}
    </div>
  );
}
