import { useCallback, useEffect, useMemo, useState } from 'react';
import { T } from '../tokens.js';
import { b } from '../bridge.js';
import { ConfigRow } from './config/ConfigRow.jsx';

const GROUP_ORDER = ['general', 'llm', 'voice', 'tts'];
const GROUP_LABEL = {
  general: 'General',
  llm: 'LLM',
  voice: 'Voice',
  tts: 'TTS (Kokoro)',
};

function buildFields(raw) {
  return Object.entries(raw).map(([key, v]) => ({
    key,
    label: v.label || key.toUpperCase(),
    description: v.description || '',
    type: v.type || 'STRING',
    options: v.options || [],
    group: v.group || 'general',
    min: v.min, max: v.max, step: v.step,
    value: v.value,
    source: v.source,
    draft: v.value,
    dirty: false,
  }));
}

export function ConfigPanel() {
  const [fields, setFields] = useState([]);
  const [saved, setSaved] = useState(false);
  const [reverted, setReverted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const raw = await b.getConfig();
      setFields(buildFields(raw));
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const setDraft = (key, draft) => {
    setFields(fs => fs.map(f => f.key !== key ? f : { ...f, draft, dirty: draft !== f.value }));
  };
  const revertOne = (key) => {
    setFields(fs => fs.map(f => f.key !== key ? f : { ...f, draft: f.value, dirty: false }));
  };

  const dirtyFields = fields.filter(f => f.dirty);
  const dirtyCount = dirtyFields.length;
  const anyDirty = dirtyCount > 0;

  const saveAll = async () => {
    if (!anyDirty || saving) return;
    setSaving(true);
    try {
      setError(null);
      const updates = {};
      for (const f of dirtyFields) updates[f.key] = String(f.draft);
      await b.updateConfig(updates);
      await b.persistConfig(Object.keys(updates));
      await refresh();
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const revertAll = async () => {
    if (!anyDirty) return;
    try {
      setError(null);
      setFields(fs => fs.map(f => ({ ...f, draft: f.value, dirty: false })));
      setReverted(true);
      setTimeout(() => setReverted(false), 1500);
    } catch (e) {
      setError(String(e));
    }
  };

  const resetOverrides = async () => {
    try {
      setError(null);
      await b.resetConfig([]);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const byGroup = useMemo(() => {
    const m = {};
    for (const f of fields) (m[f.group] = m[f.group] || []).push(f);
    return m;
  }, [fields]);

  const orderedGroups = GROUP_ORDER.filter(g => (byGroup[g] || []).length > 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', animation: 'fadeIn 0.15s ease' }}>
      {/* Toolbar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px',
        borderBottom: `1px solid ${T.border}`, flexShrink: 0,
      }}>
        <span style={{ fontSize: 11, color: T.dim }}>
          {dirtyCount === 0 ? 'No unsaved changes' : `${dirtyCount} unsaved change${dirtyCount !== 1 ? 's' : ''}`}
        </span>
        {error && <span style={{ fontSize: 11, color: T.red, marginLeft: 8 }}>{error}</span>}
        <div style={{ flex: 1 }} />
        <button onClick={resetOverrides}
          title="Drop runtime overrides and reload from registry"
          style={{
            height: 28, padding: '0 11px', borderRadius: 6,
            border: `1px solid ${T.border2}`, background: 'transparent',
            color: T.sub, fontSize: 11, cursor: 'pointer', fontFamily: 'DM Sans',
          }}>Reset overrides</button>
        <button onClick={revertAll} disabled={!anyDirty} style={{
          height: 28, padding: '0 11px', borderRadius: 6,
          border: `1px solid ${T.border2}`, background: 'transparent',
          color: anyDirty ? T.sub : T.dim, fontSize: 11,
          cursor: anyDirty ? 'pointer' : 'default', fontFamily: 'DM Sans',
        }}>{reverted ? '✓ Reverted' : 'Revert all'}</button>
        <button onClick={saveAll} disabled={!anyDirty || saving} style={{
          height: 28, padding: '0 13px', borderRadius: 6, border: 'none',
          background: anyDirty ? T.blurple : 'rgba(255,255,255,0.08)',
          color: anyDirty ? '#fff' : T.dim,
          fontSize: 11, fontWeight: 600,
          cursor: anyDirty && !saving ? 'pointer' : 'default',
          fontFamily: 'DM Sans', transition: 'all 0.15s',
        }}>{saving ? 'Saving…' : saved ? '✓ Saved' : 'Save to disk'}</button>
      </div>

      {/* Groups */}
      <div style={{ flex: 1, overflow: 'auto', padding: '14px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {orderedGroups.map(g => {
            const groupFields = byGroup[g];
            const groupDirty = groupFields.some(f => f.dirty);
            return (
              <div key={g} style={{
                background: T.surface, border: `1px solid ${T.border}`,
                borderRadius: 9, overflow: 'hidden',
              }}>
                <div style={{
                  padding: '10px 14px', borderBottom: `1px solid ${T.border}`,
                  display: 'flex', alignItems: 'center', gap: 8,
                }}>
                  <span style={{
                    fontSize: 11, fontWeight: 600, color: T.text,
                    textTransform: 'uppercase', letterSpacing: '0.06em',
                  }}>{GROUP_LABEL[g] || g}</span>
                  {groupDirty && (
                    <span style={{
                      fontSize: 9, color: T.blurple,
                      background: `${T.blurple}18`,
                      border: `1px solid ${T.blurple}30`,
                      borderRadius: 3, padding: '1px 5px',
                      fontFamily: 'JetBrains Mono', fontWeight: 600,
                    }}>MODIFIED</span>
                  )}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column' }}>
                  {groupFields.map((f, i) => (
                    <ConfigRow key={f.key} field={f} isLast={i === groupFields.length - 1}
                      onChange={v => setDraft(f.key, v)}
                      onRevert={() => revertOne(f.key)} />
                  ))}
                </div>
              </div>
            );
          })}

          {orderedGroups.length === 0 && (
            <div style={{ padding: 24, color: T.dim, fontSize: 12 }}>
              No config fields reported by the daemon. Verify the daemon is
              running and its SCHEMA dict is populated.
            </div>
          )}
        </div>
        <div style={{ height: 16 }} />
      </div>
    </div>
  );
}
