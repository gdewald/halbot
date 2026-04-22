import { useEffect, useMemo, useRef, useState } from 'react';
import { T } from '../tokens.js';
import { b } from '../bridge.js';

const WINDOW_DAYS = 30;
const MAX_FEED = 200;

// kind → color / emoji. Keep list small, fall back to blurple/🗒.
const KIND_META = {
  soundboard_play: { c: T.blurple, e: '🔊' },
  cmd_invoke:      { c: T.cyan,    e: '⌨' },
  voice_join:      { c: T.green,   e: '🎙' },
  llm_call:        { c: T.yellow,  e: '🧠' },
  tts_request:     { c: T.blurpleL,e: '🗣' },
  mention:         { c: T.sub,     e: '@'  },
};

function fmtRelative(unix) {
  if (!unix) return '—';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - unix));
  if (s < 60)   return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  if (s < 86400) return `${Math.floor(s/3600)}h ago`;
  return `${Math.floor(s/86400)}d ago`;
}

function fmtTime(ns) {
  const d = new Date(Math.floor(ns / 1_000_000));
  return d.toLocaleTimeString([], { hour12: false });
}

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{
        fontSize: 10, fontWeight: 600, color: T.dim, letterSpacing: '0.1em',
        textTransform: 'uppercase', padding: '0 2px', marginBottom: 8,
      }}>{title}</div>
      {children}
    </div>
  );
}

function BarRow({ rank, label, count, max, last, accent, mono }) {
  const pct = max > 0 ? Math.max(4, (count / max) * 100) : 0;
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '26px 1fr 90px 72px',
      alignItems: 'center', gap: 8, padding: '6px 12px',
      borderBottom: `1px solid ${T.border}`,
    }}>
      <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: T.dim, textAlign: 'center' }}>#{rank}</span>
      <span style={{
        fontFamily: mono ? 'JetBrains Mono' : 'DM Sans',
        fontSize: 12, color: T.text, overflow: 'hidden',
        textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{label}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <div style={{
          flex: 1, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.06)', overflow: 'hidden',
        }}>
          <div style={{ width: `${pct}%`, height: '100%', background: accent }} />
        </div>
        <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: T.text, minWidth: 32, textAlign: 'right' }}>{count}</span>
      </div>
      <span style={{ fontSize: 10, color: T.sub, textAlign: 'right' }}>{fmtRelative(last)}</span>
    </div>
  );
}

function EmptyOverlay({ message }) {
  return (
    <div style={{
      position: 'absolute', inset: 0, display: 'flex',
      alignItems: 'center', justifyContent: 'center',
      background: 'rgba(12,12,15,0.45)', backdropFilter: 'blur(2px)',
      WebkitBackdropFilter: 'blur(2px)', pointerEvents: 'none',
    }}>
      <div style={{
        background: T.raised, border: `1px solid ${T.yellow}35`,
        borderRadius: 12, padding: '20px 24px', maxWidth: 520, textAlign: 'center',
        boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
      }}>
        <div style={{
          fontSize: 10, fontWeight: 600, color: T.yellow,
          textTransform: 'uppercase', letterSpacing: '0.12em', marginBottom: 8,
        }}>No events yet</div>
        <div style={{ fontSize: 13, color: T.text, lineHeight: 1.5 }}>
          {message}
        </div>
      </div>
    </div>
  );
}

