import { T } from './tokens.js';
import { IS_SNAPSHOT, SNAPSHOT_META } from './bridge.js';

function fmtAge(iso) {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function SnapshotBanner() {
  if (!IS_SNAPSHOT || !SNAPSHOT_META) return null;
  const ts = SNAPSHOT_META.generated_at_utc;
  return (
    <div style={{
      padding: '6px 14px', background: `${T.cyan}14`,
      borderBottom: `1px solid ${T.cyan}55`,
      fontFamily: 'JetBrains Mono', fontSize: 11, color: T.cyan,
      display: 'flex', gap: 12, alignItems: 'center',
    }}>
      <span style={{ fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
        Static snapshot
      </span>
      <span style={{ color: T.text }}>generated {ts || 'unknown'}</span>
      {ts && <span style={{ color: T.dim }}>· {fmtAge(ts)}</span>}
      <span style={{ flex: 1 }} />
      <span style={{ color: T.dim, fontSize: 10 }}>
        live data is frozen — re-run /halbot-stats for a new snapshot
      </span>
    </div>
  );
}
