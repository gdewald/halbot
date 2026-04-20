// Mock data for the Stats panel preview. Values surface behind a
// visible "preview only" overlay because the backing subsystems
// (soundboard, voice, wake-word, STT, TTS, LLM) are not yet
// implemented. Do not remove the overlay without replacing these
// values with real telemetry from GetStats.

export const SOUND_LIST = [
  { name: 'airhorn',   emoji: '📯', desc: 'Classic airhorn blast', plays: 142, lastPlayed: '10 min ago', size: '48 KB' },
  { name: 'bruh',      emoji: '😐', desc: '',                      plays: 89,  lastPlayed: '2 hrs ago',  size: '32 KB' },
  { name: 'vine boom', emoji: '💥', desc: 'Vine era impact sound', plays: 211, lastPlayed: 'just now',   size: '61 KB' },
  { name: 'rizz',      emoji: '😎', desc: '',                      plays: 34,  lastPlayed: 'yesterday',  size: '29 KB' },
  { name: 'nope',      emoji: '🚫', desc: '',                      plays: 17,  lastPlayed: '3 days ago', size: '18 KB' },
  { name: 'tada',      emoji: '🎉', desc: 'Tada fanfare',          plays: 55,  lastPlayed: '1 hr ago',   size: '72 KB' },
];

export const WAKE_HISTORY = [
  { time: '09:41:22', phrase: 'hey hal', action: 'Joined #gaming, played vine_boom.mp3', ok: true },
  { time: '09:28:07', phrase: 'hey hal', action: 'Joined #general, played airhorn.mp3',  ok: true },
  { time: '08:55:44', phrase: 'hey hal', action: 'False positive — no voice channel',    ok: false },
  { time: '07:12:03', phrase: 'hey hal', action: 'Joined #music, played bruh.mp3',       ok: true },
];

// Aggregate numbers used in the stat cards. Single source of truth
// so the overlay can be unambiguous about "none of these are real."
export const MOCK_NUMBERS = {
  soundboard: { backedUp: 847, storageMb: 214, lastSync: '4m ago',      lastSyncTs: '09:38:12 today', newSince: 12 },
  voice:      { playedToday: 38, playedAllTime: 2841, sessionToday: '1h 12m', avgResponseMs: 340 },
  wake:       { detectionsToday: 4, detectionsAllTime: 312, falsePositivesToday: 1, falsePctToday: '0.3%', avgJoinMs: 620 },
  stt:        { latencyAvg: 210, latencyP95: 480, chunkAvg: 85, chunkP95: 140, segmentsToday: 187, avgUtteranceSec: 3.2 },
  tts:        { firstChunkAvg: 128, firstChunkP95: 290, fullAvg: 340, fullP95: 680, rendersToday: 42, fallbacksToday: 3 },
  llm:        {
    responseAvgMs: 1240, responseP95Ms: 4800,
    ttftAvgMs: 320, ttftP95Ms: 890, tokPerSec: 38,
    requestsToday: 52, avgTokensOut: 184, ctxUsagePct: 62, timeoutsToday: 2,
  },
};
