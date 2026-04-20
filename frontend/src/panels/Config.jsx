import { T } from '../tokens.js';

export function ConfigPanel() {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '100%', color: T.dim, fontSize: 12,
    }}>
      Config panel — lands in step 6.
    </div>
  );
}
