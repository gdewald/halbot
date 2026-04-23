// voice-primitives.jsx — voice-channel visual primitives for Halbot mocks.
// Not a Discord clone; a generic "voice room" surface that reads as a real
// voice channel UI. Used for the wake-word flow.

function VoiceFrame({ channel='Main Hall', speakers=[], event, height=760 }) {
  return (
    <div style={{
      width:'100%', height,
      background: hb.bg, color: hb.text,
      fontFamily: hb.ui, fontSize: 14,
      display:'flex', flexDirection:'column', overflow:'hidden',
    }}>
      <div style={{
        display:'flex', alignItems:'center', gap:12,
        padding:'12px 20px', borderBottom:`1px solid ${hb.line}`,
        background: hb.bg2,
      }}>
        <span style={{ color: hb.good, fontFamily: hb.mono, fontSize:14 }}>🔊</span>
        <div style={{ fontWeight:600, fontSize:15 }}>{channel}</div>
        <div style={{ width:1, height:16, background: hb.line }} />
        <div style={{ color: hb.dim, fontSize:13, flex:1 }}>{speakers.length} in voice</div>
        <div style={{ color: hb.good, fontFamily: hb.mono, fontSize:11 }}>● CONNECTED</div>
      </div>

      <div style={{ flex:1, padding:'28px 24px 18px', display:'flex', flexDirection:'column', gap:24, overflow:'hidden' }}>
        <div style={{ display:'grid', gridTemplateColumns:'repeat(3, 1fr)', gap:14 }}>
          {speakers.map((s,i) => <VoiceTile key={i} {...s}/>)}
        </div>

        <div style={{ flex:1, display:'flex', flexDirection:'column', justifyContent:'flex-end', gap:10 }}>
          {event}
        </div>
      </div>

      <div style={{
        display:'flex', alignItems:'center', justifyContent:'center', gap:10,
        padding:'14px 20px', borderTop:`1px solid ${hb.line}`, background: hb.bg,
      }}>
        <VoiceBtn icon="🎤" label="Mute" />
        <VoiceBtn icon="🎧" label="Deafen" />
        <VoiceBtn icon="📺" label="Share" />
        <VoiceBtn icon="📞" label="Disconnect" danger />
      </div>
    </div>
  );
}

function VoiceTile({ user, speaking=false, muted=false, bot=false, badge }) {
  const ringColor = speaking ? hb.good : 'transparent';
  return (
    <div style={{
      background: hb.bg2, borderRadius: 10, border:`1px solid ${hb.line}`,
      aspectRatio: '4 / 3',
      padding:'14px', display:'flex', flexDirection:'column',
      alignItems:'center', justifyContent:'center', gap:10,
      position:'relative',
      boxShadow: speaking ? `0 0 0 2px ${hb.good}` : 'none',
      transition:'box-shadow .2s',
    }}>
      <div style={{ position:'relative' }}>
        <Avatar user={user} size={64} />
        {speaking && <div style={{
          position:'absolute', inset: -6, borderRadius: bot ? 14 : '50%',
          border: `2px solid ${ringColor}`,
        }}/>}
      </div>
      <div style={{ display:'flex', alignItems:'center', gap:6 }}>
        <div style={{ color: bot ? hb.amber : hb.text, fontWeight:600, fontSize:13.5 }}>{user.name}</div>
        {bot && <span style={{
          fontFamily: hb.mono, fontSize:9, color: hb.amberInk,
          background: hb.amber, borderRadius:3, padding:'1px 4px', letterSpacing:'0.04em',
        }}>APP</span>}
      </div>
      {muted && (
        <div style={{ position:'absolute', top:10, right:10, color: hb.bad, fontSize:14 }}>🎤✕</div>
      )}
      {badge && (
        <div style={{
          position:'absolute', top:10, left:10,
          fontFamily: hb.mono, fontSize:10, letterSpacing:'0.1em', textTransform:'uppercase',
          color: hb.amber,
        }}>{badge}</div>
      )}
    </div>
  );
}

function VoiceBtn({ icon, label, danger=false }) {
  return (
    <button style={{
      width:44, height:44, borderRadius:'50%',
      background: danger ? 'oklch(0.55 0.17 25)' : hb.bg3,
      border:`1px solid ${danger ? 'oklch(0.55 0.17 25)' : hb.line}`,
      color: danger ? '#fff' : hb.text, fontSize:18, cursor:'pointer',
      display:'inline-flex', alignItems:'center', justifyContent:'center',
    }} title={label}>{icon}</button>
  );
}

// Toast: Halbot posts in the voice-channel text area / or an overlay.
// We'll render it as a compact card layered over the voice UI.
function VoiceEvent({ children }) {
  return (
    <div style={{
      background: hb.bg2, border:`1px solid ${hb.line}`,
      borderLeft:`4px solid ${hb.amber}`, borderRadius:6,
      padding:'10px 14px', fontSize:13.5, color: hb.text,
      maxWidth: 560, alignSelf:'flex-start',
    }}>
      {children}
    </div>
  );
}

// Fake waveform line (ascii) — shows STT capturing audio.
function Waveform({ active=true }) {
  const bars = '▁▂▄▆█▆▄▃▂▁▂▄▅▇▇▅▄▃▂▁▂▃▅▆▆▅▃▂▁▁▂▃▄▄▃▂▁';
  return (
    <div style={{
      fontFamily: hb.mono, fontSize: 12,
      color: active ? hb.good : hb.faint,
      letterSpacing: '1.2px',
    }}>{bars}</div>
  );
}

Object.assign(window, { VoiceFrame, VoiceTile, VoiceBtn, VoiceEvent, Waveform });
