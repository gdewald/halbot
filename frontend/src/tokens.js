export const T = {
  bg:'#0c0c0f', surface:'#111115', panel:'#16161b', raised:'#1c1c22',
  border:'rgba(255,255,255,0.065)', border2:'rgba(255,255,255,0.11)',
  text:'#e2e2ef', sub:'rgba(226,226,239,0.55)', dim:'rgba(226,226,239,0.3)',
  blurple:'#5865F2', blurpleD:'#4752c4', blurpleL:'#7289da',
  green:'#23d18b', red:'#f04747', yellow:'#faa61a', cyan:'#4fc3f7',
};

// Hex-only — Logs.jsx and LevelPill.jsx append 2-digit hex alpha to these
// values (e.g. `${color}30`). T.dim was rgba() which produced invalid CSS
// when concatenated, hiding the active DEBUG capsule.
export const LC = { DEBUG: '#9aa0aa', INFO: T.cyan, WARN: T.yellow, ERROR: T.red, WARNING: T.yellow };
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
