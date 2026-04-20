import { T } from '../tokens.js';

export function StatsPanel() {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '100%', color: T.dim, fontSize: 12,
    }}>
      Stats panel — lands in step 7.
    </div>
  );
}
