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
