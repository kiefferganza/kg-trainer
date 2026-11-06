# 05: Send a plan to Liftosaur

**What to build:** A Canonical Plan becomes a Liftosaur program and reaches the user's app. Compilation emits one program covering every week and day. Each Progression Rule maps to its Liftosaur form (`linear`→`lp`, `double`→`dp`, `reps_threshold`→`sum`, `none`→`progress: none`); Liftosaur Native Rules pass through verbatim, and a Native Rule for another app fails loudly. Liftosaur's server-side stats and parse check run as a cross-check against the local stats, and any disagreement is reported rather than resolved. The plan can be pushed, updated and fetched through the single app CLI. The playground simulator stays reachable as a Liftosaur-only extra.

**Blocked by:** 03 (Liftosaur reading behind the contract), 04 (Canonical Plan authoring)

**Status:** ready-for-agent

- [x] Compiled Liftoscript for a fixture plan matches an approved golden file
- [x] A plan whose Native Rule names another app fails to compile with a message naming the exercise
- [x] The cross-check reports local and Liftosaur weekly set counts side by side and flags differences
- [x] Push returns and records the program id in `profile.json`; no id means the push is reported as failed
- [x] Update and get work by id and by `current`
- [ ] Live verification against the user's Liftosaur account is recorded, including one recorded playground response with `finish_workout()` for ticket 07
