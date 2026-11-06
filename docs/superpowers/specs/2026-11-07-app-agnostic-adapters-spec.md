# Spec — Make kg-trainer app-agnostic through an Adapter Contract

**Date:** 2026-11-07
**Status:** Approved for planning
**Supersedes:** decision 3 ("hard replace, no provider abstraction") of
`2026-10-11-liftosaur-migration-spec.md`. See `docs/adr/0001-adapter-contract-over-single-app.md`.
**Vocabulary:** terms in bold below are defined in `GLOSSARY.md`; use them exactly.

## Why

kg-trainer works only with Liftosaur. Every script imports `liftosaur.py`, the plan's source
of truth is Liftoscript, progression is whatever Liftosaur's `progress:` does, volume checks
are Liftosaur's `program-stats`, and exercise identity is Liftosaur's naming. An athlete who
logs in any other app gets a coach that cannot read their training.

The goal is a skill that coaches from any **Training App** with an API, without giving up the
rule that makes it trustworthy: every number comes from a reproducible script over local data,
never from the agent improvising API calls in a session.

## Decisions

Settled with the user on 2026-11-07 in a grilling session. Not open for re-litigation during
execution.

1. **Pre-built adapters, not runtime-generic access.** The skill defines an **Adapter Contract**
   and ships one tested **Adapter** per app. Supporting a new app means writing an Adapter in
   the skill repo; the agent may scaffold one from the app's API docs, but it is then code with
   tests, not something improvised each session.
2. **Only reading history is required.** Every other **Capability** is optional, and each
   missing one has a named **Fallback** owned by the skill.
3. **The Canonical Plan is the source of truth.** One app-neutral JSON plan per block; each
   Adapter compiles it into its app's format. App-native program text is an output, never an
   input.
4. **Canonical Exercise Names belong to the skill.** Adapters translate between app names and
   canonical names. The vocabulary stays seeded from Liftosaur's built-in list
   (`references/exercises.txt`), accepted knowingly as not neutral by origin.
5. **One Active App per store.** No mixing apps for history, bodyweight or plans.
6. **This change ships two Adapters: Liftosaur and Hevy.** A second real app is what keeps the
   contract from quietly taking Liftosaur's shape. Hevy, with no weeks and no progression, is
   the stress test. The user has a Hevy Pro key, so Hevy is verified live.
7. **Canonical Plan is JSON** (`plans/meso-NN.plan.json`), because the scripts are stdlib-only.
   `meso-NN.md` is generated from it and never hand-edited.
8. **Progression Rule vocabulary:** `linear(increment)`, `double(increment, low, high)`,
   `reps_threshold(total, increment)`, `none`, plus a **Native Rule**
   `{"native": "<app>", "text": "..."}` that only that app's Adapter compiles. Compiling a
   Native Rule for any other app fails loudly.
9. **The skill, not the app, guarantees progression.** `plan.py progress` computes the next
   prescriptions from the **Workout Log** and the plan's rules. The coach shows the result,
   gets a yes, and pushes it with `update_plan` if the app supports that. Nobody applies rules
   by hand.
10. **Validation and stats run locally for every app.** `plan.py validate` and `plan.py stats`
    work on the Canonical Plan. Server-side checks (Liftosaur `program-stats`, `playground`)
    run as a cross-check after compiling, and any disagreement is reported, not resolved
    silently.
11. **Layout:** `scripts/adapters/<app>.py`, one module per app, each declaring its
    Capabilities; `scripts/app.py` is the single CLI and dispatches to the Active App.
    App-only extras (Liftosaur `playground`) stay as subcommands of the adapter module.
    `analyze.py`, `checkin.py` and `dashboard.py` import only the contract.
12. **The Workout Log keeps today's `workouts.jsonl` shape,** with `title` holding the
    Canonical Exercise Name and every weight in kilograms.
13. **API keys per app:** `$<APP>_API_KEY`, then the nearest `.env`, then
    `kg-trainer-data/<app>-key`. Same rules as today: never echoed, failures report the HTTP
    status only, redirects refused.
14. **Existing stores migrate on first run.** `profile.json`'s `liftosaur` block becomes `app`.
    An existing `.liftoscript` plan stays as a historical file with no reverse-parse; the next
    block or PROGRESS IT starts a Canonical Plan.
