# Project Restructure — Phase 2 Implementation Plan

Integration Target: Discord bot code from main branch moves into skeleton daemon service.
Scope Focus: Replicate existing Discord functionality (command parsing, message handling) within the halbot/daemon.py process
lifecycle.
Mechanics:
1. Discord Listener Hook: Implement background listener in Daemon to intercept Discord events.
2. Message Router: Develop internal router: takes incoming Discord message -> parses command/intent -> calls
appropriate service handler (e.g., voice, LLM).
3. State Management: Integrate necessary session state management previously handled by the full bot context into the daemon's
persistent memory structure.
4. Inter-Component Call: Use internal function calls/module imports (`halbot.voice.VoiceSession`) instead of network RPCs
for LLM/voice stack execution.

Deliverable: Functional, skeleton Discord bot integrated with Phase 1 service framework. gRPC remains limited to tray utility
functions.
