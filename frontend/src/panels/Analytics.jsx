import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { T } from '../tokens.js';
import { b, IS_SNAPSHOT } from '../bridge.js';

const WINDOWS = [
  { key: '24h', label: '24h', seconds: 86400 },
  { key: '7d',  label: '7 days', seconds: 7 * 86400 },
  { key: '30d', label: '30 days', seconds: 30 * 86400 },
];
const MAX_FEED = 200;

// kind → color / emoji. Fallback blurple/•.
const KIND_META = {
  soundboard_play: { c: T.blurple, e: '🔊' },
  cmd_invoke:      { c: T.cyan,    e: '⌨' },
  voice_join:      { c: T.green,   e: '🎙' },
  llm_call:        { c: T.yellow,  e: '🧠' },
  tts_request:     { c: T.blurpleL,e: '🗣' },
  stt_request:     { c: T.cyan,    e: '👂' },
  mention:         { c: T.sub,     e: '@'  },
};

function kindColor(k) { return (KIND_META[k] || { c: T.sub }).c; }
function kindEmoji(k) { return (KIND_META[k] || { e: '•' }).e; }

function fmtRelative(unix) {
  if (!unix) return '—';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - unix));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  if (s < 86400) return `${Math.floor(s/3600)}h ago`;
  return `${Math.floor(s/86400)}d ago`;
}

function fmtTime(ns) {
  const ms = Math.floor(ns / 1_000_000);
  return new Date(ms).toLocaleTimeString([], { hour12: false });
}

function shortUser(id) {
  if (!id) return '';
  const s = String(id);
  if (s.length <= 6) return s;
  return `…${s.slice(-4)}`;
}

const AVATAR_COLORS = ['#5865F2', '#23d18b', '#faa61a', '#eb459e', '#4fc3f7'];
function avatarColor(s) {
  let h = 0;
  for (let i = 0; i < (s || '').length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}
function avatarLetter(s) {
  const cleaned = String(s || '').replace(/[^a-z0-9]/gi, '');
  return cleaned ? cleaned.charAt(0).toUpperCase() : '?';
}

function parseMeta(raw) {
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

function metaSummary(meta, kind) {
  if (!meta) return '';
  const parts = [];
  if (typeof meta.latency_ms === 'number') parts.push(`${meta.latency_ms}ms`);
  if (typeof meta.bytes === 'number' && meta.bytes > 0) {
    const kb = meta.bytes / 1024;
    parts.push(kb >= 1024 ? `${(kb/1024).toFixed(1)}MB` : `${Math.round(kb)}KB`);
  }
  if (typeof meta.chars === 'number' && kind === 'tts_request') parts.push(`${meta.chars}ch`);
  if (typeof meta.audio_seconds === 'number' && kind === 'stt_request') parts.push(`${meta.audio_seconds}s`);
  if (typeof meta.lock_wait_ms === 'number' && meta.lock_wait_ms > 0) parts.push(`wait=${meta.lock_wait_ms}ms`);
  if (typeof meta.concurrency_peak === 'number' && meta.concurrency_peak > 1) parts.push(`peak=${meta.concurrency_peak}`);
  if (meta.trigger) parts.push(meta.trigger);
  if (meta.source) parts.push(meta.source);
  if (meta.status && meta.status !== 'ok') parts.push(meta.status);
  if (typeof meta.action_count === 'number' && meta.action_count !== 1) parts.push(`×${meta.action_count}`);
  return parts.join(' · ');
}

function Section({ title, right, children }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 10,
        padding: '0 2px', marginBottom: 8,
      }}>
        <span style={{
          fontSize: 10, fontWeight: 600, color: T.dim, letterSpacing: '0.1em',
          textTransform: 'uppercase',
        }}>{title}</span>
        <div style={{ flex: 1, height: 1, background: T.border }} />
        {right}
      </div>
      {children}
    </div>
  );
}

function BarRow({ rank, label, count, max, last, accent, mono, onClick, active, avatar }) {
  const pct = max > 0 ? Math.max(4, (count / max) * 100) : 0;
  const cols = avatar !== undefined ? '26px 22px 1fr 110px 86px' : '26px 1fr 110px 86px';
  return (
    <div
      onClick={onClick}
      style={{
        display: 'grid', gridTemplateColumns: cols,
        alignItems: 'center', gap: 8, padding: '6px 12px',
        borderBottom: `1px solid ${T.border}`,
        cursor: onClick ? 'pointer' : 'default',
        background: active ? 'rgba(88,101,242,0.12)' : 'transparent',
        transition: 'background 0.1s',
      }}
    >
      <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: T.dim, textAlign: 'center' }}>#{rank}</span>
      {avatar !== undefined && (
        <span style={{
          width: 18, height: 18, borderRadius: '50%',
          background: avatarColor(String(avatar)),
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          color: '#fff', fontSize: 9, fontWeight: 700, fontFamily: 'DM Sans',
          justifySelf: 'center', flexShrink: 0,
        }}>{avatarLetter(avatar)}</span>
      )}
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
        <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: T.text, minWidth: 36, textAlign: 'right' }}>{count}</span>
      </div>
      <span style={{ fontSize: 10, color: T.sub, textAlign: 'right' }}>{fmtRelative(last)}</span>
    </div>
  );
}

