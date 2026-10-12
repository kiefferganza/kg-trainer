# Spec — Migrate kg-trainer from Hevy to Liftosaur

**Date:** 2026-10-11
**Status:** Approved for planning

## Why

The kg-trainer skill is built on the Hevy public API. Hevy's routine model has no week
dimension and no progression logic: a routine is one static session, so a ten-week
mesocycle has to be expressed as four static routines plus a markdown note telling the
client that someone will hand-edit their loads at week six. Every progression decision has
to be re-derived by the coach and pushed by hand.

Liftosaur expresses the same program as a single Liftoscript document with weeks, days and
native progression functions — `progress: lp(5lb)`, `dp(5lb, 8, 12)`, `sum(30, 5lb)`, and
`custom(...) {~ ~}` for anything else. The progression is part of the program, so it runs
without the coach in the loop. That is the reason for the migration.

Liftosaur also offers two things Hevy had no equivalent of:

- `POST /api/v1/playground` simulates a program to check its logic before it is committed.
- `POST /api/v1/program-stats` returns per-day session content and weekly set totals per
  muscle group, computed server-side from the program text.

Together those replace hand-counted volume tables and unverified pushes with machine checks.

## Decisions

These were settled with the user on 2026-10-11 and are not open for re-litigation during
execution.

1. **Premium access is available.** The user is subscribed. Both the REST API and the MCP
   server require a premium subscription; an API key is created in the app under
   Settings → API Keys and looks like `lftsk_...`.
2. **Transport is the REST API, via a local client script.** Not the official MCP server.
   The skill's core discipline is that scripts compute the numbers over a local cache, so a
   334-workout history never enters the model's context and every figure is reproducible by
   re-running a command. An MCP transport would return bulk history as tool results into
   context and replace script-computed math with the model reading numbers off a page. The
   MCP server's **reference tools need no authentication**, so they are used at build time to
   vendor documentation into `references/`, and are not a runtime dependency.
3. **Hard replace.** Hevy support is removed entirely — `scripts/hevy.py`,
   `references/hevy-api.md` and `references/hevy-openapi.json` are deleted. There is no
   provider abstraction and no dual-provider mode.
4. **The user migrates their own history.** The user will import their 334 Hevy workouts
   into Liftosaur themselves. The skill ships no backfill tool and no reader for the old
   `data/workouts.jsonl` cache. After their import, Liftosaur is the single source of truth
   and the skill pulls everything from it.

## Liftosaur API facts

Base URL `https://www.liftosaur.com/api/v1`. Auth is `Authorization: Bearer lftsk_...`.
Success responses wrap the payload as `{"data": ...}`; errors return
`{"error": {code, message, details}}`. A request with no key returns 401. Invalid Liftoscript
on a program write returns 422.

Endpoints this skill uses:

| Method | Path | Purpose |
|---|---|---|
| GET | `/programs` | List programs — id, name, isCurrent |
| GET | `/programs/:id` | One program; `:id` may be `current`. Returns Liftoscript `text` |
| POST | `/programs` | Create from `{name, text}` |
| PUT | `/programs/:id` | Replace `text`, optionally `name` |
| GET | `/history` | Workout records. Query `startDate`, `endDate`, `limit`, `cursor`. Returns `records[]` of `{id, text}`, plus `hasMore`, `nextCursor` |
| GET | `/history/:id` | One record |
| POST | `/playground` | `{programText, day?, week?, commands[]?}` → resolved workout text |
| POST | `/program-stats` | `{programText}` → `days[]`, weekly set totals, `muscleGroups[]` |
| GET | `/measurements` | Overview: key, category, count, latest |
| GET | `/measurements/:key` | Series for one key. Query `limit`, `cursor` |
| POST | `/measurements/:key` | `{value, timestamp?}`. 409 if that timestamp exists |
| GET | `/settings` | `units`, timers |

Pagination on `/history` and `/measurements/:key`: default limit 50, maximum 200. Pass
`nextCursor` back as `cursor` while `hasMore` is true. This is a real improvement over Hevy,
whose `pageSize` capped at 10 and made a full pull one call per ten workouts.

Bodyweight is the measurement key `weight`; body fat is `bodyfat`. The health keys `sleep`,
`calories` and `protein` are read-only through the API and return 400 on write.

Writes to a live workout session (`/workout/start`, `/workout/set`, `/workout/finish`) also
require `X-Liftosaur-Device-Id` and `X-Liftosaur-Client` headers. This skill does not drive
live sessions and does not use those endpoints.

## Liftohistory — the workout record format

`GET /history` returns each record's `text` in Liftohistory, a line-oriented text format, not
structured JSON. This is the single largest piece of work in the migration: a parser.

The format, from the authoritative reference:

```
// Optional workout note
2026-02-28T10:45:30Z / program: "5/3/1 For Beginners" / dayName: "Push Day" / week: 1 / dayInWeek: 5 / duration: 1235s / exercises: {
  // Optional exercise note
  Bench Press, Barbell / 3x8 185lb @7, 1x6 185lb @9 / warmup: 1x10 95lb, 1x5 135lb / target: 3x8-12 185lb @8 90s
  OHP / 3x10 95lb / target: 3x10 95lb 60s
  Pull Ups / 3x8|7 0lb / target: 3x10 0lb 60s
  Wall Sit / 2x1 0kg 60s, 1x1 0kg 52s / target: 3x1 0kg 60s|45s
}
```

