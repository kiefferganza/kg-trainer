# 05: Send a plan to Liftosaur

**What to build:** A Canonical Plan becomes a Liftosaur program and reaches the user's app. Compilation emits one program covering every week and day. Each Progression Rule maps to its Liftosaur form (`linear`→`lp`, `double`→`dp`, `reps_threshold`→`sum`, `none`→`progress: none`); Liftosaur Native Rules pass through verbatim, and a Native Rule for another app fails loudly. Liftosaur's server-side stats and parse check run as a cross-check against the local stats, and any disagreement is reported rather than resolved. The plan can be pushed, updated and fetched through the single app CLI. The playground simulator stays reachable as a Liftosaur-only extra.

**Blocked by:** 03 (Liftosaur reading behind the contract), 04 (Canonical Plan authoring)

**Status:** ready-for-agent

- [x] Compiled Liftoscript for a fixture plan matches an approved golden file
- [x] A plan whose Native Rule names another app fails to compile with a message naming the exercise
- [x] The cross-check reports local and Liftosaur weekly set counts side by side and flags differences
- [x] Push returns and records the program id in `profile.json`; no id means the push is reported as failed
- [x] Update and get work by id and by `current`
- [x] Live verification against the user's Liftosaur account is recorded, including one recorded playground response with `finish_workout()` for ticket 07

**Live check 2026-11-07 (main session, read-only and simulation calls only):** the first compile was rejected (422) because it re-declared warmups in later weeks; fixed in 9beeaa2. The fixture plan now compiles, parses in `program-stats` and `playground`, and `app.py check` agrees with local stats on every muscle once synergist sets are excluded. The live `program-stats` response is `tests/fixtures/liftosaur_program_stats.live.json`. The playground fixture for ticket 07 is `tests/fixtures/playground_finish.json` (week 1, Upper A, every set of Bench Press and Lat Pulldown completed, then `finish_workout()`). Push, get and update have **not** been run live, because they write to the user's account.

**Live push check 2026-11-07 (user approved):** `app.py push` created program `opwlyhxs` ("kg-trainer test") and recorded it as `app.program_id`; the user's current program stayed current. `app.py get` returned it. `app.py update --id opwlyhxs` changed Bench Press from 80 kg to 85 kg, confirmed by a second `get`. Liftosaur stores the text with `///` lines and some blank lines removed (`///` is a Liftoscript comment), so the phase label the compiler writes under each `# Week` heading does not survive in the app. The test program was left in the account for the user to delete.
