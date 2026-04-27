// Thin wrapper over window.pywebview.api.*.
// Three modes:
//   1. Static snapshot — index.html injected `window.__STATS_SNAPSHOT__`
//      by halbot.stats_publisher. Returns pre-computed data, mutations no-op.
//   2. pywebview — bot operator's tray dashboard.
//   3. Browser dev — STUB returns empty data.

const api = () => window.pywebview?.api;
const SNAPSHOT = (typeof window !== 'undefined') ? window.__STATS_SNAPSHOT__ : null;

export const IS_SNAPSHOT = !!SNAPSHOT;

const STUB = {
  health: async () => ({ uptime_seconds: 0, daemon_version: 'dev', llm_reachable: false, whisper_loaded: false, tts_loaded: false, pid: 0, rss_bytes: 0, cpu_percent: 0, guild_count: 0 }),
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
  soundboard_list: async () => [],
  emoji_list: async () => [],
  query_stats: async () => ({ total_count: 0, rows: [] }),
  backlog_events: async () => [],
  pop_event_batch: async () => [],
  window_minimize: async () => null,
  window_maximize: async () => null,
  window_close: async () => null,
};

function makeSnapshotBridge(S) {
  const A = S.analytics || {};
  // Returns the pre-baked aggregate matching (kind, group_by). Args ts_from
  // etc. are ignored — the snapshot was frozen with a single 30d window.
  const queryStats = async (kind = '', _user = 0, _target = '',
                            _from = 0, _to = 0, group_by = '', _limit = 100) => {
    if (kind === 'soundboard_play' && group_by === 'target') return A.top_sounds || { total_count: 0, rows: [] };
    if (!kind && group_by === 'user_id') return A.top_users || { total_count: 0, rows: [] };
    if (kind === 'cmd_invoke' && group_by === 'target') return A.top_commands || { total_count: 0, rows: [] };
    if (!kind && group_by === 'kind') return A.kind_mix || { total_count: 0, rows: [] };
    return { total_count: 0, rows: [] };
  };
  return {
    ...STUB,
    health: async () => ({
      uptime_seconds: 0,
      daemon_version: `static snapshot · ${S.generated_at_utc || ''}`,
      llm_reachable: false, whisper_loaded: false, tts_loaded: false,
      pid: 0, rss_bytes: 0, cpu_percent: 0, guild_count: 0,
    }),
    get_stats: async () => S.stats || { mock: true },
    soundboard_list: async () => S.soundboard || [],
    emoji_list: async () => S.emoji || [],
    query_stats: queryStats,
  };
}

const SNAPSHOT_BRIDGE = SNAPSHOT ? makeSnapshotBridge(SNAPSHOT) : null;

function make(name) {
  return async (...args) => {
    if (SNAPSHOT_BRIDGE) return SNAPSHOT_BRIDGE[name](...args);
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
  soundboardList: make('soundboard_list'),
  emojiList: make('emoji_list'),
  queryStats: make('query_stats'),
  backlogEvents: make('backlog_events'),
  popEventBatch: make('pop_event_batch'),
  minimize: make('window_minimize'),
  maximize: make('window_maximize'),
  close: make('window_close'),
};

export const SNAPSHOT_META = SNAPSHOT
  ? {
      generated_at_utc: SNAPSHOT.generated_at_utc || '',
      schema_version: SNAPSHOT.schema_version || 0,
      window_seconds: SNAPSHOT.window_seconds || 0,
    }
  : null;
