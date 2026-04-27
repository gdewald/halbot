import { useState } from 'react';
import { T } from '../../tokens.js';

// Source-of-truth list of metrics-not-yet-emitted. Cards in Stats.jsx that map
// to these keys render `—` for missing values; this drawer is the canonical
// place to learn why a card is empty and what event/field would fill it.
//
// As of 2026-04-27 the drawer is empty: implementable metrics were wired
// (STT chunk decode, utterance length, LLM tokens/throughput/context/
// timeouts, voice session seconds) and architecture-incompatible ones
// were dropped (TTFT — non-streaming Ollama; engine fallback — single TTS
// engine; first audio chunk — blocking synth; wake join latency — bot
// joins before listening starts). Add new entries here when emitting a
// stat that the UI shows but the daemon doesn't yet populate.
const GROUPS = [];

export function MissingDataDrawer() {
  const [open, setOpen] = useState(false);
  const total = GROUPS.reduce((n, g) => n + g.items.length, 0);
  if (total === 0) return null;

  return (
    <div style={{
      marginTop: 6, border: `1px solid ${T.border}`, borderRadius: 9,
      background: T.surface, overflow: 'hidden',
    }}>
      <button onClick={() => setOpen(!open)} style={{
        width: '100%', display: 'flex', alignItems: 'center', gap: 10,
        padding: '10px 14px', background: 'transparent', border: 'none',
        cursor: 'pointer', textAlign: 'left',
      }}>
        <span style={{
          width: 18, height: 18, display: 'inline-flex',
          alignItems: 'center', justifyContent: 'center',
          color: T.sub, fontSize: 10,
          transform: open ? 'rotate(90deg)' : 'rotate(0deg)',
          transition: 'transform 0.15s',
        }}>▶</span>
        <span style={{ fontSize: 11, fontWeight: 600, color: T.text, letterSpacing: '0.04em' }}>
          Missing data
        </span>
        <span style={{
          fontSize: 10, color: T.dim, fontFamily: 'JetBrains Mono',
          background: 'rgba(255,255,255,0.05)', border: `1px solid ${T.border}`,
          borderRadius: 10, padding: '1px 7px',
        }}>
          {total} metric{total !== 1 ? 's' : ''}
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: T.dim, fontStyle: 'italic', fontFamily: 'JetBrains Mono' }}>
          {open ? 'click to collapse' : "metrics the daemon doesn't emit yet"}
        </span>
      </button>
      {open && (
        <div style={{
          padding: '4px 14px 14px', borderTop: `1px solid ${T.border}`,
          animation: 'fadeIn 0.18s ease',
        }}>
          <div style={{
            fontSize: 11, color: T.sub, lineHeight: 1.5, margin: '10px 2px 14px',
            padding: '8px 10px', background: 'rgba(250,166,26,0.06)',
            border: `1px solid ${T.yellow}28`, borderRadius: 6,
          }}>
            <span style={{ color: T.yellow, fontWeight: 600, fontFamily: 'JetBrains Mono', marginRight: 6 }}>!</span>
            Cards above marked with <code style={{ fontFamily: 'JetBrains Mono', color: T.dim }}>—</code> render
            real numbers once the daemon emits the event keys listed below.
            No UI changes needed.
          </div>
          {GROUPS.map(g => (
            <div key={g.label} style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                <span style={{ fontSize: 12 }}>{g.icon}</span>
                <span style={{
                  fontSize: 10, fontWeight: 600, color: T.text,
                  letterSpacing: '0.08em', textTransform: 'uppercase',
                }}>{g.label}</span>
                <div style={{ flex: 1, height: 1, background: T.border }} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: 6 }}>
                {g.items.map(it => (
                  <div key={it.label} style={{
                    display: 'flex', alignItems: 'center', gap: 10, padding: '7px 10px',
                    background: T.panel, border: `1px dashed ${T.border2}`,
                    borderRadius: 6, opacity: 0.85,
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 11, color: T.text, fontWeight: 500,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>
                        {it.label}
                        {it.unit && <span style={{ color: T.dim, marginLeft: 4 }}>· {it.unit}</span>}
                      </div>
                      <div style={{
                        fontSize: 9.5, color: T.sub, marginTop: 2,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        fontFamily: 'JetBrains Mono',
                      }}>
                        {it.why}
                      </div>
                    </div>
                    <span style={{
                      fontSize: 9, color: g.accent, fontFamily: 'JetBrains Mono',
                      background: `${g.accent}14`, border: `1px solid ${g.accent}30`,
                      borderRadius: 3, padding: '1px 5px', whiteSpace: 'nowrap',
                    }}>
                      {it.source}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
