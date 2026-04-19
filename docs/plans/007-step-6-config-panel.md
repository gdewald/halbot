# Step 6 — Config Panel

**Goal:** replace the Config placeholder with a grouped form
driven by `bridge.getConfig()`. Widgets chosen by the `type` field
on each row. Save persists via `UpdateConfig` + `PersistConfig`,
Revert drops runtime overrides via `ResetConfig`.

**Runnable at end:** yes — changing `log_level` from the panel
visibly changes the log stream in the Logs panel and survives
daemon restart.

## Files you will touch

- `frontend/src/panels/Config.jsx` (rewrite placeholder)
- `frontend/src/panels/config/ConfigRow.jsx` (new)
- `frontend/src/panels/config/FieldInput.jsx` (new)

Do not touch `dashboard/`, `halbot/`, or other panels. Do not
invent new field types; the proto enum from step 1 is the only
source of truth.

## 6.1 `frontend/src/panels/config/FieldInput.jsx`

Render one of: STRING, URL, NUMBER, RANGE, BOOL, SELECT. Widgets
copy their styling from the mockup (lines 628–674).

```jsx
import { T } from '../../tokens.js';

const inputBase = {
  background: T.panel, border: `1px solid ${T.border2}`, borderRadius: 6,
  color: T.text, fontSize: 12, outline: 'none',
  fontFamily: 'JetBrains Mono', transition: 'border-color 0.15s',
};

export function FieldInput({ field, onChange }) {
  const { type, draft } = field;

  const focus = e => { e.target.style.borderColor = T.blurple; };
  const blur  = e => { e.target.style.borderColor = 'rgba(255,255,255,0.11)'; };

  if (type === 'BOOL') {
    const val = String(draft).toLowerCase() === 'true';
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div onClick={() => onChange(val ? 'false' : 'true')} style={{
          width: 34, height: 18, borderRadius: 9, cursor: 'pointer',
          background: val ? T.blurple : 'rgba(255,255,255,0.12)',
          transition: 'background 0.2s', position: 'relative', flexShrink: 0,
        }}>
          <div style={{
            position: 'absolute', top: 2, left: val ? 16 : 2, width: 14, height: 14,
            borderRadius: '50%', background: '#fff', transition: 'left 0.2s',
            boxShadow: '0 1px 3px rgba(0,0,0,0.4)',
          }} />
        </div>
        <span style={{ fontSize: 12, color: val ? T.green : T.dim }}>
          {val ? 'enabled' : 'disabled'}
        </span>
      </div>
    );
  }

  if (type === 'SELECT') {
    return (
      <select value={draft} onChange={e => onChange(e.target.value)}
        onFocus={focus} onBlur={blur}
        style={{ ...inputBase, padding: '5px 8px', height: 30, cursor: 'pointer' }}>
        {(field.options || []).map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  }

  if (type === 'RANGE') {
    const min = field.min, max = field.max, step = field.step || 0.01;
    const n = Number(draft);
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <input type="range" min={min} max={max} step={step} value={Number.isFinite(n) ? n : min}
          onChange={e => onChange(e.target.value)}
          style={{ flex: 1, accentColor: T.blurple, height: 4 }} />
        <span style={{
          fontFamily: 'JetBrains Mono', fontSize: 12, color: T.text, minWidth: 56, textAlign: 'right',
        }}>{Number.isFinite(n) ? n.toFixed(step < 0.1 ? 3 : 2) : '—'}</span>
      </div>
    );
  }

  if (type === 'NUMBER') {
    return (
      <input type="number" value={draft} onChange={e => onChange(e.target.value)}
        min={field.min || undefined} max={field.max || undefined} step={field.step || 1}
        onFocus={focus} onBlur={blur}
        style={{ ...inputBase, padding: '5px 9px', height: 30, width: 140 }} />
    );
  }

  // STRING, URL, or unknown → text input
  return (
    <input value={draft} onChange={e => onChange(e.target.value)}
      onFocus={focus} onBlur={blur}
      style={{ ...inputBase, padding: '5px 9px', height: 30, width: '100%', maxWidth: 360 }} />
  );
}
```

## 6.2 `frontend/src/panels/config/ConfigRow.jsx`

Mockup lines 593–626, verbatim modulo imports + uses the local
`FieldInput` component.

