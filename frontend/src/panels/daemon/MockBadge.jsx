import { T } from '../../tokens.js';

export function MockBadge({ label = 'mock', title }) {
  return (
    <span title={title || 'not yet implemented — mocked for design review'}
      style={{
        fontFamily: 'JetBrains Mono', fontSize: 9, fontWeight: 600,
        color: T.yellow, background: `${T.yellow}14`,
        border: `1px solid ${T.yellow}35`,
        borderRadius: 3, padding: '1px 5px',
        letterSpacing: '0.06em', textTransform: 'uppercase',
      }}>{label}</span>
  );
}
