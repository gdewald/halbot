# Project Restructure — Implementation Plan

Status: completed. Track execution of design in
[002-project-restructure.md](002-project-restructure.md). Only reviewed
phases live here. Unreviewed future phases sit in untracked working
draft.

## Approach

- **Changes in `feature branch` per phase.** Each phase lands on branch, next phase branches of previous phase.
- Phases independently runnable at endpoints.

## Phase 1 — Skeleton: daemon + tray + build/deploy

Prove build, install, service, gRPC, tray-to-daemon round-trip
end-to-end **before** any Discord/voice/LLM code moves. Fresh scaffolds,
**no reuse of current `bot.py` / `halbot_tray.py`**.

Branch: `restructure/phase-1-skeleton`. Old flat modules at repo root
(`bot.py`, `db.py`, `llm.py`, `audio.py`, `voice.py`, `voice_session.py`,
`tts.py`, `halbot_tray.py`, `prompts/`) **deleted early this phase**.
Branch holds only new skeleton. No coexistence.

## Phase 2

Goal: Integrate Discord bot functionality into new skeleton. Bridge old code to daemon model.

Focus: Core bot loop logic, command handling, voice/LLM interaction via Daemon internal mechanisms.

Constraint: gRPC API exposed only for tray management utility (Service Start/Stop/Restart, log viewing). All primary bot/LLM logic
remains within daemon process memory, not exposed via API.
