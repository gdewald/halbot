// flows-v3.jsx — Halbot conversations modeled on interactions.md.
// Surfaces: Discord text channel + voice channel (user-facing only).
// Uses only real Discord bot primitives from chat-primitives-v2.jsx.

// ─────────────────────────────────────────────────────────────
// 01 · Mention routing — anything in @ gets LLM-handled, with
//       ~50-msg channel context. Covers free-form chat + intent.
// ─────────────────────────────────────────────────────────────
function FlowMention() {
  return (
    <ChatFrame channel="soundboard-lab" topic="sounds · banter · halbot (context window: 50)" input={<InputBar/>}>
      <DayDivider label="Today · 3:40 PM" />

      <Message user={USERS.nico} time="3:38 PM">tacos tonight?</Message>
      <Message user={USERS.aria} time="3:39 PM" compact>obviously</Message>
      <Message user={USERS.petra} time="3:40 PM">
        <Mention>Halbot</Mention> play that taco sound from earlier, the screwed one
      </Message>

      <Typing who="Halbot" />

      <Message user={USERS.halbot} time="3:40 PM">
        <Subtext>*Used last 50 messages as context · resolved "screwed one" → `taco-bell--screwed.mp3`*</Subtext>
        <Embed
          color={hb.amber}
          author={{ name: 'Halbot · soundboard', icon: '✦' }}
          title="▶ Playing taco-bell--screwed.mp3"
          description={'Queued to the current voice session. 0:08, -6st, 0.5×.'}
          fields={[
            { name: 'From', value: '`library`', inline: true },
            { name: 'Voice', value: '#main-hall', inline: true },
            { name: 'Requested', value: 'petra_kx', inline: true },
          ]}
          footer={{ text: 'Mention intent · soundboard.play' }}
        />
        <ActionRow>
          <Btn style="secondary" emoji="⏹">Stop</Btn>
          <Btn style="secondary" emoji="↺">Replay</Btn>
          <Btn style="secondary" emoji="🔊">Louder</Btn>
        </ActionRow>
      </Message>
    </ChatFrame>
  );
}

// ─────────────────────────────────────────────────────────────
// 02 · Upload — attaching audio in a mention saves it as a sound.
// ─────────────────────────────────────────────────────────────
function FlowUpload() {
  return (
    <ChatFrame channel="soundboard-lab" topic="sounds · banter · halbot" input={<InputBar/>}>
      <DayDivider label="Today · 1:14 PM" />

      <Message user={USERS.nico} time="1:14 PM">
        <Mention>Halbot</Mention> save this as <Code>wilhelm-hd</Code>, emoji 😱
        <AttachmentCard name="wilhelm-hd-44k.wav" size="312 KB" length="0:01" />
      </Message>

      <Message user={USERS.halbot} time="1:14 PM">
        <Subtext>*Attached audio detected · intent: soundboard.save · name: `wilhelm-hd` · emoji: 😱*</Subtext>
        <Embed
          color={hb.good}
          author={{ name: 'Halbot · saved', icon: '✦' }}
          title="Saved wilhelm-hd to the soundboard"
          fields={[
            { name: 'Slot',   value: '**#15** of 24', inline: true },
            { name: 'Size',   value: '312 KB',        inline: true },
            { name: 'Length', value: '0:01',          inline: true },
            { name: 'Emoji',  value: '😱',            inline: true },
            { name: 'Saved by', value: 'nico',        inline: true },
            { name: 'Format', value: '`WAV 44.1k mono`', inline: true },
          ]}
          footer={{ text: 'Soft-deletable · recoverable via admin undelete' }}
        />
        <ActionRow>
          <Btn style="primary" emoji="▶">Play now</Btn>
          <Btn style="secondary" emoji="✏️">Rename</Btn>
          <Btn style="secondary" emoji="🎛️">Edit</Btn>
          <Btn style="danger" emoji="🗑️">Remove</Btn>
        </ActionRow>
      </Message>
    </ChatFrame>
  );
}

