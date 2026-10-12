# Liftosaur API reference

Runtime reference: read this before making a Liftosaur call.

## Basics

Base URL `https://www.liftosaur.com/api/v1`. Auth is `Authorization: Bearer lftsk_...`.
Success responses wrap the payload as `{"data": ...}`; errors return
`{"error": {code, message, details}}`.

Status codes that matter:

- **401** — no key, a bad key, or an inactive subscription.
- **422** — invalid Liftoscript on a program write.
- **409** — a measurement write whose timestamp already exists.
- **400** — a write to a read-only health key (see below).

## Endpoints this skill uses

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

## Pagination

On `/history` and `/measurements/:key`: default `limit` 50, maximum **200**. Pass
`nextCursor` back as `cursor` while `hasMore` is true.

## Measurements

Bodyweight is the measurement key `weight`; body fat is `bodyfat`. The health keys `sleep`,
`calories` and `protein` are **read-only** through the API and return 400 on write.

## Live workout endpoints are not used

Writes to a live workout session (`/workout/start`, `/workout/set`, `/workout/finish`) also
require `X-Liftosaur-Device-Id` and `X-Liftosaur-Client` headers. This skill does not drive
live sessions and does not use the `/workout/*` endpoints.

## Liftohistory — the workout record format

`GET /history` returns each record's `text` in Liftohistory, a line-oriented text format, not
structured JSON. The full reference is vendored in `liftohistory.md`.

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
- Warmups are their own section, so warmup exclusion is a structural test, not a per-set one.

## Exercise names

Liftosaur identifies exercises by name with an optional equipment suffix — `Bench Press`,
`Bench Press, Dumbbell`, `Bicep Curl, Cable`. There are no opaque template ids. The built-in
list is `exercises.txt`; the name-to-muscle map used by the balance table is
`exercise-muscles.json`.
