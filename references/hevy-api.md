# Hevy Public API reference

Verified against the live OpenAPI spec served at `https://api.hevyapp.com/docs/` (extracted spec kept alongside this file as `hevy-openapi.json` — read that for exact field-level detail).

**Base URL:** `https://api.hevyapp.com`
**Auth:** `api-key: <key>` request header on every endpoint. The key is a UUID. Users get it at <https://hevy.com/settings?developer> and the API requires **Hevy Pro**.
**Units:** every weight is kilograms (`weight_kg`), every distance is meters. There is no imperial option — convert at the presentation layer only.

## Endpoint map

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/user/info` | `{data:{id,name,url}}`. Use as the key-validation ping. |
| GET | `/v1/workouts` | Paginated logged workouts, newest first. `page`, `pageSize` (**max 10**). |
| GET | `/v1/workouts/count` | `{workout_count}` — cheap total. |
| GET | `/v1/workouts/events` | Changes since a timestamp. `since` (ISO 8601), `page`, `pageSize` (max 10). Events are `{type:"updated",workout}` or `{type:"deleted",id,deleted_at}`. |
| GET | `/v1/workouts/{workoutId}` | One workout. |
| POST | `/v1/workouts` | Create a logged workout (201). |
| PUT | `/v1/workouts/{workoutId}` | Replace a logged workout. |
| GET | `/v1/routines` | Paginated routines. `pageSize` **max 10**. |
| POST | `/v1/routines` | Create routine (201). **Can return 403.** |
| GET | `/v1/routines/{routineId}` | One routine → `{routine:{...}}`. |
| PUT | `/v1/routines/{routineId}` | Replace routine (200) → `{routine:[{...}]}` — array, see gotcha 4. |
| GET | `/v1/routine_folders` | Paginated folders. `pageSize` max 10. |
| POST | `/v1/routine_folders` | Create folder (201) → `{routine_folder:{id,index,title,...}}`. |
| GET | `/v1/routine_folders/{folderId}` | One folder. |
| GET | `/v1/exercise_templates` | The exercise catalog. `pageSize` **max 100**. |
| GET | `/v1/exercise_templates/{id}` | One template. |
| POST | `/v1/exercise_templates` | Create a custom exercise → `{id}`. Can return 403. |
| GET | `/v1/exercise_history/{exerciseTemplateId}` | Every logged set for one exercise. Optional `start_date`, `end_date` (ISO 8601). |
| GET | `/v1/body_measurements` | Paginated measurements. `pageSize` max 10. |
| GET | `/v1/body_measurements/{date}` | One date, `YYYY-MM-DD`. |
| POST | `/v1/body_measurements` | Create. **409 if that date already exists.** |
| PUT | `/v1/body_measurements/{date}` | Update an existing date. |

There is no DELETE anywhere in the API, and no endpoint for nutrition, sleep, or cardio-outside-Hevy. Anything the API cannot store lives in local state instead.

## Gotchas that bite

1. **`pageSize` is capped at 10** on workouts, routines, folders and body measurements — only `exercise_templates` allows 100. Paginate with `page_count` from the response; do not assume one call returns everything.
2. **`POST /v1/routines` returns 403** when the account cannot create more routines. Treat 403 as "stop and tell the user", not as retryable.
3. **`POST /v1/body_measurements` returns 409** when a measurement already exists for that date. On 409, `PUT /v1/body_measurements/{date}` instead.
4. **Every routine response is wrapped, and PUT's wrapper holds an array.** Verified against the live API — the spec is wrong here:

   | Call | Actual shape |
   |---|---|
   | `POST /v1/routines` | `{"routine": { ... }}` |
   | `PUT /v1/routines/{id}` | `{"routine": [ { ... } ]}` — single-element **array** |
   | `GET /v1/routines/{id}` | `{"routine": { ... }}` |

   The spec documents POST and PUT as returning a bare `Routine`. Parsing one shape for all three yields `undefined` on two of them, and the PUT path is the one a weekly routine update runs through.

5. **A routine's own `notes` field is write-only at best.** `POST` and `PUT` accept it and return 201/200, but no routine response ever contains a `notes` key — not for a routine just created with one, and not for routines created in the app. Anything the client has to read belongs in per-exercise `notes` (those persist and come back reliably) or in the markdown plan.

6. **`POST /v1/routine_folders` is wrapped too:** `{"routine_folder": {"id": 3670546, ...}}`. The numeric id you need for `folder_id` is at `.routine_folder.id`.
7. **A routine set has no `rpe` field.** `rpe` exists only on *logged* sets (workouts, exercise history). RIR/RPE *targets* must go in the exercise `notes` string.
8. **`exercise_template_id` values are opaque 8-char hex strings** (`"D04AC939"`, `"79D0BB3A"`). They are not guessable and not derivable from a name. A bad one is rejected with `400 Found invalid exercise template id`. Fetch the catalog and resolve every id; never invent one.
9. `rest_seconds` exists on routine exercises only, not on workout exercises.
10. **A routine has no week dimension.** It is one static session, so a mesocycle whose RIR target or set count changes week to week cannot be expressed in a single routine. Either `PUT` the routine at the start of each week, or create one routine per distinct week and keep the week-to-week ramp in the markdown plan. Say which of the two you did, so the client knows whether to expect their Hevy routine to change under them.
11. The spec has no `servers` block; the base URL comes from Hevy's docs. `hevy.py ping` is the cheap way to confirm it and the key together before doing anything else.

## Request bodies

### Create routine

```jsonc
// POST /v1/routines   header: api-key
{
  "routine": {
    "title": "Upper A — Week 1-4",
    "folder_id": 42,          // null = default "My Routines" folder
    // no routine-level "notes" — it is accepted and then never returned (gotcha 5)
    "exercises": [
      {
        "exercise_template_id": "05293BCA",
        "superset_id": null,   // same integer on 2+ exercises groups them
        "rest_seconds": 180,
        "notes": "Top set @ 2 RIR, then backoff sets.",
        "sets": [
          { "type": "warmup", "weight_kg": 40, "reps": 8 },
          { "type": "normal", "weight_kg": 80, "rep_range": { "start": 6, "end": 8 } },
          { "type": "normal", "weight_kg": 80, "rep_range": { "start": 6, "end": 8 } }
        ]
      }
    ]
  }
}
```

Set `type`: `warmup` | `normal` | `failure` | `dropset`.
Set fields: `weight_kg`, `reps`, `rep_range:{start,end}`, `distance_meters`, `duration_seconds`, `custom_metric` (steps/floors). All nullable — send only the ones the exercise type uses.

**`rep_range` is the right tool for hypertrophy programming.** Prefer it over a fixed `reps` whenever the prescription is a range.

### Create routine folder

```jsonc
// POST /v1/routine_folders
{ "routine_folder": { "title": "Meso 1 — Upper/Lower 🏋️" } }
```

Create the folder first, then pass `.routine_folder.id` (a number) as `folder_id` on each routine.

### Create body measurement

```jsonc
// POST /v1/body_measurements   (PUT /v1/body_measurements/2026-09-18 to update)
{ "date": "2026-09-18", "weight_kg": 81.7, "fat_percent": 15.2, "waist": 80 }
```

Other optional numeric fields: `lean_mass_kg`, `neck_cm`, `shoulder_cm`, `chest_cm`, `left_bicep_cm`, `right_bicep_cm`, `left_forearm_cm`, `right_forearm_cm`, `abdomen`, `waist`, `hips`, `left_thigh`, `right_thigh`, `left_calf`, `right_calf`. Note the inconsistent naming — some carry a `_cm` suffix and some do not; copy them literally.

### Create custom exercise

```jsonc
// POST /v1/exercise_templates
{ "exercise": {
    "title": "Cable Y-Raise",
    "exercise_type": "weight_reps",
    "equipment_category": "machine",
    "muscle_group": "shoulders",
    "other_muscles": ["traps", "upper_back"]
} }
```

`exercise_type`: `weight_reps` | `reps_only` | `bodyweight_reps` | `bodyweight_assisted_reps` | `duration` | `weight_duration` | `distance_duration` | `short_distance_weight`
`equipment_category`: `none` | `barbell` | `dumbbell` | `kettlebell` | `machine` | `plate` | `resistance_band` | `suspension` | `other`
`muscle_group`: `abdominals` `shoulders` `biceps` `triceps` `forearms` `quadriceps` `hamstrings` `calves` `glutes` `abductors` `adductors` `lats` `upper_back` `traps` `lower_back` `chest` `cardio` `neck` `full_body` `other`

Only create a custom exercise after searching the catalog and finding nothing usable.

## Response shapes worth knowing

**Logged set** (in `Workout.exercises[].sets[]` and exercise history): `index`, `type`, `weight_kg`, `reps`, `distance_meters`, `duration_seconds`, `rpe`, `custom_metric` — all nullable except `index`/`type`.

**`ExerciseHistoryEntry`** is one flat record per set: `workout_id`, `workout_title`, `workout_start_time`, `workout_end_time`, `exercise_template_id`, `weight_kg`, `reps`, `rpe`, `set_type`, plus distance/duration/custom metric. This is the cheapest way to trend a single lift over months — one call, date-filterable, no pagination.

**`ExerciseTemplate`**: `id`, `title`, `type`, `primary_muscle_group`, `secondary_muscle_groups[]`, `equipment`, `is_custom`. The catalog is ~490 templates, so one `pageSize=100` pass is 5 calls.

**Body measurements carry two fields the spec omits:** each record also comes back with a numeric `id` and a `created_at` timestamp. `created_at` is when the row was *entered*, which can be long after the `date` it describes — a client back-filling a month of weigh-ins in one sitting gives them all the same `created_at`. Trend off `date`, never off `created_at`.

## Working calls

Validate a key:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H "api-key: $HEVY_API_KEY" \
  https://api.hevyapp.com/v1/user/info
```

`200` = good. `401`/`403` = bad key or no Pro subscription.

Cache the whole exercise catalog (needed before any routine write):

```bash
page=1; : > ~/.kg-trainer/exercise-templates.jsonl
while :; do
  r=$(curl -s -H "api-key: $HEVY_API_KEY" \
    "https://api.hevyapp.com/v1/exercise_templates?page=$page&pageSize=100")
  echo "$r" | jq -c '.exercise_templates[]' >> ~/.kg-trainer/exercise-templates.jsonl
  [ "$page" -ge "$(echo "$r" | jq '.page_count')" ] && break
  page=$((page+1))
done
```

Then resolve a name to an id:

```bash
jq -r 'select(.title|test("Bench Press";"i"))|[.id,.title,.equipment]|@tsv' \
  ~/.kg-trainer/exercise-templates.jsonl
```

Pull recent workouts (remember the cap of 10 per page):

```bash
curl -s -H "api-key: $HEVY_API_KEY" \
  "https://api.hevyapp.com/v1/workouts?page=1&pageSize=10" | jq '.page_count, .workouts[].title'
```