// A small file-attachment card — mimics Discord's audio attachment bubble.
function AttachmentCard({ name, size, length }) {
  return (
    <div style={{
      marginTop: 6, maxWidth: 380,
      background: hb.bg2, borderRadius: 6, border: `1px solid ${hb.line}`,
      padding: '10px 12px', display:'flex', alignItems:'center', gap: 12,
    }}>
      <div style={{
        width: 36, height: 36, borderRadius: 6, background: hb.bg3,
        display:'flex', alignItems:'center', justifyContent:'center',
        color: hb.cyan, fontSize: 16,
      }}>♪</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13.5, color: hb.cyan, fontWeight: 500, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{name}</div>
        <div style={{ fontSize: 11.5, color: hb.faint, fontFamily: hb.mono, marginTop: 1 }}>{size} · {length}</div>
        <div style={{
          marginTop: 6, height: 14, background: hb.bg3, borderRadius: 2, position:'relative',
          display:'flex', alignItems:'center', padding: '0 6px',
          fontFamily: hb.mono, fontSize: 9, color: hb.faint, letterSpacing: '1px',
        }}>▁▂▅█▇▅▃▂▁▂▄▇█▅▃▁▁▂▄▆▇▅▃▂▁</div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// 03 · Text trigger fires — user typed a trigger phrase; bot
//       auto-fires a reply (or voice_play). No mention needed.
// ─────────────────────────────────────────────────────────────
function FlowTextTrigger() {
  return (
    <ChatFrame channel="general" topic="banter · halbot listens for triggers here" input={<InputBar/>}>
      <DayDivider label="Today · 11:52 AM" />

      <Message user={USERS.aria} time="11:51 AM">sprint demo at 2, don't forget</Message>
      <Message user={USERS.nico} time="11:51 AM" compact>lol devin is gonna <b>forget</b> again</Message>
      <Message user={USERS.dev} time="11:52 AM">i would never. <b>skill issue</b> honestly</Message>

      <Message user={USERS.halbot} time="11:52 AM">
        <Subtext>*Text trigger fired · phrase: "skill issue" · action: reply · fires: 23*</Subtext>
        <Embed
          color={hb.violet}
          author={{ name: 'Halbot · trigger', icon: '⚡' }}
          title="ratio + skill issue + touch grass"
          description={'*(configured reply — no mention needed)*'}
          fields={[
            { name: 'Matched phrase', value: '`skill issue`', inline: true },
            { name: 'Scope',          value: 'this guild',    inline: true },
            { name: 'Fire count',     value: '**23**',        inline: true },
          ]}
          footer={{ text: 'Owner can tune triggers in the dashboard' }}
        />
        <ActionRow>
          <Btn style="secondary" emoji="🔕">Mute trigger here</Btn>
          <Btn style="link" emoji="⚙️">See triggers</Btn>
        </ActionRow>
        <Reactions items={[{emoji:'💀',n:4},{emoji:'🎯',n:2,me:true}]}/>
      </Message>

      <Message user={USERS.dev} time="11:52 AM" compact>bro</Message>
    </ChatFrame>
  );
}

// ─────────────────────────────────────────────────────────────
// 04 · Voice wake-word — "Halbot, …" captured via VAD + STT,
//       reply spoken via TTS, transcript surfaced as a card.
// ─────────────────────────────────────────────────────────────
function FlowVoiceWake() {
  return (
    <VoiceFrame
      channel="Main Hall"
      speakers={[
        { user: USERS.nico,   speaking: false },
        { user: USERS.petra,  speaking: true, badge: '● Speaking' },
        { user: USERS.aria,   muted: true },
        { user: USERS.halbot, bot: true, badge: '◆ Listening' },
      ]}
      event={
        <>
          <VoiceEvent>
            <div style={{ display:'flex', alignItems:'center', gap:10 }}>
              <span style={{ color: hb.amber, fontFamily: hb.mono, fontSize: 11, letterSpacing:'0.1em' }}>WAKE</span>
              <span style={{ color: hb.dim, fontFamily: hb.mono, fontSize: 12 }}>"Halbot…"</span>
              <span style={{ flex: 1 }}/>
              <span style={{ color: hb.faint, fontFamily: hb.mono, fontSize: 11 }}>wake→join 180ms</span>
            </div>
            <div style={{ marginTop: 8 }}>
              <Waveform active />
              <div style={{ marginTop: 6, color: hb.text, fontSize: 14, lineHeight: 1.5 }}>
                <span style={{ color: hb.good }}>▸</span> <b>petra</b>: <i>"Halbot, play the sad trombone and then tell us a dad joke."</i>
              </div>
              <div style={{ marginTop: 4, color: hb.faint, fontSize: 11, fontFamily: hb.mono }}>
                VAD end-of-utterance · 1.52s silence · faster-whisper · session turn 3/10
              </div>
            </div>
          </VoiceEvent>

          <VoiceEvent>
            <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom: 6 }}>
              <span style={{ color: hb.amber, fontFamily: hb.mono, fontSize: 11, letterSpacing:'0.1em' }}>HALBOT</span>
              <span style={{ color: hb.dim, fontFamily: hb.mono, fontSize: 12 }}>▶ TTS · speaking</span>
              <span style={{ flex: 1 }}/>
              <span style={{ color: hb.faint, fontFamily: hb.mono, fontSize: 11 }}>LLM 640ms · TTS 220ms</span>
            </div>
            <div style={{ color: hb.text, fontSize: 14, lineHeight: 1.5 }}>
              <span style={{ color: hb.amber }}>▸</span> <i>"Playing sad-trombone.wav. Why did the scarecrow get a promotion? He was outstanding in his field."</i>
            </div>
          </VoiceEvent>
        </>
      }
    />
  );
}

