import { useMemo } from 'react';

const CUSTOM_EMOJI_RE = /^<a?:([A-Za-z0-9_]+):(\d+)>$/;

function parseCustomEmoji(raw) {
  if (!raw) return null;
  const m = CUSTOM_EMOJI_RE.exec(raw);
  if (!m) return null;
  return { name: m[1], id: m[2] };
}

/**
 * Build a (name, id) → emoji-row index from a flat emojiList.
 * Used by EmojiCell to resolve custom-emoji image_data_urls.
 */
export function useEmojiIndex(emojis) {
  return useMemo(() => {
    const byId = new Map();
    const byName = new Map();
    for (const e of emojis || []) {
      if (e?.emoji_id) byId.set(String(e.emoji_id), e);
      if (e?.name) byName.set(e.name, e);
    }
    return { byId, byName };
  }, [emojis]);
}

/**
 * Render a sound's emoji column.
 *  - Empty / missing → bullet placeholder.
 *  - Plain unicode emoji → render as text.
 *  - Custom Discord emoji `<:name:id>` → look up image_data_url in
 *    emojiIndex, render as <img>; falls back to bullet on miss.
 */
export function EmojiCell({ raw, emojiIndex, size = 16 }) {
  if (!raw) return <span>•</span>;
  const parsed = parseCustomEmoji(raw);
  if (!parsed) return <span>{raw}</span>;
  const hit = emojiIndex?.byId?.get(parsed.id) || emojiIndex?.byName?.get(parsed.name);
  if (hit?.image_data_url) {
    return (
      <img
        src={hit.image_data_url}
        alt={parsed.name}
        title={parsed.name}
        style={{ width: size, height: size, objectFit: 'contain', verticalAlign: 'middle' }}
      />
    );
  }
  return <span title={parsed.name}>•</span>;
}
