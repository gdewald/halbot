import { T } from '../tokens.js';

export function LogsPanel() {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '100%', color: T.dim, fontSize: 12,
    }}>
      Logs panel — lands in step 4.
    </div>
  );
}
