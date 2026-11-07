# Adding a Training App

kg-trainer reads and writes a Training App only through an **Adapter**: one module in `scripts/adapters/` that implements the **Adapter Contract**. Everything else (analysis, check-ins, the dashboard, plan authoring) talks to the contract and never to an app. Terms in bold are defined in `GLOSSARY.md`; the decision record is `docs/adr/0001-adapter-contract-over-single-app.md`.

**Adapters are built in the skill repo, never in a user's store.** A store holds data, not code; nothing in a store or the environment can add an Adapter. An agent may scaffold one from an app's API docs, but the result is reviewed code with fixtures and tests, not something improvised in a session.

## The contract

Each module declares `NAME` (its module name, e.g. `acme`) and `CAPABILITIES` (the set of operations it implements), and defines those operations. Only the first two are required; each missing one has a **Fallback** the CLI prints (`scripts/adapters/contract.py` holds the table in `FALLBACKS`).

| Operation | Required | Returns | Fallback when absent |
|---|---|---|---|
| `ping()` | yes | `{"account": str, "units": "kg"\|"lb"}` | none |
| `pull_history()` | yes | the full Workout Log, newest first, already translated | none |
| `pull_bodyweight()` | no | `[(date, kg)]`, one per day, the latest reading of the day | `checkin.py log-weight` |
| `find_exercise(pattern)` | no | `[{"canonical": str, "app_id": str\|None}]` | the shipped canonical list only |
| `compile(plan, week=None)` | no | the app's payload for a Canonical Plan; local, no network | `meso-NN.md`, entered by hand |
| `push_plan(compiled, name)` | no | the app's id for the created plan | `meso-NN.md`, entered by hand |
| `update_plan(compiled, id)` | no | the app's id | push again, or edit by hand |
| `get_plan(id\|"current")` | no | the app's current plan payload | the Canonical Plan in `plans/` |
| `check(compiled)` | no | `{"ok": bool, "weekly_sets": {muscle: n}, "errors": [...]}` | local `plan.py validate`/`stats` only |

Declare exactly what you implement. `CAPABILITIES` is how the CLI knows what to refuse: a declared operation that is missing, or an implemented one that is not declared, fails the suite.

**Native progression (optional).** If the app runs Progression Rules itself (it moves the weights when the athlete finishes a workout), declare `NATIVE_PROGRESSION = True` (a bool; absent means False) and define `prescriptions(payload, week)`: from a `get_plan` payload, the working sets the app prescribes for one week as `[(day, exercise, [(reps, load_kg | None, amrap), ...])]` in program order, or `None` when the program has no such week. Ignore warmups, comments, timers, supersets and progress fields, and parse rather than compare text: apps reformat what you send. It needs `compile`, `update_plan` and `get_plan` declared. `app.py update` then fetches the program first and refuses, showing both sides, when the plan's `current_week` differs from the Canonical Plan; `--force` writes anyway after printing the difference. `plan.py progress` records the same change in the Canonical Plan, so a plan that is kept up with `progress` does not drift. The suite checks the flag is a bool, that `prescriptions(compile(plan))` shows no drift against the plan in every week, and that a changed rep count shows as drift (`contract.drift`, `contract.plan_prescriptions`).

Rules every Adapter follows:

- It is the only code that knows its app's URLs, payloads, pagination and quirks. Expose the base URL as `BASE` (the boundary test uses it to keep that URL out of every other module).
- All HTTP goes through `scripts/adapters/http.py` (`request`, `check`, `api_key`, `header_auth`/`bearer`, `key_source`). It refuses redirects, resolves the key (`$<APP>_API_KEY`, then the nearest `.env`, then `kg-trainer-data/<app>-key`) and reports failures as the operation and HTTP status, never the key. Do not open connections any other way.
- `pull_history` reads everything and the caller replaces the cache wholesale. Pagination that does not end cleanly fails loudly (`sys.exit`); it never returns a partial list.
- Weights leave the Adapter in kilograms; pound accounts are converted on read.
- A logged workout keeps today's `workouts.jsonl` shape (see `tests/contract_fixtures/hevy.py` for a small example, or `scripts/liftohistory.py`). `title` holds the Canonical Exercise Name. A title the Adapter cannot map is kept as the app wrote it so it lands in `unclassified_exercises`; it is never fuzzy-matched. `rpe` is only trusted when `rpe_logged` is true: a prescribed target never has it.
- A failed call exits non-zero with a message naming the operation and the HTTP status. Never swallow an error and return an empty or partial result.
- A Native Rule written for another app makes `compile` fail, naming the exercise.
- Writes never delete (most apps cannot undo): `app.py` asks before `push` and `update`.

## Where things go

| What | Where |
|---|---|
| The Adapter | `scripts/adapters/<app>.py` |
| Fixture provider (recorded responses) | `tests/contract_fixtures/<app>.py` |
| Reference doc: Capabilities, quirks, verification status | `references/adapters/<app>/README.md` |
| Optional: exercise map, vendored app docs | `references/adapters/<app>/` |
| App-specific tests (anything beyond the contract) | `tests/test_<app>*.py` |

## Scaffolding from the app's API docs

