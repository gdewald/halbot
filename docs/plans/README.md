# Plans index

Status snapshot of every plan tracked under `docs/plans/`.

Statuses:
- **completed** — design landed, code on `main`, feature is live.
- **incomplete** — partially landed or deferred mid-flight.
- **discarded** — superseded by a different approach or abandoned.
- **reference** — design preserved for future-you; never implemented
  as written, but the thinking is worth keeping.

Workflow: drafts live under `drafts/` (gitignored), get promoted to
`<NNN>-...-impl.md` here when approved + implemented, and reference
material lives in `reference/`.

## Numbered plans (`docs/plans/*.md`)

| File | Status | Notes |
|---|---|---|
| `001-voice-text-decoupling.md` | completed | Voice/text split landed in `voice_session.py` + `voice.py`. |
| `002-project-restructure.md` | completed | Original umbrella design; execution was 003 + per-phase plans. |
| `003-project-restructure-impl.md` | completed | Phases 1–3 all merged. |
| `004-project-restructure-phase1.md` | completed | Daemon/tray skeleton. |
| `005-project-restructure-phase2.md` | completed | Discord/voice/LLM port back in-process. |
| `006-project-restructure-phase3.md` | completed | Migration tool shipped, used, then retired (`migrate_v050.py` removed once everyone was off v0.5). |
| `007-gui-dashboard.md` | completed | Umbrella for the v0.7 dashboard. |
| `007-gui-dashboard-design.md` | completed | Locked mockup — used by every step plan below. |
| `007-step-1-proto.md` … `007-step-9-validation.md` | completed | All 9 steps shipped; Stats / Logs / Daemon / Config / Analytics panels live. |
| `008-analytics-events.md` | completed | events.db writer + `QueryStats` / `StreamEvents` RPCs + Analytics panel. |
| `014-discord-embed-flows-impl.md` | completed | Embed mockup grammar adopted via `bot_ui.py` + `interactions.py` + `slash.py`. |
| `016-voice-pipeline-benchmarks-impl.md` | completed | `benchmarks/` runner + multiple result sets under `benchmarks/_out/`. |
| `017-wake-variants-impl.md` | completed | LLM-generated wake-variant dictionary + `/halbot-admin wake-variants` slash group. |
| `018-transcript-capture-impl.md` | completed | Rotating JSONL transcript log, dashboard-toggleable. |
| `020-static-stats-publish-impl.md` | completed | `/halbot-stats` snapshot publisher + Cloudflare R2 infra. v0.8.0. |

## Drafts (`docs/plans/drafts/`, gitignored)

Each lives outside source control on purpose — these are pre-approval
sketches that may or may not become real plans.

| File | Status | Notes |
|---|---|---|
| `009-hot-reload.md` | incomplete | Still on the wishlist; no daemon hot-reload landed. |
| `011-smoke-test-rpc.md` | incomplete | `SmokeTest` RPC not added; design still relevant. |
| `012-persistent-llm-hooks.md` | incomplete | Persona-hook system not built. |
| `019-capability-surfaces.md` | incomplete | Sketch only; no implementation started. |

Removed (delete from local checkouts if you have them):
- `010-fast-build-deploy.md` — superseded by `scripts/deploy.ps1`.
- `013-fix-fonts.md` — completed; fonts bundled in
  `frontend/src/fonts/`.
- `phase-backlog.md` — superseded by 003/004/005/006.

## Reference (`docs/plans/reference/`)

Tracked, but not roadmap items — design archive.

| File | Status | Notes |
|---|---|---|
| `015-voice-integration-tests.md` | reference | L2/L3 harness shipped as plan 016; L0/L1 unit-test scaffold never built. Useful starting point if we add a `tests/` tree later. |
