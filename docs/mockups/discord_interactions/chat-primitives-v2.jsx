// chat-primitives-v2.jsx
// Halbot primitives — Discord-accurate pass.
// Only renders things Discord bots can ACTUALLY produce:
//   · Embeds (color, author, title, description, fields (name/value, inline), footer, timestamp)
//   · Buttons (5 styles, 5 per row, 5 rows max), Select menus
//   · Reactions, mentions, markdown (**bold**, *em*, `code`, ```blocks```, > quote, spoiler)
//   · Typing indicator, ephemeral replies, modals
//   · Progress via edited messages (just re-render the same embed)

const hb = {
  bg:        'oklch(0.19 0.008 260)',
  bg2:       'oklch(0.22 0.009 260)',
  bg3:       'oklch(0.26 0.010 260)',
  line:      'oklch(0.32 0.012 260)',
  lineSoft:  'oklch(0.28 0.010 260 / 0.7)',
  text:      'oklch(0.96 0.008 100)',
  dim:       'oklch(0.74 0.010 260)',
  faint:     'oklch(0.56 0.012 260)',
  amber:     'oklch(0.78 0.15 70)',
  amberInk:  'oklch(0.30 0.08 60)',
  amberSoft: 'oklch(0.78 0.15 70 / 0.14)',
  good:      'oklch(0.78 0.14 155)',
  warn:      'oklch(0.80 0.15 45)',
  bad:       'oklch(0.70 0.17 25)',
  violet:    'oklch(0.72 0.14 300)',
  cyan:      'oklch(0.80 0.11 210)',
  ui:        "'Inter Tight', ui-sans-serif, system-ui, sans-serif",
  mono:      "'JetBrains Mono', ui-monospace, monospace",
};

// ─── Avatars ────────────────────────────────────────────────────────────────
function Avatar({ user, size=36 }) {
  const palette = {
    nico:  { bg: 'oklch(0.62 0.14 30)',  ink: '#1a0f08' },
    aria:  { bg: 'oklch(0.70 0.10 200)', ink: '#051418' },
    petra: { bg: 'oklch(0.66 0.12 320)', ink: '#140516' },
    dev:   { bg: 'oklch(0.68 0.11 140)', ink: '#04170d' },
    halbot:{ bg: hb.amber,               ink: '#1d0c02' },
    system:{ bg: hb.bg3,                 ink: hb.dim },
  };
  const p = palette[user.key] || palette.system;
  const initials = user.initials || (user.name || '?').slice(0,1).toUpperCase();
  const isBot = user.key === 'halbot';
  return (
    <div style={{
      position:'relative',
      width:size, height:size, borderRadius: isBot ? 10 : '50%',
      background:p.bg, color:p.ink,
      display:'flex', alignItems:'center', justifyContent:'center',
      fontFamily: isBot ? hb.mono : hb.ui,
      fontWeight: 700, fontSize: size*0.38,
      letterSpacing: isBot ? '0.05em' : 0,
      flexShrink:0,
    }}>
      {isBot ? '✦' : initials}
    </div>
  );
}

