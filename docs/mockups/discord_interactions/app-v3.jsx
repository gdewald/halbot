// app-v3.jsx — canvas for v3, grounded in interactions.md
const FRAME_W = 720;
const FRAME_H = 760;

function HalbotFlowsApp() {
  return (
    <DesignCanvas>
      <DCSection id="mention" title="Mention routing" subtitle="Anything in @ goes through the LLM with last 50 messages as context. Attached audio becomes a sound.">
        <DCArtboard id="mention" label="01 · @ mention · LLM + context"  width={FRAME_W} height={FRAME_H}><FlowMention/></DCArtboard>
        <DCArtboard id="upload"  label="02 · Upload · attach → save"     width={FRAME_W} height={FRAME_H}><FlowUpload/></DCArtboard>
      </DCSection>

      <DCSection id="triggers" title="Triggers" subtitle="Configured phrases fire without a mention. Text channels + voice STT transcripts.">
        <DCArtboard id="trigger-text"  label="03 · Text trigger fires"   width={FRAME_W} height={FRAME_H}><FlowTextTrigger/></DCArtboard>
        <DCArtboard id="trigger-voice" label="04 · Voice trigger (no wake word)" width={FRAME_W} height={FRAME_H}><FlowVoiceTrigger/></DCArtboard>
      </DCSection>

      <DCSection id="voice" title="Voice" subtitle="Wake-word → VAD → faster-whisper → LLM → TTS.">
        <DCArtboard id="wake" label="05 · Wake-word capture + spoken reply" width={FRAME_W} height={FRAME_H}><FlowVoiceWake/></DCArtboard>
      </DCSection>

      <DCSection id="memory" title="Memory" subtitle="Personas, facts, and grudges — persisted, soft-deletable, tombstoned.">
        <DCArtboard id="personas"     label="06 · Personas · saved per user"   width={FRAME_W} height={FRAME_H}><FlowPersonas/></DCArtboard>
        <DCArtboard id="grudges"      label="07 · Facts + grudges ledger"      width={FRAME_W} height={FRAME_H}><FlowGrudgesFacts/></DCArtboard>
      </DCSection>

      <DCSection id="admin" title="Admin (owner only)" subtitle="!halbot admin … — prefix-gated recovery shell. Soft deletes, tombstones, panic.">
        <DCArtboard id="status"    label="08 · admin status"             width={FRAME_W} height={FRAME_H}><FlowAdminStatus/></DCArtboard>
        <DCArtboard id="undelete"  label="09 · admin deleted + undelete" width={FRAME_W} height={FRAME_H}><FlowAdminUndelete/></DCArtboard>
        <DCArtboard id="panic"     label="10 · admin panic [all]"        width={FRAME_W} height={FRAME_H}><FlowAdminPanic/></DCArtboard>
      </DCSection>

      <DCPostIt x={40} y={40} w={260}>
        <b>Scope</b><br/>
        This pass covers the user-facing Discord surfaces from <i>interactions.md</i>:<br/>
        · <b>Text channel</b> — mentions, uploads, text triggers, <code>!halbot admin</code><br/>
        · <b>Voice channel</b> — wake word, voice triggers
      </DCPostIt>
      <DCPostIt x={320} y={40} w={260}>
        <b>Not covered here</b><br/>
        Tray menu, dashboard, and the gRPC API are local surfaces — different medium. Happy to mock the dashboard panels next as a separate file.
      </DCPostIt>
      <DCPostIt x={600} y={40} w={240}>
        <b>Memory model in the UI</b><br/>
        Rows are <i>live</i> or <i>tombstoned</i>. Delete = tombstone. Admin can list, undelete, panic, or purge. Copy distinguishes <b>soft</b> (recoverable) from <b>hard</b> (purge).
      </DCPostIt>
    </DesignCanvas>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<HalbotFlowsApp/>);
