# 06: Send a plan to Hevy

**What to build:** A Canonical Plan reaches a Hevy account even though Hevy has no weeks and no progression. The Hevy Adapter compiles only `current_week`: one routine per day, inside one routine folder per block, with cue and RIR target in each exercise's notes and rep ranges as ranges. Push creates the folder and routines and records the folder as the plan id. Update rewrites those routines. Get returns them. A routine limit (403) stops and tells the user. An exercise with no Hevy match stops the push and proposes creating a custom exercise; it is never fuzzy-matched. Because Hevy has no delete, every write asks the user first.

**Blocked by:** 02 (Hevy user gets a baseline report), 04 (Canonical Plan authoring)

**Status:** ready-for-agent

- [x] The compiled routines for a fixture plan and week match approved golden payloads
- [x] Push handles Hevy's inconsistent response wrappers (object on POST and GET, array on PUT) and returns the folder id
- [x] Update strips the fields Hevy's GET returns but its PUT rejects
- [x] A 403 on routine creation stops with a clear message and nothing half-written is reported as success
- [x] An unmatched exercise blocks the push and lists what needs a custom exercise
- [x] Hevy declares no `check`; the CLI says local validate and stats are the only checks
- [ ] Live verification against the user's Hevy account is recorded with a date
      deferred: user has no Hevy Pro (2026-11-07); ships fixture-tested and marked unverified