// ─────────────────────────────────────────────────────────────
// 05 · Voice trigger — phrase fires in STT transcript, no wake word.
// ─────────────────────────────────────────────────────────────
function FlowVoiceTrigger() {
  return (
    <VoiceFrame
      channel="Main Hall"
      speakers={[
        { user: USERS.dev,    speaking: true, badge: '● Speaking' },
        { user: USERS.nico,   muted: true },
        { user: USERS.halbot, bot: true, badge: '◆ Firing' },
      ]}
      event={
        <VoiceEvent>
          <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom: 6 }}>
            <span style={{ color: hb.violet, fontFamily: hb.mono, fontSize: 11, letterSpacing:'0.1em' }}>VOICE TRIGGER</span>
            <span style={{ color: hb.dim, fontFamily: hb.mono, fontSize: 12 }}>no wake word needed</span>
            <span style={{ flex: 1 }}/>
            <span style={{ color: hb.faint, fontFamily: hb.mono, fontSize: 11 }}>match 40ms</span>
          </div>
          <div style={{ color: hb.text, fontSize: 14, lineHeight: 1.5 }}>
            <span style={{ color: hb.good }}>▸</span> <b>devin</b>: <i>"…and then the PM was like, that's a </i><span style={{ background: hb.amberSoft, color: hb.amber, padding:'0 4px', borderRadius: 3 }}>skill issue</span><i>, honestly."</i>
          </div>
          <div style={{ marginTop: 10, padding: '8px 10px', background: hb.bg3, borderRadius: 4, border: `1px solid ${hb.line}` }}>
            <div style={{ color: hb.amber, fontSize: 12.5 }}>
              ▶ Playing <b>vine-boom.mp3</b> <span style={{ color: hb.faint }}>· bound to "skill issue" · action voice_play</span>
            </div>
          </div>
          <div style={{ marginTop: 6, color: hb.faint, fontSize: 11, fontFamily: hb.mono }}>
            rolling transcript history: 7 of 10 turns
          </div>
        </VoiceEvent>
      }
    />
  );
}

