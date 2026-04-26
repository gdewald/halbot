import { useEffect, useMemo, useState } from 'react';
import { T } from '../tokens.js';
import { b, IS_SNAPSHOT } from '../bridge.js';
import { StatCard, MiniBar, SectionHeader } from './stats/StatCard.jsx';
import { LatencyCard } from './stats/LatencyCard.jsx';

const REFRESH_MS = 10_000;
const EMPTY_STATS = {
  mock: true,
  soundboard: { sounds_backed_up: 0, storage_bytes: 0, last_sync_unix: 0, new_since_last: 0 },
  voice_playback: { played_today: 0, played_all_time: 0, session_seconds_today: 0, avg_response_ms: 0 },
  wake_word: { detections_today: 0, detections_all_time: 0, false_positives_today: 0, avg_join_latency_ms: 0 },
  stt: { avg_ms: 0, p95_ms: 0, count_today: 0 },
  tts: { avg_ms: 0, p95_ms: 0, count_today: 0 },
  llm: {
    response_avg_ms: 0, response_p95_ms: 0,
    ttft_avg_ms: 0, ttft_p95_ms: 0, tokens_per_sec: 0,
    requests_today: 0, avg_tokens_out: 0, context_usage_pct: 0, timeouts_today: 0,
  },
};

function fmtRelative(unix) {
  if (!unix) return '—';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - unix));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  if (s < 86400) return `${Math.floor(s/3600)}h ago`;
  return `${Math.floor(s/86400)}d ago`;
}

function fmtBytes(n) {
  if (!n) return '0';
  const mb = n / (1024 * 1024);
  if (mb >= 1) return `${mb.toFixed(1)}`;
  return `${(n / 1024).toFixed(0)}`;
}

function fmtByteCol(n) {
  if (!n) return '—';
  const kb = n / 1024;
  if (kb >= 1024) return `${(kb / 1024).toFixed(1)} MB`;
  return `${Math.round(kb)} KB`;
}

const CUSTOM_EMOJI_RE = /^<a?:([A-Za-z0-9_]+):(\d+)>$/;
function parseCustomEmoji(raw) {
  if (!raw) return null;
  const m = CUSTOM_EMOJI_RE.exec(raw);
  if (!m) return null;
  return { name: m[1], id: m[2] };
}
function EmojiCell({ raw, emojiIndex }) {
  if (!raw) return <span>•</span>;
  const parsed = parseCustomEmoji(raw);
  if (!parsed) return <span>{raw}</span>;  // unicode emoji: render directly
  const hit = emojiIndex?.byId?.get(parsed.id) || emojiIndex?.byName?.get(parsed.name);
  if (hit?.image_data_url) {
    return <img src={hit.image_data_url} alt={parsed.name} title={parsed.name}
                style={{ width: 16, height: 16, objectFit: 'contain', verticalAlign: 'middle' }} />;
  }
  return <span title={parsed.name}>•</span>;
}

