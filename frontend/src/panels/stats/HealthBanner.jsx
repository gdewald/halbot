import { useEffect, useState } from 'react';
import { T } from '../../tokens.js';
import { b, IS_SNAPSHOT } from '../../bridge.js';

const REFRESH_MS = 5_000;

function fmtUptime(sec) {
  if (!sec || sec < 60) return `${Math.floor(sec || 0)}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  if (h < 24) return `${h}h ${String(mm).padStart(2, '0')}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

function fmtMB(bytes) {
  if (!bytes) return '—';
  return `${(bytes / (1024 * 1024)).toFixed(0)} MB`;
}

export function HealthBanner({ stats }) {
  const [h, setH] = useState(null);
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const r = await b.health();
        if (!cancelled) setH(r);
      } catch { /* swallow */ }
    };
    refresh();
    if (IS_SNAPSHOT) return () => { cancelled = true; };
    const iv = setInterval(refresh, REFRESH_MS);
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  const healthy = h && !!h.daemon_version && !String(h.daemon_version).startsWith('static snapshot');
  const subParts = [];
  if (healthy) {
    if (h.guild_count) subParts.push(`${h.guild_count} guild${h.guild_count !== 1 ? 's' : ''}`);
    subParts.push(`uptime ${fmtUptime(h.uptime_seconds)}`);
    if (h.rss_bytes) subParts.push(`${fmtMB(h.rss_bytes)} RSS`);
  } else if (h && String(h.daemon_version).startsWith('static snapshot')) {
    subParts.push(h.daemon_version);
  } else {
    subParts.push('daemon unreachable');
  }

  const dotColor = healthy ? T.green : (h ? T.yellow : T.red);

  // Right-side latency summary from stats. Use TTS + LLM p50 as the user-perceptible
  // round-trip pair. Hide the line entirely if neither metric has data.
  const ttsAvg = stats?.tts?.p50_ms || stats?.tts?.avg_ms || 0;
  const llmAvg = stats?.llm?.response_p50_ms || stats?.llm?.response_avg_ms || 0;
  const showLatency = ttsAvg > 0 || llmAvg > 0;

  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10,
      padding: '14px 16px', marginBottom: 18,
      display: 'flex', alignItems: 'center', gap: 14,
    }}>
      <div style={{
        width: 10, height: 10, borderRadius: '50%', background: dotColor,
        boxShadow: `0 0 12px ${dotColor}`,
        animation: healthy ? 'pulse 2s infinite' : 'none', flexShrink: 0,
      }} />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: T.text }}>
          {healthy ? 'Daemon healthy' : (h ? 'Snapshot view' : 'Daemon unreachable')}
        </div>
        <div style={{ fontSize: 10, color: T.sub, marginTop: 2, fontFamily: 'JetBrains Mono', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {subParts.join(' · ')}
        </div>
      </div>
      <div style={{ flex: 1 }} />
      {showLatency && (
        <div style={{ textAlign: 'right' }}>
          {ttsAvg > 0 && (
            <div style={{ fontSize: 11, color: T.text, fontFamily: 'JetBrains Mono' }}>
              {Math.round(ttsAvg)} ms <span style={{ color: T.sub }}>p50 TTS</span>
            </div>
          )}
          {llmAvg > 0 && (
            <div style={{ fontSize: 11, color: T.text, marginTop: 2, fontFamily: 'JetBrains Mono' }}>
              {Math.round(llmAvg).toLocaleString()} ms <span style={{ color: T.sub }}>p50 LLM</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