// ─────────────────────────────────────────────────────────────
// 06 · Admin status — owner-only prefix command
// ─────────────────────────────────────────────────────────────
function FlowAdminStatus() {
  return (
    <ChatFrame channel="halbot-admin" topic="private · owner only" input={<InputBar/>}>
      <DayDivider label="Today · 9:05 AM" />

      <Message user={USERS.nico} time="9:05 AM">
        <Code>!halbot admin status</Code>
      </Message>

      <Message user={USERS.halbot} time="9:05 AM">
        <Subtext>*Admin shell · caller verified as guild owner · `nico`*</Subtext>
        <Embed
          color={hb.cyan}
          author={{ name: 'Halbot · admin/status', icon: '✦' }}
          title="Store counts · live vs. tombstoned"
          description={'```\nkind         live   tombstoned\n─────────── ────── ───────────\nsounds         24            3\npersonas        4            1\nfacts          87           12\ntriggers       16            0\ngrudges         2            5\n```'}
          fields={[
            { name: 'Retention',  value: 'Tombstones kept until `purge` · default 30d', inline: true },
            { name: 'Next purge', value: 'manual only · `!halbot admin purge`',          inline: true },
          ]}
          footer={{ text: 'owner-only · !halbot admin help for subcommands' }}
        />
        <ActionRow>
          <Btn style="secondary" emoji="🪦">View deleted</Btn>
          <Btn style="secondary" emoji="↶">Undelete…</Btn>
          <Btn style="danger" emoji="⚠️">Panic…</Btn>
          <Btn style="link" emoji="📖">admin help</Btn>
        </ActionRow>
      </Message>
    </ChatFrame>
  );
}

// ─────────────────────────────────────────────────────────────
// 07 · Admin · deleted + undelete (soft delete recovery)
// ─────────────────────────────────────────────────────────────
function FlowAdminUndelete() {
  return (
    <ChatFrame channel="halbot-admin" topic="private · owner only" input={<InputBar/>}>
      <DayDivider label="Today · 9:08 AM" />

      <Message user={USERS.nico} time="9:08 AM">
        <Code>!halbot admin deleted sounds 5</Code>
      </Message>

      <Message user={USERS.halbot} time="9:08 AM">
        <Embed
          color={hb.warn}
          author={{ name: 'Halbot · admin/deleted', icon: '✦' }}
          title="Tombstoned sounds (5 of 3)"
          description={'All rows below are soft-deleted. Pick one to bring back, or `undelete-all sounds` to restore everything.'}
          fields={[
            { name: 's_2094 · cringe-2023.wav', value: '`0:04` · deleted 14m ago by aria.lin', inline: false },
            { name: 's_1871 · old-ringtone.mp3', value: '`0:08` · deleted 2d ago by nico',     inline: false },
            { name: 's_1420 · vine-boom-OLD.mp3', value: '`0:01` · deleted 3w ago by nico',   inline: false },
          ]}
          footer={{ text: 'Tombstones are recoverable until purge' }}
        />
        <SelectMenu
          placeholder="Undelete one…"
          options={[
            { label: 'cringe-2023.wav',     desc: 's_2094 · 14m ago', emoji: '🪦', hover: true },
            { label: 'old-ringtone.mp3',    desc: 's_1871 · 2d ago',  emoji: '🪦' },
            { label: 'vine-boom-OLD.mp3',   desc: 's_1420 · 3w ago',  emoji: '🪦' },
          ]}
        />
        <ActionRow>
          <Btn style="success" emoji="↶">Undelete all sounds</Btn>
          <Btn style="secondary" emoji="🪦">Show 2 more kinds</Btn>
        </ActionRow>
      </Message>

      <Message user={USERS.nico} time="9:09 AM" compact>
        <Code>!halbot admin undelete sounds s_2094</Code>
      </Message>

      <Message user={USERS.halbot} time="9:09 AM">
        <Embed
          color={hb.good}
          author={{ name: 'Halbot · admin/undelete', icon: '✦' }}
          title="Restored: cringe-2023.wav"
          fields={[
            { name: 'Kind',  value: 'sounds',               inline: true },
            { name: 'ID',    value: '`s_2094`',             inline: true },
            { name: 'State', value: 'live · tombstone gone', inline: true },
          ]}
          footer={{ text: 'Back in the library as if it never left' }}
        />
      </Message>
    </ChatFrame>
  );
}

