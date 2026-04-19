# Step 3 — Frontend Scaffold

**Goal:** stand up a Vite + React project at `frontend/`, extract
the design tokens + shell components from the mockup
(`docs/mockups/dashboard/halbot.html`), produce a buildable
`frontend/dist/` that loads in the pywebview window from step 2.
Panels are empty placeholders in this step — they are filled by
steps 4–7.

**Runnable at end:** yes — `npm run build` in `frontend/` produces
`dist/`, and `uv run python -m dashboard.app` opens the window
with title bar + sidebar + empty panel area. Step-2 stub HTML is
no longer loaded.

## Prereqs

Node.js 20+ installed on the dev box. Verify:

```powershell
node --version   # must print v20.x or newer
npm --version
```

If missing: `winget install OpenJS.NodeJS.LTS` then reopen shell.

## Files you will create

```
frontend/
  .gitignore
  .nvmrc
  package.json
  vite.config.js
  index.html
  src/
    main.jsx
    App.jsx
    bridge.js
    tokens.js
    components/
      WinTitleBar.jsx
      SidebarNarrow.jsx
      SidebarWide.jsx
      TopTabBar.jsx
      StatusBar.jsx
      LevelPill.jsx
      ToggleBtn.jsx
    panels/
      Logs.jsx
      Daemon.jsx
      Config.jsx
      Stats.jsx
    fonts/
      (woff2 files dropped in 3.9)
    styles.css
```

Do not touch `dashboard/` or `halbot/` in this step.

## 3.1 `frontend/.gitignore`

```
node_modules/
dist/
*.log
.vite/
```

## 3.2 `frontend/.nvmrc`

```
20
```

## 3.3 `frontend/package.json`

```json
{
  "name": "halbot-dashboard",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.1",
    "vite": "^5.4.0"
  }
}
```

## 3.4 `frontend/vite.config.js`

```js
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    assetsInlineLimit: 0,
  },
});
```

`base: './'` is mandatory — without it, assets fail under the
`file://` URL pywebview uses.

## 3.5 `frontend/index.html`

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>halbot</title>
    <link rel="stylesheet" href="/src/styles.css" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

## 3.6 `frontend/src/styles.css`

Copy the `<style>` block from the mockup
(`docs/mockups/dashboard/halbot.html` lines ~11–24). Then add
`@font-face` declarations (3.9 drops the woff2 files). Verbatim:

```css
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;background:#0c0c0f;overflow:hidden}
body{font-family:'DM Sans',sans-serif;color:#e2e2ef}
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,0.09);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,0.17)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.35}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes fadeIn{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}
input[type=range]{accent-color:#5865F2}
input[type=checkbox]{accent-color:#5865F2}

@font-face {
  font-family: 'DM Sans';
  src: url('./fonts/DMSans-Regular.woff2') format('woff2');
  font-weight: 400; font-style: normal;
}
@font-face {
  font-family: 'DM Sans';
  src: url('./fonts/DMSans-Medium.woff2') format('woff2');
  font-weight: 500; font-style: normal;
}
@font-face {
  font-family: 'DM Sans';
  src: url('./fonts/DMSans-SemiBold.woff2') format('woff2');
  font-weight: 600; font-style: normal;
}
@font-face {
  font-family: 'JetBrains Mono';
  src: url('./fonts/JetBrainsMono-Regular.woff2') format('woff2');
  font-weight: 400; font-style: normal;
}
@font-face {
  font-family: 'JetBrains Mono';
  src: url('./fonts/JetBrainsMono-Medium.woff2') format('woff2');
  font-weight: 500; font-style: normal;
}
@font-face {
  font-family: 'JetBrains Mono';
  src: url('./fonts/JetBrainsMono-SemiBold.woff2') format('woff2');
  font-weight: 600; font-style: normal;
}
```

## 3.7 `frontend/src/tokens.js`

Extract the `T` object from the mockup (line ~32–38) verbatim:

