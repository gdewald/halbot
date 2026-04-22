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
  query_stats: async () => ({ total_count: 0, rows: [] }),
  backlog_events: async () => [],
  pop_event_batch: async () => [],
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
  queryStats: make('query_stats'),
  backlogEvents: make('backlog_events'),
  popEventBatch: make('pop_event_batch'),
  minimize: make('window_minimize'),
  maximize: make('window_maximize'),
  close: make('window_close'),
};