// ─────────────────────────────────────────────────────────────
// 08 · Admin · panic (with optional `all` to also nuke sounds)
// ─────────────────────────────────────────────────────────────
function FlowAdminPanic() {
  return (
    <ChatFrame channel="halbot-admin" topic="private · owner only" input={<InputBar/>}>
      <DayDivider label="Today · 2:02 AM" />

      <Message user={USERS.nico} time="2:02 AM">
        <Code>!halbot admin panic all</Code>
      </Message>

      <Message user={USERS.halbot} time="2:02 AM">
        <Subtext>*Owner invoked panic · `all` flag present · destructive but soft*</Subtext>
        <Embed
          color={hb.bad}
          author={{ name: 'Halbot · admin/panic', icon: '⚠️' }}
          title="Soft-clear EVERYTHING?"
          description={'This tombstones:'}
          fields={[
            { name: 'Will clear', value: '`personas` (4)\n`facts` (87)\n`triggers` (16)\n`grudges` (2)\n`sounds` (24) ← because `all`', inline: false },
            { name: 'Recoverable?', value: 'Yes. Every row becomes a tombstone. Run `undelete-all <kind>` to bring them back. They become **permanent** only when you `purge`.', inline: false },
          ]}
          footer={{ text: 'Confirmation required · modal will ask for a code word' }}
        />
        <ActionRow>
          <Btn style="danger" emoji="⚠️">Confirm panic…</Btn>
          <Btn style="secondary" emoji="✕">Cancel</Btn>
        </ActionRow>
      </Message>

      <Message user={USERS.nico} time="2:02 AM" compact ephemeral>
        <Modal title="Panic confirmation" submitLabel="Yes, soft-clear all">
          <div style={{ color: hb.dim, fontSize: 13, lineHeight: 1.5 }}>
            Type <Code>PANIC</Code> to confirm. Nothing is permanently deleted — tombstones can be undeleted until you run `purge`.
          </div>
          <ModalInput label="Code word" placeholder="PANIC" value="PANIC" required />
          <ModalInput label="Reason (audit log)" placeholder="what happened?" value="revert test seeds" />
        </Modal>
      </Message>

      <Message user={USERS.halbot} time="2:03 AM">
        <Embed
          color={hb.bad}
          author={{ name: 'Halbot · panic complete', icon: '✦' }}
          title="Soft-cleared. Take a breath."
          fields={[
            { name: 'Tombstoned', value: '`sounds` ×24 · `personas` ×4 · `facts` ×87 · `triggers` ×16 · `grudges` ×2', inline: false },
            { name: 'Undo',       value: '`!halbot admin undelete-all <kind>` · for each kind you want back', inline: false },
            { name: 'Permanent?', value: 'No — only after `!halbot admin purge`', inline: false },
          ]}
          footer={{ text: 'No outgoing Discord side-effects · state is store-only' }}
        />
      </Message>
    </ChatFrame>
  );
}