```js
export const T = {
  bg:'#0c0c0f', surface:'#111115', panel:'#16161b', raised:'#1c1c22',
  border:'rgba(255,255,255,0.065)', border2:'rgba(255,255,255,0.11)',
  text:'#e2e2ef', sub:'rgba(226,226,239,0.55)', dim:'rgba(226,226,239,0.3)',
  blurple:'#5865F2', blurpleD:'#4752c4', blurpleL:'#7289da',
  green:'#23d18b', red:'#f04747', yellow:'#faa61a', cyan:'#4fc3f7',
};

export const LC = { DEBUG: T.dim, INFO: T.cyan, WARN: T.yellow, ERROR: T.red, WARNING: T.yellow };
export const LB = {
  DEBUG:'rgba(255,255,255,0.04)', INFO:'rgba(79,195,247,0.1)',
  WARN:'rgba(250,166,26,0.1)', WARNING:'rgba(250,166,26,0.1)',
  ERROR:'rgba(240,71,71,0.1)',
};
export const LBD = {
  DEBUG:'rgba(255,255,255,0.08)', INFO:'rgba(79,195,247,0.18)',
  WARN:'rgba(250,166,26,0.18)', WARNING:'rgba(250,166,26,0.18)',
  ERROR:'rgba(240,71,71,0.18)',
};
```

Note: the daemon emits `WARNING` (Python default) but the mockup
uses `WARN`. Both keys are included in `LC`/`LB`/`LBD` so either
works.

## 3.8 `frontend/src/bridge.js`

Thin async wrapper so components never touch `window.pywebview`
directly. All methods return a Promise.

```js
// Thin wrapper over window.pywebview.api.*.
// Works in-browser dev (returns stub data) and inside pywebview.

const api = () => window.pywebview?.api;

const STUB = {
  health: async () => ({ uptime_seconds: 0, daemon_version: 'dev', llm_reachable: false, whisper_loaded: false, tts_loaded: false }),
  get_config: async () => ({}),
  update_config: async () => ({}),
  persist_config: async () => ({}),
  reset_config: async () => ({}),
  service_query: async () => ({ state: 'stopped', pid: 0 }),
  service_start: async () => null,
  service_stop: async () => null,
  service_restart: async () => null,
  proc_stats: async () => ({ memory_mb: 0, cpu_pct: 0 }),
  nssm_auto_restart_get: async () => null,
  nssm_auto_restart_set: async () => false,
  backlog_logs: async () => [],
  pop_log_batch: async () => [],
  get_stats: async () => ({ mock: true }),
  window_minimize: async () => null,
  window_maximize: async () => null,
  window_close: async () => null,
};

function make(name) {
  return async (...args) => {
    const a = api();
    if (!a) return STUB[name](...args);
    return a[name](...args);
  };
}

export const b = {
  health: make('health'),
  getConfig: make('get_config'),
  updateConfig: make('update_config'),
  persistConfig: make('persist_config'),
  resetConfig: make('reset_config'),
  serviceQuery: make('service_query'),
  serviceStart: make('service_start'),
  serviceStop: make('service_stop'),
  serviceRestart: make('service_restart'),
  procStats: make('proc_stats'),
  nssmGet: make('nssm_auto_restart_get'),
  nssmSet: make('nssm_auto_restart_set'),
  backlogLogs: make('backlog_logs'),
  popLogBatch: make('pop_log_batch'),
  getStats: make('get_stats'),
  minimize: make('window_minimize'),
  maximize: make('window_maximize'),
  close: make('window_close'),
};
```

## 3.9 Fonts

Download the 6 woff2 files into `frontend/src/fonts/`:

- DM Sans: `DMSans-Regular.woff2`, `DMSans-Medium.woff2`, `DMSans-SemiBold.woff2`
- JetBrains Mono: `JetBrainsMono-Regular.woff2`, `JetBrainsMono-Medium.woff2`, `JetBrainsMono-SemiBold.woff2`

Source: the respective GitHub repos or Google Fonts zip (self-host,
do not link to the Google CDN). Weights 400 / 500 / 600 only.

Total weight <200KB. Do not check in full OTF/TTF variants.

## 3.10 `frontend/src/components/LevelPill.jsx`

Copy the mockup's `LevelPill` (lines 68–72). Verbatim except for
imports:

