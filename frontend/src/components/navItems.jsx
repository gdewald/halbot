import { T } from '../tokens.js';

// Nav order is the visual order in the sidebar and tab bar. Config MUST stay
// last — it's the "settings at the bottom" convention and new sections
// should be inserted above it, never after.
export const NAV_ITEMS = [
  {id:'logs',   label:'Logs',   icon:(a)=><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="2" y="3" width="12" height="1.6" rx=".8" fill={a?T.blurple:T.sub}/><rect x="2" y="7.2" width="8.5" height="1.6" rx=".8" fill={a?T.blurple:T.sub}/><rect x="2" y="11.4" width="10.5" height="1.6" rx=".8" fill={a?T.blurple:T.sub}/></svg>},
  {id:'daemon', label:'Daemon', icon:(a)=><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="5.5" stroke={a?T.blurple:T.sub} strokeWidth="1.5"/><circle cx="8" cy="8" r="2" fill={a?T.blurple:T.sub}/><line x1="8" y1="2.5" x2="8" y2="4.5" stroke={a?T.blurple:T.sub} strokeWidth="1.5" strokeLinecap="round"/><line x1="8" y1="11.5" x2="8" y2="13.5" stroke={a?T.blurple:T.sub} strokeWidth="1.5" strokeLinecap="round"/><line x1="2.5" y1="8" x2="4.5" y2="8" stroke={a?T.blurple:T.sub} strokeWidth="1.5" strokeLinecap="round"/><line x1="11.5" y1="8" x2="13.5" y2="8" stroke={a?T.blurple:T.sub} strokeWidth="1.5" strokeLinecap="round"/></svg>},
  {id:'stats',  label:'Stats',  icon:(a)=><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="2" y="9" width="3" height="5" rx=".8" fill={a?T.blurple:T.sub}/><rect x="6.5" y="5.5" width="3" height="8.5" rx=".8" fill={a?T.blurple:T.sub}/><rect x="11" y="2" width="3" height="12" rx=".8" fill={a?T.blurple:T.sub}/></svg>},
  {id:'analytics', label:'Analytics', icon:(a)=><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 13 L5.5 9 L8.5 11 L13.5 4" stroke={a?T.blurple:T.sub} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none"/><circle cx="13.5" cy="4" r="1.2" fill={a?T.blurple:T.sub}/><circle cx="5.5" cy="9" r="1.2" fill={a?T.blurple:T.sub}/></svg>},
  {id:'emojis', label:'Emojis', icon:(a)=><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="5.8" stroke={a?T.blurple:T.sub} strokeWidth="1.4"/><circle cx="6" cy="7" r=".9" fill={a?T.blurple:T.sub}/><circle cx="10" cy="7" r=".9" fill={a?T.blurple:T.sub}/><path d="M5.3 10 Q8 12.3 10.7 10" stroke={a?T.blurple:T.sub} strokeWidth="1.3" strokeLinecap="round" fill="none"/></svg>},
  // Keep Config last. Always.
  {id:'config', label:'Config', icon:(a)=><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 2a6 6 0 100 12A6 6 0 008 2z" stroke={a?T.blurple:T.sub} strokeWidth="1.4"/><path d="M8 5.5v5M5.5 8h5" stroke={a?T.blurple:T.sub} strokeWidth="1.4" strokeLinecap="round"/></svg>},
];