// ─────────────────────────────────────────────────────────────
// 09 · Personas — user sets a persisted persona + asks one thing
// ─────────────────────────────────────────────────────────────
function FlowPersonas() {
  return (
    <ChatFrame channel="general" topic="banter · halbot personas live here" input={<InputBar/>}>
      <DayDivider label="Today · 4:48 PM" />

      <Message user={USERS.petra} time="4:48 PM">
        <Mention>Halbot</Mention> from now on when i talk to you, be a grumpy night-shift DJ. save it
      </Message>

      <Message user={USERS.halbot} time="4:48 PM">
        <Subtext>*Intent: persona.save · scope: user petra_kx · kind: `personas`*</Subtext>
        <Embed
          color={hb.violet}
          author={{ name: 'Halbot · persona saved', icon: '✦' }}
          title="Grumpy night-shift DJ"
          description={'Got it. When petra_kx pings me, I\'ll channel this. Other users get the default voice.'}
          fields={[
            { name: 'Trigger', value: 'mentions from petra_kx', inline: true },
            { name: 'Scope',   value: 'this guild',             inline: true },
            { name: 'Saved',   value: 'just now',               inline: true },
          ]}
          footer={{ text: 'Edit or remove in the dashboard · or ask me "drop my persona"' }}
        />
        <ActionRow>
          <Btn style="secondary" emoji="📝">Edit wording</Btn>
          <Btn style="secondary" emoji="👥">Make guild-wide</Btn>
          <Btn style="danger" emoji="🗑️">Remove persona</Btn>
        </ActionRow>
      </Message>

      <Message user={USERS.petra} time="4:49 PM">
        <Mention>Halbot</Mention> what's on the board
      </Message>

      <Message user={USERS.halbot} time="4:49 PM">
        <Embed
          color={hb.amber}
          author={{ name: 'Halbot · night shift', icon: '✦' }}
          title="Fine. Fine. Here's the rotation."
          description={'*you want the list, you get the list. keep it down out there.*'}
          fields={[
            { name: 'On deck',   value: '`airhorn` · `wilhelm` · `anakin-nooo` · `bruh` · `dial-up`',  inline: false },
            { name: 'In the crates', value: '`taco-bell--screwed` · `sad-trombone` · `windows-xp` · + 6 more', inline: false },
          ]}
          footer={{ text: 'persona: grumpy night-shift DJ · fires: 1' }}
        />
      </Message>
    </ChatFrame>
  );
}

// ─────────────────────────────────────────────────────────────
// 10 · Grudges + facts — the bot remembers specific things
// ─────────────────────────────────────────────────────────────
function FlowGrudgesFacts() {
  return (
    <ChatFrame channel="general" topic="banter · halbot remembers" input={<InputBar/>}>
      <DayDivider label="Today · 5:20 PM" />

      <Message user={USERS.aria} time="5:20 PM">
        <Mention>Halbot</Mention> remember: devin owes me a coffee, since the staging incident
      </Message>

      <Message user={USERS.halbot} time="5:20 PM">
        <Subtext>*Intent resolved · `fact.add` (about devin) · noted with source message*</Subtext>
        <Embed
          color={hb.cyan}
          author={{ name: 'Halbot · noted', icon: '✦' }}
          title="Fact stored about devin"
          fields={[
            { name: 'Fact',    value: '"owes aria.lin a coffee — staging incident"', inline: false },
            { name: 'Subject', value: 'devin',     inline: true },
            { name: 'Source',  value: 'aria.lin · this msg', inline: true },
            { name: 'Kind',    value: '`facts`',   inline: true },
          ]}
          footer={{ text: 'Facts inform future replies · forget with "halbot forget that"' }}
        />
      </Message>

      <Message user={USERS.dev} time="5:21 PM">
        <Mention>Halbot</Mention> still mad at me from earlier?
      </Message>

      <Message user={USERS.halbot} time="5:21 PM">
        <Subtext>*Intent: grudge.list · subject: devin*</Subtext>
        <Embed
          color={hb.warn}
          author={{ name: 'Halbot · grudge ledger', icon: '♨' }}
          title="What I'm holding onto about devin"
          fields={[
            { name: '#1 · rm -rf vibes',       value: 'wiped staging last thursday · severity 🔴',  inline: false },
            { name: '#2 · owes aria coffee',   value: 'logged 30s ago · severity 🟡',                inline: false },
          ]}
          footer={{ text: 'kind: grudges · tombstoned grudges stay recoverable until purge' }}
        />
        <ActionRow>
          <Btn style="secondary" emoji="🕊️">Forgive #1</Btn>
          <Btn style="secondary" emoji="🕊️">Forgive #2</Btn>
          <Btn style="secondary" emoji="📖">Show all grudges</Btn>
        </ActionRow>
        <Reactions items={[{emoji:'♨',n:3},{emoji:'😬',n:1,me:true}]}/>
      </Message>
    </ChatFrame>
  );
}

Object.assign(window, {
  FlowMention, FlowUpload, FlowTextTrigger,
  FlowVoiceWake, FlowVoiceTrigger,
  FlowAdminStatus, FlowAdminUndelete, FlowAdminPanic,
  FlowPersonas, FlowGrudgesFacts,
});
