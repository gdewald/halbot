import { useEffect, useState } from 'react';
import { T } from '../tokens.js';
import { b } from '../bridge.js';
import { StatCard, MiniBar, SectionHeader } from './stats/StatCard.jsx';
import { LatencyCard } from './stats/LatencyCard.jsx';
import { SOUND_LIST, WAKE_HISTORY, MOCK_NUMBERS } from './stats/MockData.js';

export function StatsPanel() {
  const [mock, setMock] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const s = await b.getStats();
        setMock(!!s.mock);
      } catch {
        setMock(true);
      }
    })();
  }, []);

  const maxPlays = Math.max(...SOUND_LIST.map(s => s.plays));
  const N = MOCK_NUMBERS;

  return (
    <div style={{ position: 'relative', height: '100%', animation: 'fadeIn 0.15s ease' }}>
      <div style={{
        height: '100%', overflow: 'auto', padding: '16px',
        filter: mock ? 'blur(2px) saturate(0.7) opacity(0.6)' : 'none',
        pointerEvents: mock ? 'none' : 'auto',
      }}>

        {/* Soundboard */}
        <SectionHeader label="Soundboard Backup" icon="💾" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 12 }}>
          <StatCard label="Sounds backed up"   value={N.soundboard.backedUp}  sub="across all categories"   accent={T.blurple} />
          <StatCard label="Storage used"       value={N.soundboard.storageMb} unit="MB" sub="on disk"       accent={T.blurple} />
          <StatCard label="Last sync"          value={N.soundboard.lastSync}         sub={N.soundboard.lastSyncTs} accent={T.cyan} />
          <StatCard label="New since last run" value={N.soundboard.newSince}  sub="added since yesterday"   accent={T.green} />
        </div>
        <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 9, overflow: 'hidden', marginBottom: 18 }}>
          <div style={{
            padding: '7px 14px', borderBottom: `1px solid ${T.border}`,
            display: 'grid', gridTemplateColumns: '26px 130px 1fr 90px 110px 56px', gap: 8,
            fontSize: 9, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em',
          }}>
            <span /><span>Name</span><span>Description</span><span>Plays</span><span>Last played</span>
            <span style={{ textAlign: 'right' }}>Size</span>
          </div>
          {SOUND_LIST.map((s, i) => (
            <div key={s.name} style={{
              display: 'grid', gridTemplateColumns: '26px 130px 1fr 90px 110px 56px',
              alignItems: 'center', gap: 8, padding: '6px 14px',
              borderBottom: i < SOUND_LIST.length - 1 ? `1px solid ${T.border}` : 'none',
              background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.012)',
            }}>
              <span style={{ fontSize: 14, textAlign: 'center' }}>{s.emoji}</span>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 12, color: T.cyan, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
              <span style={{ fontSize: 11, color: T.dim, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontStyle: s.desc ? 'normal' : 'italic' }}>{s.desc || '—'}</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <MiniBar value={s.plays} max={maxPlays} color={T.blurple} />
                <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: T.text, minWidth: 26, textAlign: 'right' }}>{s.plays}</span>
              </div>
              <span style={{ fontSize: 11, color: T.sub }}>{s.lastPlayed}</span>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: T.dim, textAlign: 'right' }}>{s.size}</span>
            </div>
          ))}
        </div>

        {/* Voice playback */}
        <SectionHeader label="Voice Playback" icon="🔊" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 18 }}>
          <StatCard label="Sounds played today" value={N.voice.playedToday}    sub="across all sessions"  accent={T.cyan} />
          <StatCard label="Total all-time"      value={N.voice.playedAllTime.toLocaleString()}  sub="since bot started"    accent={T.cyan} />
          <StatCard label="Session time today"  value={N.voice.sessionToday}   sub="cumulative voice"     accent={T.yellow} />
          <StatCard label="Avg response time"   value={N.voice.avgResponseMs}  unit="ms" sub="TTS + join" accent={T.green} />
        </div>

        {/* Wake word */}
        <SectionHeader label="Wake Word" icon="🎙" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 10 }}>
          <StatCard label="Detections today" value={N.wake.detectionsToday}  sub="hey hal"        accent={T.green} />
          <StatCard label="Total all-time"   value={N.wake.detectionsAllTime} sub="since tracking" accent={T.green} />
          <StatCard label="False positives"  value={N.wake.falsePositivesToday} unit="today" sub={N.wake.falsePctToday} accent={T.yellow} />
          <StatCard label="Avg join latency" value={N.wake.avgJoinMs} unit="ms" sub="wake → audio" accent={T.blurple} />
        </div>
        <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 9, overflow: 'hidden', marginBottom: 18 }}>
          <div style={{
            padding: '7px 14px', borderBottom: `1px solid ${T.border}`,
            display: 'grid', gridTemplateColumns: '80px 90px 1fr', gap: 8,
            fontSize: 9, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em',
          }}>
            <span>Time</span><span>Phrase</span><span>Action</span>
          </div>
          {WAKE_HISTORY.map((w, i) => (
            <div key={i} style={{
              display: 'grid', gridTemplateColumns: '80px 90px 1fr',
              alignItems: 'center', gap: 8, padding: '7px 14px',
              borderBottom: i < WAKE_HISTORY.length - 1 ? `1px solid ${T.border}` : 'none',
            }}>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: T.dim }}>{w.time}</span>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: T.blurpleL }}>{w.phrase}</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <div style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, background: w.ok ? T.green : T.yellow }} />
                <span style={{ fontSize: 12, color: w.ok ? T.text : T.yellow }}>{w.action}</span>
              </div>
            </div>
          ))}
        </div>

        {/* STT */}
        <SectionHeader label="Speech-to-Text (STT)" icon="👂" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 18 }}>
          <LatencyCard label="Transcription latency" avg={N.stt.latencyAvg} p95={N.stt.latencyP95} unit="ms" color={T.cyan} sub="time from audio end → text" />
          <LatencyCard label="Chunk processing time" avg={N.stt.chunkAvg}   p95={N.stt.chunkP95}   unit="ms" color={T.cyan} sub="per audio segment" />
          <StatCard    label="Segments today"    value={N.stt.segmentsToday}    sub="utterances processed"      accent={T.cyan} />
          <StatCard    label="Avg utterance len" value={N.stt.avgUtteranceSec}  unit="s" sub="mean audio length" accent={T.dim} />
        </div>

        {/* TTS */}
        <SectionHeader label="Text-to-Speech (TTS)" icon="🗣" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 18 }}>
          <LatencyCard label="First audio chunk" avg={N.tts.firstChunkAvg} p95={N.tts.firstChunkP95} unit="ms" color={T.yellow} sub="time to first playable chunk" />
          <LatencyCard label="Full render time"  avg={N.tts.fullAvg}       p95={N.tts.fullP95}       unit="ms" color={T.yellow} sub="complete synthesis time" />
          <StatCard    label="Renders today"     value={N.tts.rendersToday}    sub="TTS invocations"            accent={T.yellow} />
          <StatCard    label="Fallback to espeak" value={N.tts.fallbacksToday} unit="today" sub="kokoro failures" accent={T.red} />
        </div>

        {/* LLM */}
        <SectionHeader label="Text LLM" icon="🧠" />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginBottom: 10 }}>
          <LatencyCard label="Response latency"    avg={N.llm.responseAvgMs} p95={N.llm.responseP95Ms} unit="ms" color={T.blurple} sub="full completion time" />
          <LatencyCard label="Time to first token" avg={N.llm.ttftAvgMs}     p95={N.llm.ttftP95Ms}     unit="ms" color={T.blurple} sub="TTFT — key for streaming" />
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
              <span style={{ fontSize: 22, fontWeight: 600, color: T.text, fontFamily: 'JetBrains Mono' }}>{N.llm.tokPerSec}</span>
              <span style={{ fontSize: 11, color: T.sub }}>tok/s</span>
            </div>
            <div style={{ fontSize: 9, color: T.dim, marginTop: 5 }}>avg generation speed</div>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 20 }}>
          <StatCard label="Requests today" value={N.llm.requestsToday} sub="LLM completions" accent={T.blurple} />
          <StatCard label="Avg tokens out" value={N.llm.avgTokensOut}  sub="per completion"  accent={T.blurple} />
          <StatCard label="Context usage"  value={N.llm.ctxUsagePct}  unit="%" sub="avg window fill" accent={T.yellow} />
          <StatCard label="Timeouts today" value={N.llm.timeoutsToday} sub={`of ${N.llm.requestsToday} requests`} accent={T.red} />
        </div>
        <div style={{ height: 8 }} />
      </div>

      {mock && (
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
            }}>Preview only</div>
            <div style={{ fontSize: 13, color: T.text, lineHeight: 1.5 }}>
              Stats wire up in a later phase. The numbers shown are static
              mock data from the design mockup — none of the underlying
              subsystems (soundboard, voice, wake-word, STT, TTS, LLM) are
              running yet.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