```jsx
import { T, LC, LB } from '../tokens.js';

export function LevelPill({ level }) {
  return (
    <span style={{
      fontFamily: 'JetBrains Mono', fontSize: 9, fontWeight: 600,
      color: LC[level] || T.dim, background: LB[level] || 'transparent',
      border: `1px solid ${LC[level] || T.dim}28`, borderRadius: 3,
      padding: '1px 5px', letterSpacing: '0.06em', whiteSpace: 'nowrap',
    }}>{level}</span>
  );
}
```

## 3.11 `frontend/src/components/ToggleBtn.jsx`

Mockup lines 312–324, verbatim:

```jsx
import { T } from '../tokens.js';

export function ToggleBtn({ active, onClick, label, accent }) {
  const ac = accent || T.blurple;
  return (
    <button onClick={onClick} style={{
      height: 26, padding: '0 9px', borderRadius: 5,
      border: `1px solid ${active ? ac : T.border}`,
      background: active ? `${ac}18` : 'transparent',
      color: active ? ac : T.dim, fontSize: 10, cursor: 'pointer',
      fontFamily: 'DM Sans', fontWeight: 500, transition: 'all 0.1s',
      display: 'flex', alignItems: 'center', gap: 5,
    }}>
      <span style={{ fontSize: 8 }}>●</span>{label}
    </button>
  );
}
```

## 3.12 `frontend/src/components/WinTitleBar.jsx`

Mockup lines 75–99, but replace the inline button `onClick` with
real `bridge` calls. Paste:

```jsx
import { T } from '../tokens.js';
import { b } from '../bridge.js';

export function WinTitleBar({ title, subtitle }) {
  const buttons = [
    { label: '─', hov: 'rgba(255,255,255,0.08)', act: () => b.minimize() },
    { label: '□', hov: 'rgba(255,255,255,0.08)', act: () => b.maximize() },
    { label: '✕', hov: '#c42b1c',               act: () => b.close() },
  ];
  return (
    <div style={{
      height: 32, flexShrink: 0, background: T.surface,
      borderBottom: `1px solid ${T.border}`, display: 'flex',
      alignItems: 'center', paddingLeft: 12, paddingRight: 0,
      userSelect: 'none', WebkitAppRegion: 'drag',
    }}>
      <div style={{
        width: 16, height: 16, borderRadius: 4,
        background: `linear-gradient(135deg,${T.blurple},${T.blurpleL})`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 9, fontWeight: 700, color: '#fff', marginRight: 8, flexShrink: 0,
      }}>H</div>
      <span style={{ fontSize: 12, fontWeight: 600, color: T.text, marginRight: 6 }}>{title}</span>
      {subtitle && <span style={{ fontSize: 10, color: T.dim, fontFamily: 'JetBrains Mono' }}>{subtitle}</span>}
      <div style={{ flex: 1 }} />
      {buttons.map((btn, i) => (
        <button key={i} onClick={btn.act} style={{
          width: 46, height: 32, border: 'none', background: 'transparent',
          color: T.sub, fontSize: i === 2 ? 12 : 11, cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          transition: 'background 0.1s', WebkitAppRegion: 'no-drag',
        }}
          onMouseEnter={e => e.currentTarget.style.background = btn.hov}
          onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
        >{btn.label}</button>
      ))}
    </div>
  );
}
```

## 3.13 `frontend/src/components/SidebarNarrow.jsx`, `SidebarWide.jsx`, `TopTabBar.jsx`

Copy verbatim from mockup lines 104–190 with these changes:

- Replace the inline `NAV_ITEMS` constant with an import:

  ```jsx
  import { NAV_ITEMS } from './navItems.jsx';
  ```

- Move `NAV_ITEMS` into a new file
  `frontend/src/components/navItems.jsx`:

  ```jsx
  import { T } from '../tokens.js';
  export const NAV_ITEMS = [
    // copy from mockup line 104-109 verbatim
  ];
  ```

- Each sidebar/tabbar takes `{ active, onChange }` props, exports
  as a named export.

## 3.14 `frontend/src/components/StatusBar.jsx`

Mockup lines 886–904 verbatim, named export, uses `T` from tokens.

## 3.15 Panel placeholders

Each of `frontend/src/panels/Logs.jsx`, `Daemon.jsx`, `Config.jsx`,
`Stats.jsx` is a one-component placeholder for now:

```jsx
// e.g. Logs.jsx
import { T } from '../tokens.js';

export function LogsPanel() {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '100%', color: T.dim, fontSize: 12,
    }}>
      Logs panel — lands in step 4.
    </div>
  );
}
```

Mirror for `DaemonPanel`, `ConfigPanel`, `StatsPanel` with their
step numbers (5, 6, 7).

## 3.16 `frontend/src/App.jsx`

```jsx
import { useState } from 'react';
import { T } from './tokens.js';
import { WinTitleBar } from './components/WinTitleBar.jsx';
import { SidebarNarrow } from './components/SidebarNarrow.jsx';
import { StatusBar } from './components/StatusBar.jsx';
import { LogsPanel } from './panels/Logs.jsx';
import { DaemonPanel } from './panels/Daemon.jsx';
import { ConfigPanel } from './panels/Config.jsx';
import { StatsPanel } from './panels/Stats.jsx';

const SUBTITLE = {
  logs: '· Live log stream',
  daemon: '· Service control',
  config: '· Runtime configuration',
  stats: '· Activity & stats',
};

export function App() {
  const [panel, setPanel] = useState(() => localStorage.getItem('halbot_panel') || 'logs');
  const onChange = p => { setPanel(p); localStorage.setItem('halbot_panel', p); };

  return (
    <div style={{
      width: '100vw', height: '100vh', display: 'flex', flexDirection: 'column',
      background: T.bg, overflow: 'hidden',
    }}>
      <WinTitleBar title="halbot" subtitle={SUBTITLE[panel]} />
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
        <SidebarNarrow active={panel} onChange={onChange} />
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <div style={{ flex: 1, overflow: 'hidden' }}>
            {panel === 'logs'   && <LogsPanel />}
            {panel === 'daemon' && <DaemonPanel />}
            {panel === 'config' && <ConfigPanel />}
            {panel === 'stats'  && <StatsPanel />}
          </div>
        </div>
      </div>
      <StatusBar panel={panel} />
    </div>
  );
}
```

Layout is fixed to `SidebarNarrow` for now. The Wide / TopTabBar
variants ship but are not switched to — adding a runtime toggle is
out of scope for this plan.

## 3.17 `frontend/src/main.jsx`

```jsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App.jsx';

createRoot(document.getElementById('root')).render(
  <StrictMode><App /></StrictMode>
);
```

## 3.18 Build + wire into dashboard

```powershell
cd frontend
npm install
npm run build
cd ..
```

Verify: `frontend/dist/index.html` exists, plus a `frontend/dist/assets/`
subdir with JS + CSS bundles.

## 3.19 Verification gate

**Terminal 1:** daemon running (step 1 verified).

```powershell
uv run python -m halbot.daemon run
```

**Terminal 2:**

```powershell
uv run python -m dashboard.app
```

Expected:

- Dashboard window opens with the custom 32px title bar (dark,
  with the "H" mark and "halbot · Live log stream").
- Narrow sidebar on the left with 4 icons. Clicking each icon:
  - switches highlight
  - updates title-bar subtitle
  - swaps the center area between four "X panel — lands in step N"
    placeholder messages
- Blurple 22px status bar at the bottom with "connected · halbot
  v…" and the current time ticking.
- Minimize / close title-bar buttons work.
- No console errors in WebView2 devtools (press F12 if debug
  enabled in `dashboard/app.py`; leave `debug=False` for commit).

Browser-only sanity check (optional but encouraged):

```powershell
cd frontend
npm run dev
# Open the printed http://localhost:5173 in a regular browser.
# Nav + title bar render; API calls return the STUB values from bridge.js.
```

If the window shows a white page or "file not found", re-check
`vite.config.js`'s `base: './'` and that
`dashboard/paths.py::web_dir()` resolves to `frontend/dist/`.

## Commit

```powershell
git add frontend/ docs/plans/007-step-3-frontend-scaffold.md
git commit -m "feat(007): frontend scaffold — Vite + React shell"
```

`node_modules/` and `dist/` are git-ignored (3.1). Fonts under
`frontend/src/fonts/` ARE committed (need to ship with the
bundle).

Do not modify `dashboard/` or `halbot/` in this commit.