function Summary({ label, value, accent, sub }) {
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
      {sub && <div style={{ fontSize: 9, color: T.dim, marginTop: 5 }}>{sub}</div>}
    </div>
  );
}

function Pill({ active, color, onClick, children, title }) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        background: active ? `${color}28` : 'transparent',
        border: `1px solid ${active ? color : T.border}`,
        color: active ? T.text : T.sub,
        borderRadius: 6, padding: '3px 9px',
        fontFamily: 'JetBrains Mono', fontSize: 10,
        cursor: 'pointer', transition: 'all 0.1s',
      }}
    >{children}</button>
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
  const [windowKey, setWindowKey] = useState('30d');
  const [kindFilter, setKindFilter] = useState('');      // '' = all
  const [userFilter, setUserFilter] = useState(0);       // 0 = all

  const [topSounds, setTopSounds] = useState({ total: 0, rows: [] });
  const [topUsers,  setTopUsers]  = useState({ total: 0, rows: [] });
  const [topCmds,   setTopCmds]   = useState({ total: 0, rows: [] });
  const [kindMix,   setKindMix]   = useState({ total: 0, rows: [] });
  const [feed,      setFeed]      = useState([]);
  const [loaded,    setLoaded]    = useState(false);

  const feedRef = useRef([]);

  const windowSec = useMemo(
    () => (WINDOWS.find(w => w.key === windowKey) || WINDOWS[2]).seconds,
    [windowKey]
  );
  const tsFrom = useMemo(
    () => Math.floor(Date.now() / 1000) - windowSec,
    [windowSec]
  );

  // Aggregate refresh.
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const [s, u, c, k] = await Promise.all([
          b.queryStats('soundboard_play', userFilter, '', tsFrom, 0, 'target', 20),
          b.queryStats(kindFilter,        0,          '', tsFrom, 0, 'user_id', 20),
          b.queryStats('cmd_invoke',      userFilter, '', tsFrom, 0, 'target', 15),
          b.queryStats(kindFilter,        userFilter, '', tsFrom, 0, 'kind',    12),
        ]);
        if (cancelled) return;
        setTopSounds({ total: s.total_count || 0, rows: s.rows || [] });
        setTopUsers( { total: u.total_count || 0, rows: u.rows || [] });
        setTopCmds(  { total: c.total_count || 0, rows: c.rows || [] });
        setKindMix(  { total: k.total_count || 0, rows: k.rows || [] });
        setLoaded(true);
      } catch {
        setLoaded(true);
      }
    };
    refresh();
    if (IS_SNAPSHOT) {
      return () => { cancelled = true; };
    }
    const iv = setInterval(refresh, 10_000);
    return () => { cancelled = true; clearInterval(iv); };
  }, [tsFrom, kindFilter, userFilter]);

  // Live feed (skipped in snapshot mode — there is no event stream there).
  useEffect(() => {
    if (IS_SNAPSHOT) return;
    let cancelled = false;
    let iv;
    (async () => {
      try {
        const back = await b.backlogEvents(100);
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
  const maxCmd   = Math.max(1, ...topCmds.rows.map(r => r.count));
  const totalEvents = kindMix.total;

  const empty = loaded && totalEvents === 0 && !kindFilter && !userFilter;

  const feedFiltered = useMemo(() => feed.filter(ev => {
    if (kindFilter && ev.kind !== kindFilter) return false;
    if (userFilter && String(ev.user_id) !== String(userFilter)) return false;
    return true;
  }), [feed, kindFilter, userFilter]);

  const toggleKind = useCallback((k) => {
    setKindFilter(prev => prev === k ? '' : k);
  }, []);
  const toggleUser = useCallback((uid) => {
    setUserFilter(prev => String(prev) === String(uid) ? 0 : uid);
  }, []);

  const filterLabel = (kindFilter || userFilter)
    ? `${kindFilter || ''}${kindFilter && userFilter ? ' · ' : ''}${userFilter ? `user ${shortUser(userFilter)}` : ''}`
    : '';

  const clearFilters = () => { setKindFilter(''); setUserFilter(0); };

  return (
    <div style={{ position: 'relative', height: '100%', animation: 'fadeIn 0.15s ease' }}>
      <div style={{
        height: '100%', overflow: 'auto', padding: '16px',
        filter: empty ? 'blur(2px) saturate(0.7) opacity(0.6)' : 'none',
        pointerEvents: empty ? 'none' : 'auto',
      }}>

        {/* Identity header — distinguishes Analytics from Stats */}
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: T.text }}>
            Who's using halbot, what they're doing
          </div>
          <div style={{ fontSize: 11, color: T.sub, marginTop: 3, fontFamily: 'JetBrains Mono' }}>
            {IS_SNAPSHOT
              ? 'Aggregated event history · static 30-day snapshot'
              : 'Aggregated event history · click any pill to filter'}
          </div>
        </div>

        {/* Toolbar — window picker + filter affordances are tray-only.
             Snapshot is frozen at 30d and has no per-row data to filter against. */}
        {!IS_SNAPSHOT && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14,
            padding: '10px 12px', background: T.surface,
            border: `1px solid ${T.border}`, borderRadius: 9,
          }}>
            <span style={{
              fontSize: 9, fontWeight: 600, color: T.dim,
              textTransform: 'uppercase', letterSpacing: '0.1em',
            }}>Window</span>
            {WINDOWS.map(w => (
              <Pill key={w.key} active={windowKey === w.key} color={T.blurple}
                    onClick={() => setWindowKey(w.key)}>
                {w.label}
              </Pill>
            ))}
            <div style={{ flex: 1 }} />
            {filterLabel ? (
              <>
                <span style={{
                  fontSize: 10, color: T.yellow, fontFamily: 'JetBrains Mono',
                }}>filter: {filterLabel}</span>
                <Pill active color={T.yellow} onClick={clearFilters} title="Clear all filters">
                  ✕ clear
                </Pill>
              </>
            ) : (
              <span style={{ fontSize: 10, color: T.dim, fontStyle: 'italic' }}>
                click a kind or user to filter
              </span>
            )}
          </div>
        )}

        {/* Summary strip */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 18 }}>
          <Summary label={`Events (${windowKey})`} value={totalEvents} accent={T.blurple} sub={filterLabel ? 'filtered' : 'all events'} />
          <Summary label="Soundboard plays" value={topSounds.total} accent={T.blurple} />
          <Summary label="Commands invoked" value={topCmds.total} accent={T.cyan} />
          <Summary label="Event types seen" value={kindMix.rows.length} accent={T.green} sub={`of ${Object.keys(KIND_META).length} known`} />
        </div>

        {/* Event kind mix — interactive filter */}
        <Section title={`Event type mix — ${windowKey}`}
                 right={!IS_SNAPSHOT && <span style={{ fontSize: 9, color: T.dim, fontStyle: 'italic' }}>click to filter</span>}>
          <div style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 9, padding: '10px 14px', display: 'flex', flexWrap: 'wrap', gap: 8,
          }}>
            {kindMix.rows.length === 0 ? (
              <span style={{ fontSize: 12, color: T.dim, fontStyle: 'italic' }}>no events in window</span>
            ) : kindMix.rows.map(r => (
              <Pill key={r.key}
                    active={kindFilter === r.key}
                    color={kindColor(r.key)}
                    onClick={IS_SNAPSHOT ? undefined : () => toggleKind(r.key)}
                    title={IS_SNAPSHOT ? r.key : (kindFilter === r.key ? 'Click to clear filter' : `Filter to ${r.key}`)}>
                <span>{kindEmoji(r.key)}</span>
                <span>{r.key}</span>
                <span style={{ color: kindColor(r.key), fontWeight: 600 }}>{r.count}</span>
              </Pill>
            ))}
          </div>
        </Section>

        {/* Top soundboard plays */}
        <Section title={`Top soundboard plays — ${windowKey}`}>
          <div style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 9, overflow: 'hidden',
          }}>
            {topSounds.rows.length === 0 ? (
              <div style={{ padding: '14px', fontSize: 12, color: T.dim, fontStyle: 'italic' }}>no soundboard plays in window</div>
            ) : topSounds.rows.map((r, i) => (
              <BarRow key={r.key} rank={i + 1} label={r.key || '—'}
                count={r.count} max={maxSound} last={r.last_ts_unix}
                accent={T.blurple} mono />
            ))}
          </div>
        </Section>

        {/* Top commands */}
        <Section title={`Top commands invoked — ${windowKey}`}>
          <div style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 9, overflow: 'hidden',
          }}>
            {topCmds.rows.length === 0 ? (
              <div style={{ padding: '14px', fontSize: 12, color: T.dim, fontStyle: 'italic' }}>no commands invoked in window</div>
            ) : topCmds.rows.map((r, i) => (
              <BarRow key={r.key} rank={i + 1} label={r.key || 'unknown'}
                count={r.count} max={maxCmd} last={r.last_ts_unix}
                accent={T.cyan} mono />
            ))}
          </div>
        </Section>

        {/* Top users — clickable to filter (tray only). Snapshot has the user
             id pre-resolved to a display name in the `key` field. */}
        <Section title={`Top users by activity — ${windowKey}`}
                 right={!IS_SNAPSHOT && <span style={{ fontSize: 9, color: T.dim, fontStyle: 'italic' }}>click to drill down</span>}>
          <div style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 9, overflow: 'hidden',
          }}>
            {topUsers.rows.length === 0 ? (
              <div style={{ padding: '14px', fontSize: 12, color: T.dim, fontStyle: 'italic' }}>no user activity in window</div>
            ) : topUsers.rows.map((r, i) => {
              const label = IS_SNAPSHOT ? (r.key || '—') : `user ${shortUser(r.key)}`;
              return (
              <BarRow key={`${r.key}-${i}`} rank={i + 1}
                label={label} avatar={label}
                count={r.count} max={maxUser} last={r.last_ts_unix}
                accent={T.green} mono={!IS_SNAPSHOT}
                onClick={IS_SNAPSHOT ? undefined : () => toggleUser(r.key)}
                active={!IS_SNAPSHOT && String(userFilter) === String(r.key)} />
              );
            })}
          </div>
        </Section>

        {/* Live feed — only meaningful when wired to a live event stream. */}
        {!IS_SNAPSHOT && <Section title={`Live event feed${filterLabel ? ` (filtered: ${filterLabel})` : ''}`}
                 right={<span style={{ fontSize: 9, color: T.dim, fontFamily: 'JetBrains Mono' }}>
                   {feedFiltered.length}/{feed.length}
                 </span>}>
          <div style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 9, maxHeight: 360, overflow: 'auto',
          }}>
            {feedFiltered.length === 0 ? (
              <div style={{ padding: '14px', fontSize: 12, color: T.dim, fontStyle: 'italic' }}>
                {feed.length === 0 ? 'waiting for events…' : 'no events match current filter'}
              </div>
            ) : feedFiltered.map((ev, i) => {
              const meta = parseMeta(ev.meta_json);
              const metaLine = metaSummary(meta, ev.kind);
              return (
                <div key={`${ev.ts_ns}-${i}`} style={{
                  display: 'grid', gridTemplateColumns: '68px 22px 128px 1fr 130px 80px',
                  alignItems: 'center', gap: 8, padding: '5px 12px',
                  borderBottom: i < feedFiltered.length - 1 ? `1px solid ${T.border}` : 'none',
                  fontSize: 11,
                }}>
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: T.dim }}>{fmtTime(ev.ts_ns)}</span>
                  <span style={{ textAlign: 'center' }}>{kindEmoji(ev.kind)}</span>
                  <span style={{ fontFamily: 'JetBrains Mono', color: kindColor(ev.kind), overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', cursor: 'pointer' }}
                        onClick={() => toggleKind(ev.kind)}
                        title={`Filter to ${ev.kind}`}>
                    {ev.kind}
                  </span>
                  <span style={{ color: T.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {ev.target || <span style={{ color: T.dim, fontStyle: 'italic' }}>—</span>}
                  </span>
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: T.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {metaLine}
                  </span>
                  <span
                    onClick={() => ev.user_id && toggleUser(ev.user_id)}
                    title={ev.user_id ? `Filter to user ${ev.user_id}` : ''}
                    style={{
                      fontFamily: 'JetBrains Mono', fontSize: 10, color: T.sub,
                      textAlign: 'right', cursor: ev.user_id ? 'pointer' : 'default',
                    }}>
                    {ev.user_id ? shortUser(ev.user_id) : ''}
                  </span>
                </div>
              );
            })}
          </div>
        </Section>}

        <div style={{ height: 8 }} />
      </div>

      {empty && (
        <EmptyOverlay message={
          <>
            Analytics storage is live but no events recorded yet in this window.
            Interact with the bot — mention it, trigger a soundboard play, join a
            voice channel — and numbers will populate here within a second.
            Try widening the window to <code style={{ fontFamily: 'JetBrains Mono', color: T.cyan }}>30 days</code> if recent activity is expected.
          </>
        } />
      )}
    </div>
  );
}