```jsx
import { useState } from 'react';
import { T } from '../../tokens.js';
import { FieldInput } from './FieldInput.jsx';

export function ConfigRow({ field, isLast, onChange, onRevert }) {
  const [hov, setHov] = useState(false);
  return (
    <div onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
      style={{
        display: 'grid', gridTemplateColumns: '260px 1fr auto',
        alignItems: 'center', gap: 0,
        borderBottom: isLast ? 'none' : `1px solid ${T.border}`,
        background: field.dirty ? `${T.blurple}06`
          : hov ? 'rgba(255,255,255,0.018)' : 'transparent',
        transition: 'background 0.1s',
      }}>
      <div style={{ padding: '10px 14px', borderRight: `1px solid ${T.border}` }}>
        <div style={{
          fontFamily: 'JetBrains Mono', fontSize: 11, color: T.cyan,
          display: 'flex', alignItems: 'center', gap: 5,
        }}>
          {field.dirty && <span style={{ color: T.blurple, fontSize: 8 }}>●</span>}
          {field.label}
        </div>
        <div style={{ fontSize: 10, color: T.dim, marginTop: 2 }}>{field.description}</div>
      </div>
      <div style={{ padding: '8px 14px' }}>
        <FieldInput field={field} onChange={onChange} />
      </div>
      <div style={{ padding: '0 10px', display: 'flex', alignItems: 'center' }}>
        {field.dirty && (
          <button onClick={onRevert} title="revert" style={{
            width: 24, height: 24, borderRadius: 5,
            border: `1px solid ${T.border2}`,
            background: 'transparent', color: T.yellow, fontSize: 13, cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>↺</button>
        )}
      </div>
    </div>
  );
}
```

## 6.3 `frontend/src/panels/Config.jsx`

```jsx
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
      // Local revert first (UX instant). No RPC needed — drafts never hit daemon.
      setFields(fs => fs.map(f => ({ ...f, draft: f.value, dirty: false })));
      setReverted(true);
      setTimeout(() => setReverted(false), 1500);
    } catch (e) {
      setError(String(e));
    }
  };

  // Drop registry/runtime override for all fields (different from revert-local-draft).
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
```

### Save semantics

- **Save to disk** = `UpdateConfig` + `PersistConfig` for the
  dirty keys. After the round-trip, the refreshed state should
  show `source: REGISTRY` for those fields.
- **Revert all** = discard unsaved drafts client-side. No RPC.
  Does not touch runtime overrides or registry values.
- **Reset overrides** = `ResetConfig([])`. Drops all runtime
  overrides on the daemon so registry / default wins. Useful
  to undo an `UpdateConfig` done with an un-persisted override.

### What this panel does *not* do

- No "planned" fields. Every field shown comes from a real
  `DEFAULTS` key in `halbot/config.py`. Fields for subsystems not
  yet in the repo (LLM / voice / TTS) **are editable** because
  the config layer already exists — they just don't drive any
  behavior until the subsystem lands. That is acceptable: the UI
  is honest about the stored value.
- No secrets editing. `SetSecret` is out of scope for this step
  and not plumbed through the bridge. Do not add a "secrets"
  group.

## 6.4 Rebuild + verify

```powershell
cd frontend
npm run build
cd ..
```

## 6.5 Verification gate

**Terminal 1:** daemon running.

**Terminal 2:**

```powershell
uv run python -m dashboard.app
```

Navigate to Config. Expected:

- Four groups visible (`General`, `LLM`, `Voice`, `TTS (Kokoro)`)
  with the fields from `halbot.config.SCHEMA`.
- `LOG_LEVEL` row renders as a `<select>` with DEBUG/INFO/WARNING/ERROR.
- `VOICE_LLM_COMBINE_CALLS` renders as a toggle pill.
- `TTS_SPEED` renders as a slider showing the current value.
- `LLM_MAX_TOKENS_TEXT` renders as a number input.
- Changing a field:
  - shows the dirty dot on the row
  - highlights the row with the blurple tint
  - increments the "N unsaved changes" counter
  - shows the `MODIFIED` chip on the group header
  - shows the revert ↺ button
- Revert all → drafts drop, dirty counter clears.
- Save to disk → status briefly shows "✓ Saved", values persist
  across a daemon restart.
- Change `log_level` to DEBUG and save → the Logs panel starts
  showing DEBUG lines within one tick.

## Commit

```powershell
git add frontend/src/panels/Config.jsx frontend/src/panels/config/ConfigRow.jsx frontend/src/panels/config/FieldInput.jsx docs/plans/007-step-6-config-panel.md
git commit -m "feat(007): config panel — schema-driven widgets + save/revert"
```
