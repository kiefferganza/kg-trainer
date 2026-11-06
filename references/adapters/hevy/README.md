# Hevy Adapter

Module: `scripts/adapters/hevy.py`. Auth header `api-key`, key from `$HEVY_API_KEY`, then `.env`, then `kg-trainer-data/hevy-key`. Hevy Pro required.

## Capabilities

Read side: `ping`, `pull_history`, `pull_bodyweight`, `find_exercise`. Write side (ticket 06): `compile`, `push_plan`, `update_plan`, `get_plan`. There is no `check`: Hevy has no server-side validation, so local `plan.py validate` and `plan.py stats` are the only checks (`app.py check` says so and exits non-zero).

Hevy has no weeks and no progression. `compile` emits one week (default `current_week`): one routine per day, titled with the day name. `push_plan` creates one routine folder and a routine per day; the folder id is the plan id (`app.program_id`). `update_plan` PUTs each routine in that folder, matched by day name. Cue and RIR target go in each exercise's `notes`; a working set under a `double` rule is written as `rep_range`; set `kind` maps working/warmup/drop/failure to normal/warmup/dropset/failure. A Native Rule for another app makes `compile` refuse. Hevy has no DELETE, so `app.py push` and `update` ask before writing (`--yes` skips).

An exercise with no template id (shipped map, personal `exercise-map-hevy.json`, or an exact title match in the cached catalogue) stops `compile` and the push, listing what needs a custom exercise. Cache the catalogue with `python scripts/adapters/hevy.py sync-catalogue` (read-only GET).

## Exercise map

`references/adapters/hevy/exercises.json` maps Canonical Exercise Name to the Hevy title and template id. The shipped file is a small **seed** with titles only (ids null): it covers common lifts and has not been built from the live catalogue. Rebuild it with `python scripts/adapters/hevy.py build-map` (live, needs the key) or `--catalogue FILE` on a saved JSONL; exact `Name (Equipment)` matches are written, default-equipment guesses are listed for review and never written.

`kg-trainer-data/exercise-map-hevy.json` overrides the shipped map entry by entry (same shape, or `{"Canonical": "Hevy title"}`). Unmatched Hevy titles are kept as they are and show up in `unclassified_exercises`.

## Verification status

- Offline: recorded-shape fixtures only (tests/test_adapter_hevy.py).
- Write path (compile, push, update, get): **UNVERIFIED against the live API.** The user has no Hevy Pro (2026-11-07), so it is tested on fixtures only (tests/test_hevy_write.py, golden payloads) and ships marked unverified. The response wrappers (POST/GET `{routine}`, PUT `{routine: [..]}`, folder `{routine_folder}`) and the 403 routine-limit behaviour come from the 2026-09-18 notes. Treat the first real push as an experiment.
- Read path live: **not verified either**. Facts carried over from 2026-09-18 (page size 10, `page_count`, `api-key` header, `weight_kg`, logged-set `rpe`) must be re-checked with a Hevy Pro key and the date recorded here.
