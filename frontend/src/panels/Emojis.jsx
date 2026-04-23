import { useEffect, useMemo, useState } from 'react';
import { T } from '../tokens.js';
import { b } from '../bridge.js';

const REFRESH_MS = 30_000;

function fmtBytes(n) {
  if (!n) return '0 B';
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}

export function EmojisPanel() {
  const [emojis, setEmojis] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [query, setQuery] = useState('');

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const list = await b.emojiList();
        if (cancelled) return;
        setEmojis(Array.isArray(list) ? list : []);
        setLoaded(true);
      } catch {
        if (!cancelled) setLoaded(true);
      }
    };
    refresh();
    const t = setInterval(refresh, REFRESH_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return emojis;
    return emojis.filter(e =>
      (e.name || '').toLowerCase().includes(q) ||
      (e.description || '').toLowerCase().includes(q)
    );
  }, [emojis, query]);

  const totalBytes = useMemo(
    () => emojis.reduce((s, e) => s + (e.size_bytes || 0), 0),
    [emojis]
  );
  const animatedCount = useMemo(
    () => emojis.filter(e => e.animated).length,
    [emojis]
  );

  return (
    <div style={{
      height: '100%', display: 'flex', flexDirection: 'column',
      background: T.bg, color: T.text,
    }}>
      {/* Header */}
      <div style={{
        padding: '12px 16px 10px', borderBottom: `1px solid ${T.border}`,
        display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Custom emojis</div>
          <div style={{ fontSize: 10, color: T.dim, fontFamily: 'JetBrains Mono' }}>
            {loaded ? (
              `${emojis.length} total · ${animatedCount} animated · ${fmtBytes(totalBytes)}`
            ) : 'loading…'}
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <input
          type="text" placeholder="filter by name or description"
          value={query} onChange={e => setQuery(e.target.value)}
          style={{
            background: T.surface, color: T.text, border: `1px solid ${T.border}`,
            borderRadius: 6, padding: '6px 10px', fontSize: 11,
            fontFamily: 'JetBrains Mono', minWidth: 240, outline: 'none',
          }}
        />
      </div>

      {/* Grid */}
      <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
        {loaded && filtered.length === 0 && (
          <div style={{
            color: T.dim, fontSize: 12, textAlign: 'center', padding: '40px 0',
          }}>
            {emojis.length === 0
              ? 'No emojis synced yet. The bot syncs custom emojis when it connects to Discord.'
              : 'No emojis match the filter.'}
          </div>
        )}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
          gap: 10,
        }}>
          {filtered.map(e => (
            <div key={e.emoji_id} title={e.description || e.name} style={{
              background: T.surface, border: `1px solid ${T.border}`,
              borderRadius: 8, padding: 10,
              display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6,
              position: 'relative',
            }}>
              {e.animated && (
                <div style={{
                  position: 'absolute', top: 4, right: 4,
                  fontSize: 8, color: T.blurple, fontFamily: 'JetBrains Mono',
                  background: `${T.blurple}1a`, padding: '1px 4px', borderRadius: 3,
                  letterSpacing: '0.05em',
                }}>GIF</div>
              )}
              <div style={{
                width: 56, height: 56, display: 'flex',
                alignItems: 'center', justifyContent: 'center',
                background: T.bg, borderRadius: 6,
              }}>
                {e.image_data_url ? (
                  <img src={e.image_data_url} alt={e.name}
                       style={{ maxWidth: 48, maxHeight: 48, imageRendering: 'auto' }} />
                ) : (
                  <div style={{ color: T.dim, fontSize: 10 }}>no img</div>
                )}
              </div>
              <div style={{
                fontSize: 10, fontFamily: 'JetBrains Mono',
                color: T.text, textAlign: 'center', wordBreak: 'break-all',
                lineHeight: 1.3,
              }}>{e.name}</div>
              {e.description && (
                <div style={{
                  fontSize: 9, color: T.dim, textAlign: 'center', lineHeight: 1.3,
                  maxHeight: 24, overflow: 'hidden', textOverflow: 'ellipsis',
                }}>{e.description}</div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
