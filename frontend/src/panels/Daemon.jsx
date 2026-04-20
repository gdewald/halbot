import { T } from '../tokens.js';

export function DaemonPanel() {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '100%', color: T.dim, fontSize: 12,
    }}>
      Daemon panel — lands in step 5.
    </div>
  );
}
