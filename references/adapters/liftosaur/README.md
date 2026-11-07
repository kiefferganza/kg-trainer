# Liftosaur Adapter

Module: `scripts/adapters/liftosaur.py`. Base `https://www.liftosaur.com/api/v1`, `Authorization: Bearer lftsk_...`, key from `$LIFTOSAUR_API_KEY`, then `.env`, then `kg-trainer-data/liftosaur-key`. A Liftosaur premium subscription is required.

Other files here: `liftosaur-api.md` (endpoint map, pagination, measurements), `liftohistory.md` (the workout record format, vendored), `liftoscript.md` (the program language, vendored; refresh both with `scripts/fetch_reference.py`).

## Capabilities

All nine contract operations: `ping`, `pull_history`, `pull_bodyweight`, `find_exercise`, `compile`, `push_plan`, `update_plan`, `get_plan`, `check`. Nothing falls back.

- `pull_history` reads every page of `GET /history` (cursor pagination, `limit` 200) and parses the Liftohistory text. Pounds are converted to kg. Exercise names are Liftosaur's own, which are the Canonical Exercise Names, so translation is the identity; a custom exercise is kept as written and lands in `unclassified_exercises`.
- `pull_bodyweight` reads the `weight` measurement: one reading per local day, the latest weigh-in of the day winning, a value with no unit skipped.
- `compile` emits one Liftoscript program covering every week. `progress:` is generated from each Progression Rule (`linear` to `lp`, `double` to `dp`, `reps_threshold` to `sum`, `none` to `progress: none`); a Native Rule for `liftosaur` is written verbatim and one for any other app fails the compile.
- **Native progression.** The adapter declares `NATIVE_PROGRESSION = True`. Liftosaur moves the weights itself when a workout is finished, and (verified live, `tests/fixtures/playground_finish.json`) the change lands in that exercise in the week just done and in every later week, deloads and weeks that say `progress: none` included, each keeping its authored reps and load offset. `plan.py progress` applies the same change to the Canonical Plan. Before `app.py update` writes, `prescriptions()` parses the program from `get_plan` (Liftosaur reformats stored text, e.g. `3x7 / 80kg 180s`) and the update is refused if the plan's `current_week` differs; `--force` overrides.
- `push_plan` is `POST /programs`, `update_plan` is `PUT /programs/:id` (`current` works), `get_plan` is `GET /programs/:id`. A response with no id is a failed push.
- `check` runs `POST /program-stats` and `POST /playground` (a parse check) on the compiled text.

## Quirks

- **Warmups are declared once.** Liftosaur fixes an exercise's warmups at its first occurrence in the program. Re-declaring them in a later week, even as `warmup: none`, is a 422 parse error (found live 2026-11-07). `compile` therefore writes `warmup:` only the first time an exercise appears, and prints a warning to stderr when a later week's warmups differ (the app keeps the first week's).
- **`program-stats` names muscles in camelCase** (`upperBack`, `rearDelts`, `sideDelts`, `frontDelts`, `upperTraps`, `rotatorCuff`, ...). They are folded into the skill's vocabulary in `MUSCLE_TO_SKILL`: the three delt heads become `shoulders`, `rotatorCuff` has no skill muscle and is shown as returned, and the group `back` is compared with the sum of `lats + upper_back + lower_back + traps`.
- **`program-stats` credits synergists.** Each `muscleGroups` entry lists exercises with `isSynergist`; `totalSets` includes fractional synergist credit. `plan.py stats` counts each working set once under its primary muscle, so `check` sums only the non-synergist exercise sets. With that, the live check agreed with local stats on every muscle. Any remaining difference is reported (exit 2), never resolved.
- **`playground` simulates, it does not log.** `POST /playground {programText, week?, day?, commands?}` returns the resolved workout. With no `commands` it is a parse check and the workout lists no completed sets; pass `complete_set(ex, set)` to see the targets. `finish_workout()` also returns `updatedProgramText`, the program after Liftosaur's own progression ran. Week and day are 1-based. It is Liftosaur-only: `scripts/liftosaur.py playground FILE --week N --day N --command ... [--record FILE]` (`--record` saves the response as a fixture).
- **Cursors are millisecond timestamps** (numbers), not strings; a `hasMore` with no usable `nextCursor`, or a repeated cursor, stops the pull loudly instead of returning a partial history.
- **Logged versus prescribed RPE.** In Liftohistory `@8+` is an RPE the athlete logged and a bare `@8` is the prescription; only the first has `rpe_logged: true`.
- No DELETE for programs is used; `push` and `update` ask first (`--yes` skips).

## Verification status

Last verified live: **2026-11-07**, with the user's real account.

- **Reads, live-verified.** A real account (370 workouts, 287 weigh-in days) was pulled into throwaway stores with the code before and after the Adapter refactor. `workouts.jsonl`, `measurements.json`, `report` and `deep-dive` came out byte-identical (the report differed only in the store path it prints). Golden copies of the fixtures are in `tests/fixtures/golden/` (`tests/test_liftosaur_golden.py`).
- **`compile`, `check`, `playground`: live-verified.** The fixture plan compiles, parses in `program-stats` and `playground`, and `app.py check` agrees with local stats. The live `program-stats` response is `tests/fixtures/liftosaur_program_stats.live.json`; a `playground` response with `finish_workout()` is `tests/fixtures/playground_finish.json`.
- **`push_plan`, `get_plan`, `update_plan`: live-verified 2026-11-07.** A test program was pushed, fetched and updated (Bench Press 80 kg to 85 kg, confirmed by a second fetch); the account's current program stayed current. Liftosaur stores the text reformatted: `///` comment lines and some blank lines are dropped, and set lines are rewritten (`3x7 / 80kg 180s`). Compare prescriptions, never text.
- Offline: the shared contract suite (`tests/test_contract.py`, provider `tests/contract_fixtures/liftosaur.py`).
