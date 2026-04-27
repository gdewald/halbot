import { useState } from 'react';
import { T } from './tokens.js';
import { IS_SNAPSHOT } from './bridge.js';
import { SidebarWide } from './components/SidebarWide.jsx';
import { StatusBar } from './components/StatusBar.jsx';
import { NAV_ITEMS } from './components/navItems.jsx';
import { LogsPanel } from './panels/Logs.jsx';
import { DaemonPanel } from './panels/Daemon.jsx';
import { ConfigPanel } from './panels/Config.jsx';
import { StatsPanel } from './panels/Stats.jsx';
import { AnalyticsPanel } from './panels/Analytics.jsx';
import { EmojisPanel } from './panels/Emojis.jsx';
import { SnapshotBanner } from './SnapshotBanner.jsx';

// In snapshot mode (public URL) the live/control panels are dead — only the
// data panels carry signal.
const SNAPSHOT_PANELS = new Set(['stats', 'analytics']);
const NAV_FOR_SNAPSHOT = NAV_ITEMS.filter(n => SNAPSHOT_PANELS.has(n.id));

export function App() {
  const initial = IS_SNAPSHOT
    ? 'stats'
    : (localStorage.getItem('halbot_panel') || 'logs');
  const [panel, setPanel] = useState(initial);
  const onChange = p => {
    setPanel(p);
    if (!IS_SNAPSHOT) localStorage.setItem('halbot_panel', p);
  };

  return (
    <div style={{
      width: '100vw', height: '100vh', display: 'flex', flexDirection: 'column',
      background: T.bg, overflow: 'hidden',
    }}>
      {IS_SNAPSHOT && <SnapshotBanner />}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
        <SidebarWide active={panel} onChange={onChange}
                     items={IS_SNAPSHOT ? NAV_FOR_SNAPSHOT : NAV_ITEMS} />
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <div style={{ flex: 1, overflow: 'hidden' }}>
            {panel === 'logs'   && <LogsPanel />}
            {panel === 'daemon' && <DaemonPanel />}
            {panel === 'config' && <ConfigPanel />}
            {panel === 'stats'  && <StatsPanel />}
            {panel === 'analytics' && <AnalyticsPanel />}
            {panel === 'emojis' && <EmojisPanel />}
          </div>
        </div>
      </div>
      {!IS_SNAPSHOT && <StatusBar panel={panel} />}
    </div>
  );
}