15. **Exercise translation:** identity for Liftosaur. Hevy carries a shipped map from canonical
    name to Hevy exercise template id plus a personal override in the store. An unmatched name
    on pull goes to `unclassified_exercises`; on push it stops and proposes creating a custom
    exercise. Never fuzzy-matched silently.
16. **Apps without weeks compile only the current week.** Hevy gets one routine per day in one
    routine folder per block; each progression rewrites those routines to the next week.
17. **Dashboard plan tabs read the Canonical Plan** from `plans/`, with no API call. Until a
    store has a Canonical Plan, they read the newest `plans/*.liftoscript` from disk instead, so
    an existing Liftosaur store keeps its plan tabs.
18. **Choosing and switching:** STEP 1 asks which app when `profile.json` names none, showing
    the shipped Adapters and their Capabilities. `app.py use <name>` switches explicitly,
    replaces the `data/` cache on the next pull and recompiles the current Canonical Plan.
    `checkins.jsonl`, `weight-log.csv` and `plans/` are untouched.
19. **New Adapters are built only in the skill repo.** A store holds data, never code. An Adapter
    is done when it passes the shared contract test suite on recorded fixtures and has
    `references/adapters/<app>/README.md` stating its Capabilities and verification status.
20. **References layout:** `liftosaur-api.md`, `liftoscript.md` and `liftohistory.md` move to
    `references/adapters/liftosaur/`. `programming.md` and `state.md` stay top-level. SKILL.md's
    description triggers name "a training app with an API" plus Liftosaur and Hevy. STEP 2's
    equipment list uses the skill's canonical equipment suffixes.

## The Adapter Contract

Each Adapter module exposes `NAME`, `CAPABILITIES` (a set of the operation names it
implements) and those operations. Calling an operation an Adapter does not declare is a
programming error; the CLI checks `CAPABILITIES` first and prints the Fallback instead.

| Operation | Required | Returns | Fallback when absent |
|---|---|---|---|
| `ping()` | yes | `{"account": str, "units": "kg"\|"lb"}` | — |
| `pull_history()` | yes | full Workout Log, newest first, already translated | — |
| `pull_bodyweight()` | no | `[(date, kg)]`, one per day, latest reading of the day | `checkin.py log-weight` |
| `find_exercise(pattern)` | no | `[{"canonical": str, "app_id": str\|None}]` | the shipped canonical list only |
| `compile(plan, week=None)` | no | app-format payload; local, no network | `meso-NN.md`, entered by hand |
| `push_plan(compiled, name)` | no | app id of the created plan | `meso-NN.md`, entered by hand |
| `update_plan(compiled, id)` | no | app id | push again, or hand edit |
| `get_plan(id\|"current")` | no | the app's current plan payload | the Canonical Plan in `plans/` |
| `check(compiled)` | no | `{"ok": bool, "weekly_sets": {muscle: n}, "errors": [...]}` | local `plan.py validate`/`stats` only |

Rules every Adapter follows:

- It is the only code that knows its app's URLs, payloads, pagination and quirks.
- `pull_history` fetches everything and the caller replaces the cache wholesale, as today.
  Pagination that does not terminate cleanly fails loudly.
- Weights leave the Adapter in kilograms. Pound accounts are converted on read.
- RPE in the Workout Log is only what the athlete logged, never a prescribed target.
- Errors report the HTTP status and the operation, never the key.

## The Canonical Plan

```jsonc
{
  "schema": 1,
  "name": "Meso 2 — Upper/Lower",
  "created": "2026-11-07",
  "current_week": 1,                 // the week the app currently shows; advanced by `progress`
  "weeks": [
    { "number": 1, "phase": "accumulation", "deload": false,
      "days": [
        { "name": "Upper A",
          "exercises": [
            { "exercise": "Bench Press",                // Canonical Exercise Name
              "sets": [ { "reps": 8, "load_kg": 80, "rpe": null, "kind": "working" } ],
              "rest_seconds": 180,
              "cue": "Pause one inch off the chest.",
              "rir_target": 2,
              "superset": null } ] } ] } ],
  "progression": {
    "Bench Press":          { "rule": "double", "increment_kg": 2.5, "low": 6, "high": 8 },
    "Lat Pulldown":         { "rule": "linear", "increment_kg": 2.5 },
    "Leg Press":            { "native": "liftosaur", "text": "custom() {~ ... ~}" }
  }
}
```