function ChannelHeader({ name='soundboard-lab', topic='audio chaos · ops log · halbot lives here', members=14 }) {
  return (
    <div style={{
      display:'flex', alignItems:'center', gap:14,
      padding:'12px 20px',
      borderBottom:`1px solid ${hb.line}`,
      background: hb.bg2,
    }}>
      <span style={{ color: hb.faint, fontFamily: hb.mono, fontSize:16 }}>#</span>
      <div style={{ fontWeight:600, fontSize:15 }}>{name}</div>
      <div style={{ width:1, height:16, background: hb.line }} />
      <div style={{ color: hb.dim, fontSize:13, flex:1, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{topic}</div>
      <div style={{ color: hb.faint, fontSize:12, fontFamily: hb.mono }}>{members} online</div>
    </div>
  );
}

function DayDivider({ label }) {
  return (
    <div style={{ display:'flex', alignItems:'center', gap:12, padding:'4px 20px', margin:'6px 0' }}>
      <div style={{ flex:1, height:1, background: hb.lineSoft }} />
      <div style={{ color: hb.faint, fontFamily: hb.mono, fontSize:11, letterSpacing:'0.08em', textTransform:'uppercase' }}>{label}</div>
      <div style={{ flex:1, height:1, background: hb.lineSoft }} />
    </div>
  );
}

// ─── Message row ────────────────────────────────────────────────────────────
function Message({ user, time, compact=false, children, ephemeral=false, edited=false }) {
  return (
    <div style={{
      display:'flex', gap:14,
      padding: compact ? '1px 20px' : '8px 20px',
      paddingTop: compact ? 1 : 10,
      background: 'transparent',
      position:'relative',
    }}>
      <div style={{ width:36, flexShrink:0 }}>
        {!compact && <Avatar user={user} />}
      </div>
      <div style={{ flex:1, minWidth:0 }}>
        {!compact && (
          <div style={{ display:'flex', alignItems:'baseline', gap:10, marginBottom: 2 }}>
            <span style={{ fontWeight:600, fontSize:14.5, color: user.key==='halbot' ? hb.amber : hb.text }}>
              {user.name}
            </span>
            {user.key==='halbot' && (
              <span style={{
                fontFamily: hb.mono, fontSize:10, color: hb.amberInk,
                background: hb.amber, borderRadius: 4, padding:'1px 5px', letterSpacing:'0.04em',
              }}>APP</span>
            )}
            <span style={{ color: hb.faint, fontFamily: hb.mono, fontSize:11 }}>{time}{edited && <span style={{ marginLeft: 6 }}>(edited)</span>}</span>
          </div>
        )}
        <div style={{ color: hb.text, fontSize:14.5, lineHeight:1.5 }}>
          {children}
        </div>
        {ephemeral && (
          <div style={{
            marginTop: 6, color: hb.faint, fontFamily: hb.mono, fontSize: 11,
            display:'flex', alignItems:'center', gap:6,
          }}>
            <span>🛈</span>
            <span>Only you can see this · <span style={{ textDecoration:'underline' }}>Dismiss message</span></span>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Markdown primitives ────────────────────────────────────────────────────
// Only what Discord markdown actually supports.
function Mention({ children, color=hb.amber, kind='user' }) {
  const prefix = kind === 'channel' ? '#' : kind === 'role' ? '@' : '@';
  return (
    <span style={{
      color: color,
      background: `color-mix(in oklch, ${color} 18%, transparent)`,
      padding: '0 4px', borderRadius: 4, fontWeight: 500,
    }}>{prefix}{children}</span>
  );
}
// Inline `code` — Discord renders white-on-dark, no per-token color.
function Code({ children }) {
  return (
    <span style={{
      fontFamily: hb.mono, fontSize: '0.9em',
      background: 'oklch(0.15 0.006 260)', color: hb.text,
      padding: '1px 5px', borderRadius: 3,
    }}>{children}</span>
  );
}
// ```fenced``` code block
function CodeBlock({ lang='', children }) {
  return (
    <pre style={{
      margin: '6px 0 0', padding: '10px 12px',
      background: 'oklch(0.15 0.006 260)',
      border:`1px solid ${hb.line}`,
      borderRadius: 6,
      fontFamily: hb.mono, fontSize: 12.5, color: hb.text,
      overflow: 'hidden', whiteSpace: 'pre-wrap', lineHeight: 1.5,
    }}>
      {lang && <div style={{ color: hb.faint, fontSize: 10.5, marginBottom: 4, letterSpacing:'0.08em' }}>{lang}</div>}
      {children}
    </pre>
  );
}
// > block quote
function Quote({ children }) {
  return (
    <div style={{
      borderLeft: `4px solid ${hb.line}`,
      paddingLeft: 10, color: hb.dim, margin: '4px 0',
    }}>{children}</div>
  );
}
// -# subtext (Discord's small grey line — real feature)
function Subtext({ children }) {
  return <div style={{ color: hb.faint, fontSize: 12, marginTop: 2 }}>{children}</div>;
}

// ─── Embed (true Discord embed shape) ───────────────────────────────────────
// Props model the real embed object: color, author, title, url, description,
// fields[{name,value,inline}], footer{text,icon}, timestamp, thumbnail.
function Embed({
  color=hb.amber,
  author,            // {name, icon?}
  title, titleUrl,
  description,
  fields=[],         // [{ name, value, inline }]
  image,             // {w,h,label}  (reserved thumbnail)
  footer,            // {text, icon}
  timestamp,
  children,          // we allow arbitrary JSX in description for demo prose
}) {
  return (
    <div style={{
      marginTop: 6, maxWidth: 560,
      background: hb.bg2,
      borderRadius: 4,
      borderLeft: `4px solid ${color}`,
      overflow:'hidden',
      fontSize: 13.5,
    }}>
      <div style={{ padding:'10px 14px 12px' }}>
        {author && (
          <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom: 6, color: hb.text, fontSize: 12.5, fontWeight: 500 }}>
            {author.icon && <span style={{
              width:20, height:20, borderRadius:'50%',
              background: hb.bg3, color: hb.amber,
              display:'inline-flex', alignItems:'center', justifyContent:'center',
              fontFamily: hb.mono, fontSize: 11,
            }}>{author.icon}</span>}
            <span>{author.name}</span>
          </div>
        )}
        {title && (
          <div style={{ fontWeight:600, fontSize:14.5, color: titleUrl ? hb.cyan : hb.text, marginBottom: description||children||fields.length?6:0 }}>
            {title}
          </div>
        )}
        {(description || children) && (
          <div style={{ color: hb.text, fontSize:13.5, lineHeight:1.5, whiteSpace:'pre-wrap' }}>
            {description}{children}
          </div>
        )}
        {fields.length > 0 && (
          <EmbedFields fields={fields} hasDescription={!!(description || children)} />
        )}
        {footer && (
          <div style={{
            marginTop: 10, paddingTop: 8,
            display:'flex', alignItems:'center', gap:8,
            color: hb.faint, fontSize: 11.5,
          }}>
            {footer.icon && <span style={{ fontFamily: hb.mono }}>{footer.icon}</span>}
            <span>{footer.text}</span>
            {timestamp && <><span>·</span><span>{timestamp}</span></>}
          </div>
        )}
      </div>
    </div>
  );
}

// Discord packs inline fields side-by-side (up to 3 per row depending on width).
// Block (inline:false) fields span full width. This mimics that layout.
function EmbedFields({ fields, hasDescription }) {
  // Group consecutive inline fields into rows.
  const rows = [];
  let cur = null;
  fields.forEach(f => {
    if (f.inline) {
      if (!cur) { cur = []; rows.push(cur); }
      cur.push(f);
    } else {
      cur = null;
      rows.push([f]);
    }
  });
  return (
    <div style={{ marginTop: hasDescription ? 10 : 0, display:'flex', flexDirection:'column', gap: 8 }}>
      {rows.map((row, ri) => (
        <div key={ri} style={{ display:'flex', gap: 14 }}>
          {row.map((f, fi) => (
            <div key={fi} style={{ flex: f.inline ? 1 : '0 0 100%', minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 12.5, color: hb.text, marginBottom: 2 }}>{f.name}</div>
              <div style={{ color: hb.dim, fontSize: 13, whiteSpace:'pre-wrap', lineHeight: 1.45 }}>{f.value}</div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

// ─── Action row: up to 5 buttons, up to 5 rows per message ──────────────────
function ActionRow({ children }) {
  return <div style={{ display:'flex', flexWrap:'wrap', gap:8, marginTop:8 }}>{children}</div>;
}

// Discord's 5 button styles: primary (blurple), secondary (grey), success (green),
// danger (red), link (grey w/ link icon). Emojis allowed.
function Btn({ children, style='secondary', emoji, disabled=false }) {
  const map = {
    primary:   { bg: 'oklch(0.55 0.16 265)', fg:'#fff', border:'oklch(0.55 0.16 265)' },
    secondary: { bg: hb.bg3, fg: hb.text, border: hb.bg3 },
    success:   { bg: 'oklch(0.52 0.13 155)', fg:'#fff', border:'oklch(0.52 0.13 155)' },
    danger:    { bg: 'oklch(0.55 0.17 25)',  fg:'#fff', border:'oklch(0.55 0.17 25)' },
    link:      { bg: hb.bg3, fg: hb.text, border: hb.bg3, link:true },
  }[style] || {};
  return (
    <button style={{
      background: map.bg, color: map.fg, border:`1px solid ${map.border}`,
      padding:'7px 14px', borderRadius:3,
      fontFamily: hb.ui, fontWeight: 500, fontSize: 13.5,
      cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.5 : 1,
      display:'inline-flex', alignItems:'center', gap:6,
    }}>
      {emoji && <span>{emoji}</span>}
      {children}
      {map.link && <span style={{ opacity: 0.6, fontSize: 11, marginLeft: 2 }}>↗</span>}
    </button>
  );
}

// ─── Select menu (StringSelect) — one-per-row, real Discord component ───────
function SelectMenu({ placeholder='Make a selection', selected, options=[] }) {
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{
        background: hb.bg3, border: `1px solid ${hb.line}`, borderRadius: 3,
        padding: '9px 12px', display:'flex', alignItems:'center', gap: 10,
        color: selected ? hb.text : hb.faint, fontSize: 13.5,
        cursor: 'pointer',
      }}>
        <span style={{ flex:1 }}>{selected || placeholder}</span>
        <span style={{ color: hb.faint }}>▾</span>
      </div>
      {options.length > 0 && (
        <div style={{
          marginTop: -1,
          background: hb.bg2, border:`1px solid ${hb.line}`, borderTop: 'none',
          borderRadius: '0 0 3px 3px', overflow: 'hidden',
        }}>
          {options.map((o, i) => (
            <div key={i} style={{
              padding: '8px 12px',
              display:'flex', alignItems:'flex-start', gap: 10,
              borderTop: i > 0 ? `1px solid ${hb.lineSoft}` : 'none',
              background: o.hover ? hb.bg3 : 'transparent',
            }}>
              {o.emoji && <span style={{ fontSize: 16, lineHeight: '20px' }}>{o.emoji}</span>}
              <div style={{ flex:1, minWidth: 0 }}>
                <div style={{ color: hb.text, fontSize: 13.5, fontWeight: 500 }}>{o.label}</div>
                {o.desc && <div style={{ color: hb.faint, fontSize: 12, marginTop: 1 }}>{o.desc}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Reactions ──────────────────────────────────────────────────────────────
function Reactions({ items=[] }) {
  return (
    <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginTop:6 }}>
      {items.map((r,i) => (
        <div key={i} style={{
          display:'flex', alignItems:'center', gap:5,
          padding:'2px 8px', borderRadius: 10,
          background: r.me ? hb.amberSoft : hb.bg3,
          border: `1px solid ${r.me ? hb.amber : hb.line}`,
          fontFamily: hb.mono, fontSize: 11.5, color: r.me ? hb.amber : hb.dim,
        }}>
          <span>{r.emoji}</span><span>{r.n}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Typing indicator ───────────────────────────────────────────────────────
function Typing({ who='Halbot' }) {
  return (
    <div style={{ padding:'4px 20px 6px', color: hb.faint, fontSize: 12, fontFamily: hb.mono, display:'flex', alignItems:'center', gap:8 }}>
      <span style={{ display:'inline-flex', gap: 3 }}>
        <Dot /><Dot d={0.15}/><Dot d={0.3}/>
      </span>
      {who} is typing…
    </div>
  );
}
function Dot({ d=0 }) {
  return <span style={{
    width:4, height:4, borderRadius:'50%', background: hb.amber,
    animation:`hb-bounce 1s ${d}s infinite ease-in-out`,
    display:'inline-block',
  }}/>;
}

// ─── Modal (real Discord component — pops on button click) ──────────────────
function Modal({ title, children, submitLabel='Submit' }) {
  return (
    <div style={{
      marginTop: 6, maxWidth: 520,
      background: hb.bg2,
      border: `1px solid ${hb.line}`,
      borderRadius: 8,
      boxShadow: '0 8px 40px rgba(0,0,0,0.5)',
      overflow: 'hidden',
    }}>
      <div style={{ padding:'12px 16px 4px' }}>
        <div style={{ color: hb.faint, fontFamily: hb.mono, fontSize: 10.5, letterSpacing:'0.1em', textTransform:'uppercase' }}>Modal · opened by button</div>
        <div style={{ fontWeight: 600, fontSize: 16, marginTop: 4 }}>{title}</div>
      </div>
      <div style={{ padding: '8px 16px 14px' }}>
        {children}
      </div>
      <div style={{
        display:'flex', justifyContent:'flex-end', gap: 8,
        padding:'10px 16px', background: hb.bg3,
        borderTop: `1px solid ${hb.line}`,
      }}>
        <Btn style="link">Cancel</Btn>
        <Btn style="primary">{submitLabel}</Btn>
      </div>
    </div>
  );
}
function ModalInput({ label, placeholder, required=false, value, hint }) {
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ color: hb.faint, fontFamily: hb.mono, fontSize: 10.5, letterSpacing:'0.1em', textTransform:'uppercase', marginBottom: 6 }}>
        {label} {required && <span style={{ color: hb.bad }}>*</span>}
      </div>
      <div style={{
        background: hb.bg,
        border:`1px solid ${hb.line}`, borderRadius: 3,
        padding: '9px 11px', color: value ? hb.text : hb.faint, fontSize: 13.5,
      }}>
        {value || placeholder}
      </div>
      {hint && <div style={{ color: hb.faint, fontSize: 11, marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

// ─── Input bar (decorative) ─────────────────────────────────────────────────
function InputBar({ placeholder='Message #soundboard-lab', mention }) {
  return (
    <div style={{ padding:'12px 16px', borderTop:`1px solid ${hb.line}`, background: hb.bg }}>
      <div style={{
        display:'flex', alignItems:'center', gap:10,
        background: hb.bg2, border:`1px solid ${hb.line}`, borderRadius:8,
        padding:'10px 14px',
      }}>
        <span style={{ color: hb.faint, fontFamily: hb.mono }}>＋</span>
        <div style={{ flex:1, color: mention ? hb.text : hb.faint, fontSize: 13.5 }}>
          {mention ? (
            <>
              <Mention>Halbot</Mention> <span style={{ color: hb.text }}>{mention}</span>
              <span style={{ display:'inline-block', width: 8, height: 16, background: hb.amber, verticalAlign: -3, marginLeft: 2, animation: 'hb-blink 1s steps(2) infinite' }}/>
            </>
          ) : placeholder}
        </div>
        <span style={{ color: hb.faint, fontFamily: hb.mono, fontSize:11 }}>↵ send</span>
      </div>
    </div>
  );
}

function ChatFrame({ channel='soundboard-lab', topic, children, input, height=760, members=14 }) {
  return (
    <div style={{
      width:'100%', height,
      background: hb.bg, color: hb.text,
      fontFamily: hb.ui, fontSize: 14,
      display:'flex', flexDirection:'column',
      overflow:'hidden',
    }}>
      <ChannelHeader name={channel} topic={topic} members={members}/>
      <div style={{ flex:1, overflow:'hidden', display:'flex', flexDirection:'column', justifyContent:'flex-end' }}>
        <div style={{ padding:'8px 0 10px' }}>
          {children}
        </div>
      </div>
      {input !== false && (input || <InputBar/>)}
    </div>
  );
}

if (typeof document !== 'undefined' && !document.getElementById('hb-kf')) {
  const s = document.createElement('style');
  s.id = 'hb-kf';
  s.textContent = `
    @keyframes hb-bounce { 0%,80%,100%{ transform: translateY(0); opacity: 0.4 } 40%{ transform: translateY(-3px); opacity: 1 } }
    @keyframes hb-blink { 50% { opacity: 0 } }
  `;
  document.head.appendChild(s);
}

const USERS = {
  nico:  { key:'nico',  name:'nico',         initials:'N' },
  aria:  { key:'aria',  name:'aria.lin',     initials:'A' },
  petra: { key:'petra', name:'petra_kx',     initials:'P' },
  dev:   { key:'dev',   name:'devin',        initials:'D' },
  halbot:{ key:'halbot',name:'Halbot',       initials:'H' },
  system:{ key:'system',name:'system',       initials:'S' },
};

Object.assign(window, {
  hb, Avatar, ChannelHeader, DayDivider, Message,
  Mention, Code, CodeBlock, Quote, Subtext,
  Embed, ActionRow, Btn, SelectMenu, Reactions, Typing,
  Modal, ModalInput, InputBar, ChatFrame, USERS,
});
