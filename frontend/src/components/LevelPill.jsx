import { T, LC, LB } from '../tokens.js';

export function LevelPill({ level }) {
  return (
    <span style={{
      fontFamily: 'JetBrains Mono', fontSize: 9, fontWeight: 600,
      color: LC[level] || T.dim, background: LB[level] || 'transparent',
      border: `1px solid ${LC[level] || T.dim}28`, borderRadius: 3,
      padding: '1px 5px', letterSpacing: '0.06em', whiteSpace: 'nowrap',
    }}>{level}</span>
  );
}