1. **Read the docs and write down the facts first**: base URL, auth header, pagination style and page-size cap, units, how a workout and a set are shaped, whether the app has weeks, programs or routines, whether it has any server-side check, and whether it has DELETE. Put them in `references/adapters/<app>/README.md` as you go, marked unverified until you have run them.
2. **Start the module** with `NAME`, `BASE`, a `CAPABILITIES` set naming only `ping` and `pull_history`, and those two functions built on `apphttp.request` and `apphttp.check`. Build reads first.
3. **Translate on the way out.** Map app exercise names to Canonical Exercise Names (`references/exercises.txt` and `references/exercise-muscles.json` are the vocabulary), convert to kg, turn timestamps into UTC `YYYY-MM-DDTHH:MM:SSZ`, sort newest first, and set `rpe_logged` only for RPE the athlete entered.
4. **Write the fixture provider** (below) from real or documented responses, then run the suite and work through what fails. Each message says what to change.
5. **Add one operation at a time** (`pull_bodyweight`, `find_exercise`, then the plan operations), adding each to `CAPABILITIES` as it lands. An app that cannot do something is not a special case: leave it out and let the Fallback apply.
6. **Write the reference doc** (below) and run everything.

## The fixture provider

`tests/contract_fixtures/<app>.py` is a plain module. The suite is adapter-agnostic; the provider is the only place that knows the app's routes.

```python
EXPECT = {                      # what your fixtures contain
    "history": {
        "count": 3,                         # workouts across ALL pages
        "unclassified": {"My Custom Move"}, # titles that are not Canonical Exercise Names
        "weights_kg": [("2026-10-02T10:00:00Z", "Bench Press", 1, 80.0)],
                                            # (start_time, title, set index, kg): include
                                            # one converted from pounds if the app has them
        "rpe": [("2026-10-02T10:00:00Z", "Bench Press", 1, True, 8.5),   # logged
                ("2026-10-02T10:00:00Z", "Bench Press", 2, False, None)],  # not logged
    },
    "bodyweight": {"2026-10-03": 80.9},     # day -> kg; include a day with two readings
    "find": ("curl", "Bicep Curl, Cable"),  # a pattern and a canonical name it must find
    "plan_id": "123",                       # an id update_plan/get_plan can use
}
PLAN = {...}                    # a Canonical Plan (see tests/fixtures/hevy_plan.json); only
                                # needed if you declare a plan operation
def setup(store): ...           # optional: write a cache file the Adapter reads into the store
def respond(method, path, query, body):
    """(status, json body) for a request, or None if the route is not recorded."""
```

`path` is the URL path as sent (include any `/api/v1` prefix the app uses); `query` is a dict of the first value of each parameter. Return what the real API returns, status and envelope included, and add a multi-page case if the app paginates. Reuse files in `tests/fixtures/` where you can, and import the provider's data from your own tests instead of copying it (see `tests/test_adapter_hevy.py`).

## Run the suite

```
python -m pytest -q tests/test_contract.py
```

It discovers your Adapter from `scripts/adapters/` with no edit anywhere, and it fails with a message saying what is wrong. Never edit `tests/test_contract.py` to make an Adapter pass; if the suite is wrong, fix it for every Adapter in a separate change.

What it checks, for every Adapter on its own fixtures: the required operations exist; `CAPABILITIES` exactly matches the contract operations the module implements; each signature; the Workout Log shape (fields, newest first, weights in kg, canonical or unclassified titles, logged-only RPE) and that the report can be built from it; bodyweight one reading per day; `compile` makes no network call, is deterministic and leaves the plan untouched; another app's Native Rule is refused; push, update and get round-trip and return ids; `check` returns `ok`, `weekly_sets` and `errors`; every request is HTTPS and carries the key; and under 401, 500, refused-redirect and network failures every call exits loudly naming the status and no output ever contains the key (the suite injects a sentinel key). Undeclared operations print their Fallback and exit non-zero through `app.py`. No test opens a socket.

To try the suite on a throwaway Adapter without adding one to the repo, put `adapters/<name>.py` and `fixtures/<name>.py` (the provider) under any directory and run `KG_CONTRACT_TEST_EXTRA_ROOT=<dir> python -m pytest -q tests/test_contract.py`. This seam exists only in the test file and in `contract.add_adapter_dir`; nothing at runtime reads it.

## The reference doc

`references/adapters/<app>/README.md` states:

1. The module, auth and where the key comes from.
2. **Capabilities**: which operations are declared, how each maps onto the app, and which Fallbacks apply.
3. **Quirks**: anything the next maintainer would otherwise rediscover (response wrappers, fields the API rejects, limits, naming).
4. **Verification status with dates**: what was run against the live API and when, what is fixture-only, and what is **unverified** (and why). Unverified is a legitimate status; claiming verification that did not happen is not. Re-record the date whenever you re-check.

`references/adapters/liftosaur/README.md` and `references/adapters/hevy/README.md` are the models: Liftosaur is live-verified for reads, `compile`, `check` and `playground`; Hevy is fully unverified because there was no Pro key.

## Done means

- `python -m pytest -q` passes, including `tests/test_contract.py` **unchanged**, with the new Adapter in the run.
- The fixture provider exists and its data is recorded or documented real shapes, not invented to satisfy the suite.
- The reference doc exists with Capabilities and a dated verification status.
- Live verification, if you have a key, is run read-only first and recorded; writes to a real account are done once, by hand, with the user's go-ahead, and recorded.
- Nothing outside `scripts/adapters/` imports the module or contains the app's base URL (the boundary test checks this on its own).