export function AnalyticsPanel() {
  const [topSounds, setTopSounds] = useState({ total: 0, rows: [] });
  const [topUsers,  setTopUsers]  = useState({ total: 0, rows: [] });
  const [kindMix,   setKindMix]   = useState({ total: 0, rows: [] });
  const [feed,      setFeed]      = useState([]);
  const [loaded,    setLoaded]    = useState(false);

  const feedRef = useRef([]);
  const scrollerRef = useRef(null);

  const tsFrom = useMemo(
    () => Math.floor(Date.now() / 1000) - WINDOW_DAYS * 86400,
    []
  );

  // Initial + periodic aggregate refresh.
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const [s, u, k] = await Promise.all([
          b.queryStats('soundboard_play', 0, '', tsFrom, 0, 'target', 20),
          b.queryStats('', 0, '', tsFrom, 0, 'user_id', 20),
          b.queryStats('', 0, '', tsFrom, 0, 'kind', 10),
        ]);
        if (cancelled) return;
        setTopSounds({ total: s.total_count || 0, rows: s.rows || [] });
        setTopUsers( { total: u.total_count || 0, rows: u.rows || [] });
        setKindMix(  { total: k.total_count || 0, rows: k.rows || [] });
        setLoaded(true);
      } catch {
        setLoaded(true);
      }
    };
    refresh();
    const iv = setInterval(refresh, 10_000);
    return () => { cancelled = true; clearInterval(iv); };
  }, [tsFrom]);

  // Live feed: backlog once, then poll pop_event_batch.
  useEffect(() => {
    let cancelled = false;
    let iv;
    (async () => {
      try {
        const back = await b.backlogEvents(50);
        if (!cancelled && Array.isArray(back) && back.length) {
          feedRef.current = back.slice(-MAX_FEED);
          setFeed(feedRef.current.slice().reverse());
        }
      } catch { /* stub */ }
      iv = setInterval(async () => {
        try {
          const batch = await b.popEventBatch(100);
          if (!cancelled && Array.isArray(batch) && batch.length) {
            feedRef.current = feedRef.current.concat(batch).slice(-MAX_FEED);
            setFeed(feedRef.current.slice().reverse());
          }
        } catch { /* stub */ }
      }, 500);
    })();
    return () => { cancelled = true; if (iv) clearInterval(iv); };
  }, []);

  const maxSound = Math.max(1, ...topSounds.rows.map(r => r.count));
  const maxUser  = Math.max(1, ...topUsers.rows.map(r => r.count));
  const totalEvents = kindMix.total;

  const empty = loaded && totalEvents === 0;

  return (
    <div style={{ position: 'relative', height: '100%', animation: 'fadeIn 0.15s ease' }}>
      <div style={{
        height: '100%', overflow: 'auto', padding: '16px',
        filter: empty ? 'blur(2px) saturate(0.7) opacity(0.6)' : 'none',
        pointerEvents: empty ? 'none' : 'auto',
      }}>

        {/* Summary strip */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 18 }}>
          <Summary label="Events (30d)" value={totalEvents} accent={T.blurple} />
          <Summary label="Soundboard plays" value={topSounds.total} accent={T.blurple} />
          <Summary label="Unique users" value={topUsers.rows.length} accent={T.cyan} />
          <Summary label="Event types seen" value={kindMix.rows.length} accent={T.green} />
        </div>

        {/* Event type mix */}
        <Section title="Event type mix — last 30 days">
          <div style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 9, padding: '10px 14px', display: 'flex', flexWrap: 'wrap', gap: 10,
          }}>
            {kindMix.rows.length === 0 ? (
              <span style={{ fontSize: 12, color: T.dim, fontStyle: 'italic' }}>no events</span>
            ) : kindMix.rows.map(r => {
              const meta = KIND_META[r.key] || { c: T.blurple, e: '•' };
              return (
                <div key={r.key} style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  background: `${meta.c}18`, border: `1px solid ${meta.c}35`,
                  borderRadius: 6, padding: '4px 10px',
                }}>
                  <span style={{ fontSize: 12 }}>{meta.e}</span>
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: T.text }}>{r.key}</span>
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: meta.c, fontWeight: 600 }}>{r.count}</span>
                </div>
              );
            })}
          </div>
        </Section>

        {/* Top soundboard */}
        <Section title="Top soundboard plays — last 30 days">
          <div style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 9, overflow: 'hidden',
          }}>
            {topSounds.rows.length === 0 ? (
              <div style={{ padding: '14px', fontSize: 12, color: T.dim, fontStyle: 'italic' }}>no soundboard plays recorded</div>
            ) : topSounds.rows.map((r, i) => (
              <BarRow key={r.key} rank={i + 1} label={r.key || '—'}
                count={r.count} max={maxSound} last={r.last_ts_unix}
                accent={T.blurple} mono />
            ))}
          </div>
        </Section>

        {/* Top users */}
        <Section title="Top users by activity — last 30 days">
          <div style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 9, overflow: 'hidden',
          }}>
            {topUsers.rows.length === 0 ? (
              <div style={{ padding: '14px', fontSize: 12, color: T.dim, fontStyle: 'italic' }}>no user activity recorded</div>
            ) : topUsers.rows.map((r, i) => (
              <BarRow key={r.key} rank={i + 1} label={r.key || '<anonymous>'}
                count={r.count} max={maxUser} last={r.last_ts_unix}
                accent={T.cyan} mono />
            ))}
          </div>
        </Section>

        {/* Live feed */}
        <Section title="Live event feed">
          <div ref={scrollerRef} style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 9, maxHeight: 360, overflow: 'auto',
          }}>
            {feed.length === 0 ? (
              <div style={{ padding: '14px', fontSize: 12, color: T.dim, fontStyle: 'italic' }}>
                waiting for events…
              </div>
            ) : feed.map((ev, i) => {
              const meta = KIND_META[ev.kind] || { c: T.sub, e: '•' };
              return (
                <div key={`${ev.ts_ns}-${i}`} style={{
                  display: 'grid', gridTemplateColumns: '70px 22px 140px 1fr 100px',
                  alignItems: 'center', gap: 8, padding: '5px 12px',
                  borderBottom: i < feed.length - 1 ? `1px solid ${T.border}` : 'none',
                  fontSize: 11,
                }}>
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: T.dim }}>{fmtTime(ev.ts_ns)}</span>
                  <span style={{ textAlign: 'center' }}>{meta.e}</span>
                  <span style={{ fontFamily: 'JetBrains Mono', color: meta.c, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.kind}</span>
                  <span style={{ color: T.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {ev.target || <span style={{ color: T.dim, fontStyle: 'italic' }}>—</span>}
                  </span>
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: T.sub, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {ev.user_id ? `user ${ev.user_id}` : ''}
                  </span>
                </div>
              );
            })}
          </div>
        </Section>

        <div style={{ height: 8 }} />
      </div>

      {empty && (
        <EmptyOverlay message={
          <>
            Analytics storage is live but no Discord events have been recorded yet.
            Emitters (soundboard, commands, voice, LLM) land in phase 2
            when the Discord client returns. Data shown here will be
            retrievable via <code style={{ fontFamily: 'JetBrains Mono', color: T.cyan }}>QueryStats</code> RPC and the upcoming
            <code style={{ fontFamily: 'JetBrains Mono', color: T.cyan }}> /stats</code> Discord command.
          </>
        } />
      )}
    </div>
  );
}

function Summary({ label, value, accent }) {
  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`,
      borderRadius: 9, padding: '12px 14px', position: 'relative', overflow: 'hidden',
    }}>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: `linear-gradient(90deg,${accent},${accent}44)`,
      }} />
      <div style={{ fontSize: 9, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.09em', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color: T.text, fontFamily: 'JetBrains Mono' }}>
        {typeof value === 'number' ? value.toLocaleString() : value}
      </div>
    </div>
  );
}