Rules that matter to the parser:

- The header's date is ISO 8601. Every metadata field except the `exercises` block is optional.
- An exercise line is `ExerciseName[, Equipment] / completed sets / warmup: sets / target: sets`.
  Sections are separated by `/` and the `warmup:` and `target:` sections are optional.
- Set groups within a section are comma-separated. **One group expands to several sets:**
  `3x8 185lb` is three sets of eight reps, not one set.
- Units are always explicit, `lb` or `kg`. The skill works in kilograms internally, so pounds
  must be converted (1 lb = 0.45359237 kg).
- `@7` is an RPE. `@8+` is an RPE the user logged rather than one that was prescribed.
- A `+` on reps is AMRAP: `1x5+` is five-or-more.
- `3x8|7` is a unilateral set — eight reps right, seven left.
- In a *completed* set a trailing duration is how long the set was held: `1x1 0kg 52s`.
  In a *target* set the same position is the rest timer, and `60s|45s` means hold 60s then
  rest 45s. The two meanings must not be conflated.
- `//` at the start of a line is a workout note or an exercise note.
- Records are separated by blank lines and each closes with `}` on its own line.

Warmups being their own section is a structural improvement over Hevy, where every set
carried a `type` field and warmup exclusion was a per-set test.

## Exercise names and muscle groups

Liftosaur identifies exercises by name with an optional equipment suffix — `Bench Press`,
`Bench Press, Dumbbell`, `Bicep Curl, Cable`. There are no opaque template ids. The whole
Hevy workflow of resolving a name to an `exercise_template_id`, caching a catalog and
refusing invented ids disappears; `find` becomes a name match against the built-in list.

The built-in list is available from the MCP's no-auth `list_exercises` tool, but **it returns
names only, with no muscle-group data**. The existing balance table keys off a primary muscle
group per exercise, so the migration needs a muscle source:

- For **planned** volume, `POST /program-stats` returns `muscleGroups[]` computed by
  Liftosaur from the program text. This is authoritative and replaces hand-counting sets
  when designing a block.
- For **logged** volume from history, the skill needs a local name-to-muscle map, vendored at
  `references/exercise-muscles.json` and seeded from the `free-exercise-db.json` already on
  disk plus manual entries for anything unmatched.

## Requirements

### Functional

- `liftosaur.py ping` validates the key and reports the account's unit setting.
- `liftosaur.py exercises [pattern]` matches against the vendored built-in exercise list.
- `analyze.py pull [--full]` pulls history through the cursor API into the local cache,
  parsing Liftohistory into the internal shape. Incremental pulls use `startDate`.
- `analyze.py report` and `deep-dive` produce the same tables as today, from Liftosaur data.
- `liftosaur.py program-get`, `program-push`, `program-stats` and `playground` cover program
  read, write and verification.
- `checkin.py import-liftosaur` replaces `import-hevy`, reading `GET /measurements/weight`.
- `dashboard.py build` works against Liftosaur programs.
- A program push is verified by `playground` and `program-stats` before it is reported as done.

### Non-functional

- **The API key is never written to a plan file, a commit, a dashboard, or stdout.** It is
  read from `$LIFTOSAUR_API_KEY` first, then from a `chmod 600` key file in the state
  directory, and only written there if the user asks. When a key fails, report the HTTP
  status, never the key.
- The dashboard output contains no credentials, as today.
- Every figure the coach reports traces to a logged set, a script, or the user's own words.
  That rule is unchanged by the migration and the scripts remain the only source of math.
- `analyze.py`'s internal workout dict shape is preserved, so the parser is an adapter and
  the analysis, dashboard and check-in code change as little as possible.

### Out of scope

- Driving live workout sessions (`/workout/*`).
- Gyms and equipment endpoints.
- Writing history records (`POST /history`) — the user imports their own history.
- Any Hevy compatibility shim.

## Global constraints

- Python 3.11+, standard library only for the client and parser. No new runtime dependencies.
- `pytest` is a development dependency only.
- 1 lb = 0.45359237 kg exactly. Loads are reported in kilograms, rounded to two decimals
  internally and to a loadable increment when prescribed.
- Base URL `https://www.liftosaur.com/api/v1`; auth header `Authorization: Bearer lftsk_...`.
- Measurement series and history both paginate with `limit` (max 200) and `cursor`.
- The state directory resolution in `scripts/paths.py` is unchanged.

## Risks

- **The parser is the main risk.** The format is documented but the user's real records are
  the only proof. Task 1 captures real records as fixtures before the parser is trusted, and
  Task 8 re-verifies against the live account.
- **RPE may not survive the user's own Hevy import.** The current report includes average
  top-set RPE across 194 sets. If the import drops `@` annotations, that table goes empty.
  The report already degrades to "client does not log RPE" when no RPE is present, so this
  fails soft, but it must be checked in Task 8 and reported to the user rather than hidden.
- **Unit mismatch.** If the account is set to pounds, every number the coach sees must still
  be converted consistently. `ping` surfaces the account unit so this is caught immediately.