function fmtDuration(sec) {
  if (!sec) return '—';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function StatsPanel() {
  const [stats, setStats] = useState(EMPTY_STATS);
  const [sounds, setSounds] = useState([]);
  const [emojis, setEmojis] = useState([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const [s, list, emj] = await Promise.all([
          b.getStats().catch(() => EMPTY_STATS),
          b.soundboardList().catch(() => []),
          b.emojiList().catch(() => []),
        ]);
        if (cancelled) return;
        setStats(s || EMPTY_STATS);
        setSounds(Array.isArray(list) ? list : []);
        setEmojis(Array.isArray(emj) ? emj : []);
        setLoaded(true);
      } catch {
        if (!cancelled) setLoaded(true);
      }
    };
    refresh();
    if (IS_SNAPSHOT) {
      return () => { cancelled = true; };
    }
    const iv = setInterval(refresh, REFRESH_MS);
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  const mock = !!stats.mock;
  const empty = loaded && !mock
    && stats.voice_playback.played_all_time === 0
    && stats.llm.requests_today === 0
    && stats.tts.count_today === 0
    && sounds.length === 0;

  const maxPlays = useMemo(
    () => Math.max(1, ...sounds.map(s => s.plays || 0)),
    [sounds]
  );

  // Group parent→child so effect-derived rows render as sub-bullets under
  // their source. Live-play-only rows (id=0) and rows whose parent_id
  // points outside the visible set fall back to root level.
  const displayRows = useMemo(() => {
    const visibleIds = new Set(sounds.map(s => s.id).filter(Boolean));
    const kids = new Map();  // parent_id -> [child, ...]
    const roots = [];
    for (const s of sounds) {
      const pid = s.parent_id;
      if (pid && visibleIds.has(pid)) {
        if (!kids.has(pid)) kids.set(pid, []);
        kids.get(pid).push(s);
      } else {
        roots.push(s);
      }
    }
    for (const arr of kids.values()) {
      arr.sort((a, b) => (b.plays || 0) - (a.plays || 0));
    }
    roots.sort((a, b) => (b.plays || 0) - (a.plays || 0));
    const flat = [];
    for (const r of roots) {
      flat.push({ row: r, depth: 0 });
      for (const c of (kids.get(r.id) || [])) {
        flat.push({ row: c, depth: 1 });
      }
    }
    return flat;
  }, [sounds]);

  const parseEffects = (raw) => {
    if (!raw) return '';
    try {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr) && arr.length) {
        return arr.map(e => (e && e.type) || '?').join(' + ');
      }
    } catch { /* swallow */ }
    return '';
  };

  const emojiIndex = useMemo(() => {
    const byId = new Map();
    const byName = new Map();
    for (const e of emojis) {
      if (e.emoji_id) byId.set(String(e.emoji_id), e);
      if (e.name) byName.set(e.name, e);
    }
    return { byId, byName };
  }, [emojis]);

  const sb = stats.soundboard;
  const vp = stats.voice_playback;
  const ww = stats.wake_word;
  const stt = stats.stt;
  const tts = stats.tts;
  const llm = stats.llm;

  const falsePct = ww.detections_today > 0
    ? `${((ww.false_positives_today / ww.detections_today) * 100).toFixed(1)}%`
    : '—';

  return (
    <div style={{ position: 'relative', height: '100%', animation: 'fadeIn 0.15s ease' }}>
      <div style={{
        height: '100%', overflow: 'auto', padding: '16px',
        filter: (mock || empty) ? 'blur(2px) saturate(0.7) opacity(0.6)' : 'none',
        pointerEvents: (mock || empty) ? 'none' : 'auto',
      }}>

        {/* Soundboard */}
        <SectionHeader label="Soundboard Backup" icon="💾" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 12 }}>
          <StatCard label="Sounds backed up"   value={sb.sounds_backed_up}  sub="in saved_sounds table"   accent={T.blurple} />
          <StatCard label="Storage used"       value={fmtBytes(sb.storage_bytes)} unit="MB" sub="audio blob total"    accent={T.blurple} />
          <StatCard label="Last saved"         value={fmtRelative(sb.last_sync_unix)} sub={sb.last_sync_unix ? new Date(sb.last_sync_unix * 1000).toLocaleString() : 'no data'} accent={T.cyan} />
          <StatCard label="New last 24h"       value={sb.new_since_last}    sub="rows added"             accent={T.green} />
        </div>
        <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 9, overflow: 'hidden', marginBottom: 18 }}>
          <div style={{
            padding: '7px 14px', borderBottom: `1px solid ${T.border}`,
            display: 'grid', gridTemplateColumns: '26px 150px 1fr 110px 120px 72px', gap: 8,
            fontSize: 9, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em',
          }}>
            <span /><span>Name</span><span>Metadata</span><span>Plays (30d)</span><span>Last played</span>
            <span style={{ textAlign: 'right' }}>Size</span>
          </div>
          {sounds.length === 0 ? (
            <div style={{ padding: '14px', fontSize: 12, color: T.dim, fontStyle: 'italic' }}>
              no soundboard rows — save some sounds first
            </div>
          ) : displayRows.slice(0, 30).map(({ row: s, depth }, i, arr) => {
            const isChild = depth > 0;
            const effects = isChild ? parseEffects(s.effects) : '';
            const metaDisplay = s.metadata
              || (effects ? effects : (s.saved_by === '(live)' ? 'live soundboard' : '—'));
            return (
              <div key={s.id || s.name || i} style={{
                display: 'grid', gridTemplateColumns: '26px 150px 1fr 110px 120px 72px',
                alignItems: 'center', gap: 8, padding: '6px 14px',
                borderBottom: i < Math.min(arr.length, 30) - 1 ? `1px solid ${T.border}` : 'none',
                background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.012)',
              }}>
                <span style={{ fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden', whiteSpace: 'nowrap' }}>
                  {isChild ? <span style={{ color: T.dim, fontFamily: 'JetBrains Mono', fontSize: 11 }}>└</span> : <EmojiCell raw={s.emoji} emojiIndex={emojiIndex} />}
                </span>
                <span style={{
                  fontFamily: 'JetBrains Mono', fontSize: 12,
                  color: isChild ? T.sub : T.cyan,
                  paddingLeft: isChild ? 14 : 0,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>{s.name || '—'}</span>
                <span style={{ fontSize: 11, color: T.dim, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontStyle: (s.metadata || effects) ? 'normal' : 'italic' }}>
                  {metaDisplay}
                </span>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <MiniBar value={s.plays} max={maxPlays} color={T.blurple} />
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: T.text, minWidth: 30, textAlign: 'right' }}>{s.plays}</span>
                </div>
                <span style={{ fontSize: 11, color: T.sub }}>{fmtRelative(s.last_played_unix)}</span>
                <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: T.dim, textAlign: 'right' }}>{fmtByteCol(s.size_bytes)}</span>
              </div>
            );
          })}
        </div>

        {/* Voice playback */}
        <SectionHeader label="Voice Playback" icon="🔊" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 18 }}>
          <StatCard label="Sounds played today" value={vp.played_today}    sub="soundboard_play events"  accent={T.cyan} />
          <StatCard label="Total all-time"      value={vp.played_all_time.toLocaleString()}  sub="since analytics start"   accent={T.cyan} />
          <StatCard label="Session time today"  value={fmtDuration(vp.session_seconds_today)} sub="voice_join proxy × 60s"  accent={T.yellow} />
          <StatCard label="Avg response time"   value={vp.avg_response_ms} unit="ms" sub="avg TTS latency today" accent={T.green} />
        </div>

        {/* Wake word */}
        <SectionHeader label="Wake Word" icon="🎙" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 18 }}>
          <StatCard label="Detections today" value={ww.detections_today}  sub="voice LLM parses"        accent={T.green} />
          <StatCard label="Total all-time"   value={ww.detections_all_time} sub="since analytics start" accent={T.green} />
          <StatCard label="False positives"  value={ww.false_positives_today} unit="today" sub={falsePct} accent={T.yellow} />
          <StatCard label="Avg join latency" value={ww.avg_join_latency_ms} unit="ms" sub="not yet emitted" accent={T.blurple} />
        </div>

        {/* STT */}
        <SectionHeader label="Speech-to-Text (STT)" icon="👂" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 18 }}>
          <LatencyCard label="Transcription latency" avg={stt.avg_ms} p95={stt.p95_ms} unit="ms" color={T.cyan} sub="not yet emitted" />
          <LatencyCard label="Chunk processing time" avg={0}          p95={0}          unit="ms" color={T.cyan} sub="not yet emitted" />
          <StatCard    label="Segments today"    value={stt.count_today}    sub="stt_segment events"      accent={T.cyan} />
          <StatCard    label="Avg utterance len" value={0}  unit="s" sub="not yet emitted" accent={T.dim} />
        </div>

        {/* TTS */}
        <SectionHeader label="Text-to-Speech (TTS)" icon="🗣" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 18 }}>
          <LatencyCard label="Full render time"  avg={tts.avg_ms} p95={tts.p95_ms} unit="ms" color={T.yellow} sub="engine.synth latency" />
          <LatencyCard label="First audio chunk" avg={0}          p95={0}          unit="ms" color={T.yellow} sub="not yet emitted" />
          <StatCard    label="Renders today"     value={tts.count_today}    sub="tts_request events"         accent={T.yellow} />
          <StatCard    label="Engine fallback"   value={0} unit="today" sub="not yet emitted" accent={T.red} />
        </div>

        {/* LLM */}
        <SectionHeader label="Text LLM" icon="🧠" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginBottom: 10 }}>
          <LatencyCard label="Response latency"    avg={llm.response_avg_ms} p95={llm.response_p95_ms} unit="ms" color={T.blurple} sub="full completion time" />
          <LatencyCard label="Time to first token" avg={llm.ttft_avg_ms}     p95={llm.ttft_p95_ms}     unit="ms" color={T.blurple} sub="not yet emitted" />
          <div style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 9, padding: '12px 14px', position: 'relative', overflow: 'hidden',
          }}>
            <div style={{
              position: 'absolute', top: 0, left: 0, right: 0, height: 2,
              background: `linear-gradient(90deg,${T.green},${T.green}44)`,
            }} />
            <div style={{ fontSize: 9, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.09em', marginBottom: 4 }}>Throughput</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <span style={{ fontSize: 22, fontWeight: 600, color: T.text, fontFamily: 'JetBrains Mono' }}>{llm.tokens_per_sec}</span>
              <span style={{ fontSize: 11, color: T.sub }}>tok/s</span>
            </div>
            <div style={{ fontSize: 9, color: T.dim, marginTop: 5 }}>not yet emitted</div>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 20 }}>
          <StatCard label="Requests today" value={llm.requests_today} sub="llm_call events" accent={T.blurple} />
          <StatCard label="Avg tokens out" value={llm.avg_tokens_out}  sub="not yet emitted"  accent={T.blurple} />
          <StatCard label="Context usage"  value={llm.context_usage_pct}  unit="%" sub="not yet emitted" accent={T.yellow} />
          <StatCard label="Timeouts today" value={llm.timeouts_today} sub={`of ${llm.requests_today} requests`} accent={T.red} />
        </div>
        <div style={{ height: 8 }} />
      </div>

      {(mock || empty) && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex',
          alignItems: 'center', justifyContent: 'center',
          background: 'rgba(12,12,15,0.45)', backdropFilter: 'blur(2px)',
          WebkitBackdropFilter: 'blur(2px)',
        }}>
          <div style={{
            background: T.raised, border: `1px solid ${T.yellow}35`,
            borderRadius: 12, padding: '20px 24px', maxWidth: 480, textAlign: 'center',
            boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
          }}>
            <div style={{
              fontSize: 10, fontWeight: 600, color: T.yellow,
              textTransform: 'uppercase', letterSpacing: '0.12em', marginBottom: 8,
            }}>{mock ? 'Daemon unreachable' : 'No activity yet'}</div>
            <div style={{ fontSize: 13, color: T.text, lineHeight: 1.5 }}>
              {mock
                ? 'GetStats RPC returned a mock/fallback response. Check that the halbot service is running.'
                : 'Numbers populate as Discord users trigger sounds, commands, voice sessions, and LLM calls. Ambient bot traffic fills this view within a few interactions.'}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