A week with `"deload": true` has progression off for every exercise. That is the only way to
switch progression off for a whole week. A deload week that should still progress one lift
sets that lift's rule on the week itself (`"progression": {"<exercise>": {...}}` inside the
week object), which overrides the block-level rule for that week only.

`plan.py` owns this file and offers:

- `validate <plan>`: schema, canonical names known (shipped or personal map), every Progression
  Rule well-formed, and every barbell, smith-machine, EZ-bar and trap-bar load a multiple of
  2.5 kg. Equipment comes from the name suffix; a bare name is the default equipment, and the
  barbell lifts among bare names (Squat, Bench Press, Deadlift and their variants) are listed
  explicitly in `plan.py`. Dumbbell and machine loads are not checked,
  because the profile does not yet record dumbbell sizes or pin steps in structured form; the
  coach still rounds them, as SKILL.md requires.
- `stats <plan>`: weekly working sets per muscle from the muscle map. This replaces counting by
  hand everywhere, for every app.
- `render <plan>`: writes `meso-NN.md`, one table per session.
- `progress <plan>`: reads the Workout Log for `current_week`, applies each exercise's rule to
  what was actually logged, writes the next week's prescriptions back to the plan and advances
  `current_week`. It prints a before/after table and changes nothing in the app. `app.py update`
  then compiles `current_week` for apps that compile one week at a time.

Native Rules are not interpreted by `plan.py progress`. An exercise with a Native Rule is
progressed by its app, and `progress` reports it as "run by <app>".

## Liftosaur Adapter

Everything `scripts/liftosaur.py` does today, moved behind the contract. Facts and quirks are
already recorded in `references/liftosaur-api.md`, `liftoscript.md` and `liftohistory.md`.

- Capabilities: all nine operations.
- `compile` emits one Liftoscript program covering every week (`# Week N`, `## Day`), with
  `progress:` generated from each rule: `linear` → `lp`, `double` → `dp`,
  `reps_threshold` → `sum`, `none` → `progress: none`, Native Rule → its text verbatim.
- `check` calls `program-stats`, plus `playground` for a parse check.
- Exercise translation is identity.
- `playground` with `--command` stays a subcommand of `scripts/adapters/liftosaur.py`.

## Hevy Adapter

Restore the facts from git history before writing code: `git show a9c6d08^:references/hevy-api.md`
and `git show a9c6d08^:scripts/hevy.py`. They were verified against the live API on
2026-09-18. The facts that shape the Adapter:

- Base `https://api.hevyapp.com`, header `api-key: <uuid>`, requires Hevy Pro. All weights are
  kg. Env var `HEVY_API_KEY`.
- `pageSize` caps at 10 everywhere except `exercise_templates` (100). Paginate on
  `page_count`.
- `ping` → `GET /v1/user/info`.
- `pull_history` → `GET /v1/workouts`, every page. Logged sets carry `rpe`; routine sets do not.
- `pull_bodyweight` → `GET /v1/body_measurements`, `weight_kg` per date.
- `find_exercise` → the cached `GET /v1/exercise_templates` catalogue. Template ids are opaque
  8-character hex strings and cannot be derived from names.
- `compile(plan, week)` → one routine payload per day of that week, cue and RIR in each
  exercise's `notes`, ranges as `rep_range`.
- `push_plan` → `POST /v1/routine_folders` for the block, then `POST /v1/routines` per day.
  The returned plan id is the folder id. `POST /v1/routines` can return 403 (routine limit);
  stop and tell the user.
- `update_plan` → `PUT /v1/routines/{id}` per day. GET returns `index` and `title`, which PUT
  rejects, so strip them before any round trip.
- `get_plan` → the folder's routines.
- No `check`: Hevy has no server-side validation.
- Response shapes the published spec gets wrong: POST routine is `{"routine": {...}}`, PUT is
  `{"routine": [{...}]}`, GET is `{"routine": {...}}`, and folders are
  `{"routine_folder": {...}}`. A routine's own `notes` is accepted on write and never returned.
