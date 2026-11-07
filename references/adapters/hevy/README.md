# Hevy Adapter

Module: `scripts/adapters/hevy.py`. Auth header `api-key`, key from `$HEVY_API_KEY`, then `.env`, then `kg-trainer-data/hevy-key`. Hevy Pro required.

## Capabilities

Eight of the nine contract operations. Read side: `ping`, `pull_history`, `pull_bodyweight`, `find_exercise`. Write side: `compile`, `push_plan`, `update_plan`, `get_plan`. There is no `check`: Hevy has no server-side validation, so local `plan.py validate` and `plan.py stats` are the only checks (`app.py check` says so and exits non-zero).

Hevy has no weeks and no progression. `compile` emits one week (default `current_week`): one routine per day, titled with the day name. `push_plan` creates one routine folder and a routine per day; the folder id is the plan id (`app.program_id`). `update_plan` PUTs each routine in that folder, matched by day name. Cue and RIR target go in each exercise's `notes`; a working set under a `double` rule is written as `rep_range`; set `kind` maps working/warmup/drop/failure to normal/warmup/dropset/failure. A Native Rule for another app makes `compile` refuse. Hevy has no DELETE, so `app.py push` and `update` ask before writing (`--yes` skips).

An exercise with no template id (shipped map, personal `exercise-map-hevy.json`, or an exact title match in the cached catalogue) stops `compile` and the push, listing what needs a custom exercise. Cache the catalogue with `python scripts/adapters/hevy.py sync-catalogue` (read-only GET).

## Exercise map

`references/adapters/hevy/exercises.json` maps Canonical Exercise Name to the Hevy title and template id. The shipped file is a small **seed** with titles only (ids null): it covers common lifts and has not been built from the live catalogue. Rebuild it with `python scripts/adapters/hevy.py build-map` (live, needs the key) or `--catalogue FILE` on a saved JSONL; exact `Name (Equipment)` matches are written, default-equipment guesses are listed for review and never written.

`kg-trainer-data/exercise-map-hevy.json` overrides the shipped map entry by entry (same shape, or `{"Canonical": "Hevy title"}`). Unmatched Hevy titles are kept as they are and show up in `unclassified_exercises`.

## Verification status

**Unverified: the user has no Hevy Pro (2026-11-07).** Nothing in this Adapter has been run against the live API, for reads or for writes. Everything below is tested on hand-built fixtures shaped like the documented responses and on the facts recorded from the 2026-09-18 live session of the earlier Hevy client.

- Reads (`ping`, `pull_history`, `pull_bodyweight`, `find_exercise`): **unverified.** Page size 10 and `page_count` pagination, the `api-key` header, `weight_kg`, and the logged-set `rpe` field are carried over from 2026-09-18 and must be re-checked with a Hevy Pro key, with the date recorded here. The shipped exercise map is a titles-only seed, not built from the live catalogue.
- Writes (`compile`, `push_plan`, `update_plan`, `get_plan`): **unverified.** Tested on fixtures and golden payloads only (`tests/test_hevy_write.py`). The response wrappers (POST/GET `{routine}`, PUT `{routine: [..]}`, folder `{routine_folder}`) and the 403 routine-limit behaviour come from the 2026-09-18 notes. Treat the first real push as an experiment.
- Offline: `tests/test_adapter_hevy.py`, `tests/test_hevy_write.py`, and the shared contract suite (`tests/test_contract.py`, provider `tests/contract_fixtures/hevy.py`).

When a Hevy Pro key is available: run `python scripts/adapters/hevy.py sync-catalogue`, `python scripts/adapters/hevy.py build-map`, then `app.py ping`, `app.py pull`, and a throwaway `app.py push`; record each result and the date in this section, and fix the fixtures wherever the live shape differs.