- There is no DELETE endpoint, so every write asks the user first.

Re-verify each of these live with the user's key before relying on it, and record the result
and date in `references/adapters/hevy/README.md`. They were true two months ago, which is not
a guarantee.

## Requirements

### Functional

- `scripts/app.py` subcommands: `use <app>`, `apps` (shipped Adapters and their Capabilities),
  `ping`, `pull`, `bodyweight`, `exercises <pattern>`, `compile <plan>`, `push <plan> --name`,
  `update <plan> --id`, `get <id|current>`, `check <plan>`. Each refuses a missing Capability
  by printing its Fallback and exiting non-zero.
- `analyze.py pull` calls the Active App's `pull_history` and `pull_bodyweight` through the
  contract. `report` and `deep-dive` are unchanged in output.
- `checkin.py import-liftosaur` becomes `checkin.py import-app`, through `pull_bodyweight`.
- `dashboard.py build` reads the Canonical Plan for plan tabs (falling back to the newest
  `plans/*.liftoscript` until one exists) and the Workout Log for everything else, with no
  Adapter call.
- `profile.json` is migrated on first read: `liftosaur: {connected, key_source, program_id}`
  becomes `app: {name: "liftosaur", key_source, program_id}`. The migration is idempotent and
  logged once to stderr.
- SKILL.md STEP 4 writes a Canonical Plan. STEP 5 becomes "send it to the Active App":
  `validate` → `stats` → `compile` → `check` (if declared) → approval → `push`, with the
  Fallback path written out for apps that cannot receive a push. STEP 7's PROGRESS IT runs
  `plan.py progress` and then `update`.
- Each Adapter's exercise map can be overridden by `kg-trainer-data/exercise-map-<app>.json`.

### Non-functional

- Stdlib-only Python, as today.
- Tests run offline on recorded fixtures. Live verification is a separate, recorded step.
- One shared contract test suite, parametrized over every Adapter in `scripts/adapters/`, that
  any new Adapter must pass unchanged.
- A structural test fails if any module outside `scripts/adapters/` imports an app-specific
  module or contains an app's base URL. It replaces `tests/test_no_hevy.py`, which is deleted.
- The no-redirect opener, key resolution and status-only error reporting live in one shared
  module that every Adapter uses.
- Reading history, stats, the report and the dashboard produce byte-identical output for an
  existing Liftosaur store before and after the refactor. The refactor proves itself on real
  data.

### Out of scope

- Mixing apps in one store, or merging history across apps.
- Migrating history from one app to another.
- Reverse-parsing an existing `.liftoscript` into a Canonical Plan.
- Adapters beyond Liftosaur and Hevy.
- Writing logged workouts or bodyweight back to any app.

## Global constraints

- Personal data never lives in the skill directory; `paths.py` rules are unchanged.
- Two SKILL.md rules stay non-negotiable: never invent a number, and every step ends by
  writing state. Every new script output is a number the coach can quote.
- Never claim a push succeeded without an app id to show for it.

## Risks

- **The contract still takes Liftosaur's shape.** Mitigation: the Hevy Adapter is built against
  the contract before Liftosaur is moved behind it. Anything Hevy cannot express becomes a
  Fallback, not a special case.
- **`plan.py progress` disagrees with Liftosaur's native progression** for the same rule.
  Mitigation: during live verification, record one `playground` response with
  `finish_workout()` as a fixture. An offline test then runs `plan.py progress` on the same
  logged sets and compares it against the recorded `updatedProgramText`. On Liftosaur the native
  run is the one applied, with `progress` reporting it as "run by liftosaur".
- **Hevy's API has changed since September.** Mitigation: live re-verification before relying
  on any quirk, recorded with a date.
- **Canonical names that Hevy titles do not map cleanly onto** (`Bicep Curl (Cable)` vs
  `Bicep Curl, Cable`). Mitigation: a shipped map built once from Hevy's catalogue, reviewed,
  with unmatched names listed rather than guessed.
- **Existing store regression.** Mitigation: the byte-identical requirement above, checked on
  the user's real store before merge.
