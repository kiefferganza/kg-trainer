# Local state layout

Everything lives under one `kg-trainer-data/` directory, resolved once by `scripts/paths.py` (run it on its own to print the answer):

1. `$KG_TRAINER_HOME`, if set (absolute or `~`-relative).
2. Otherwise walk up from the working directory and stop at the first directory that either already contains `kg-trainer-data/` or is a git root; the store is `kg-trainer-data/` there.
3. Otherwise `kg-trainer-data/` in the working directory.

So the store follows the project, not the machine, and running from a subdirectory reuses the project's store instead of starting an empty one. The scripts exit rather than resolve to a store inside the skill's own directory — only an explicit `$KG_TRAINER_HOME` can put it there.

Inside a git repo the store must be ignored: it holds bodyweight history, plans and possibly the `hevy-key` file. The scripts print a warning on stderr when it is not; add `kg-trainer-data/` to that repo's `.gitignore` before writing anything.

```
kg-trainer-data/
  profile.json               intake answers + current targets
  hevy-key                   optional, chmod 600 — only if the user asks to save it
  exercise-templates.jsonl   cached Hevy catalog (scripts/hevy.py catalog)
  weight-log.csv             date,weight_kg,fat_percent,waist_cm
  checkins.jsonl             one append-only record per weekly check-in
  data/
    workouts.jsonl           every logged workout (scripts/analyze.py pull)
    measurements.json        body measurements, merged on date
    sync.json                {"last_pull": ISO} — drives incremental pulls
    free-exercise-db.json    cached photo catalog for the dashboard
  plans/
    meso-01.md               the human-readable program
    meso-01-routines/        the exact JSON pushed to Hevy, one file per session
  dashboard/
    index.html  data.js  img/    generated; opens from disk, contains no API key
```

`data/` is a cache and can be rebuilt with `analyze.py pull --full`. `profile.json`, `weight-log.csv`, `checkins.jsonl` and `plans/` are the irreplaceable part — they are the only record of what was decided and why.

## profile.json

```jsonc
{
  "name": "Kieffer",
  "age": 28,
  "sex": "male",                    // needed for BMR; ask, never infer from the name
  "height_cm": 178,
  "weight_kg": 82.0,
  "discipline": "bodybuilding",     // powerlifting | bodybuilding | endurance | hybrid
  "experience": "3 years consistent",
  "sessions_per_week": 4,
  "session_minutes": 70,
  "goal": "build muscle, slight fat loss",
  "goal_rate_kg_per_week": -0.15,   // signed: negative loses, positive gains
  "equipment": "full commercial gym",
  "injuries": [
    { "site": "right shoulder", "note": "impingement on flat barbell pressing",
      "avoid": ["flat barbell bench"], "prefer": ["low-incline DB press", "neutral-grip"] }
  ],
  "daily_activity": "desk job, ~7k steps",   // drives the TDEE multiplier
  "current_lifts": { "squat": "120x6", "bench": "80x8", "deadlift": "150x5" },
  "hevy": { "connected": true, "key_source": "env", "folder_id": 42 },
  "plan_output": ["markdown", "hevy"],
  "targets": { "calories": 2800, "protein_g": 175, "carbs_g": 355, "fat_g": 75,
               "set_on": "2026-09-18", "basis": "observed_tdee" },
  "created": "2026-09-18",
  "updated": "2026-09-18",
  "last_checkin": "2026-09-18"
}
```

Write it at the end of Phase 1 and update `targets`/`weight_kg`/`updated`/`last_checkin` at every check-in. `weight_kg` holds the current 7-day average, not the most recent single weigh-in — the raw readings live in `weight-log.csv`. `key_source` records *where* the key came from (`env` or `file`) — never store the key value in `profile.json`.

## checkins.jsonl

One JSON object per line, appended by `scripts/checkin.py record`:

```jsonc
{"date":"2026-09-18","avg_weight_kg":81.83,"prev_avg_weight_kg":82.14,
 "rate_kg_per_week":-0.314,"calories":2800,
 "macros_g":{"protein":175,"carbs":355,"fat":75},
 "sessions_completed":3,"sessions_planned":4,
 "notes":"bench RPE 8->9.5 at same load; backed off to 72.5"}
```

Append only. Never rewrite a past record — a wrong past decision is data about the client, and overwriting it destroys the trend the whole check-in loop depends on.

## API key handling

Read the key from `$HEVY_API_KEY` first. Only write `kg-trainer-data/hevy-key` if the user explicitly asks to save it, and then `chmod 600` it immediately and confirm `kg-trainer-data/` is gitignored — the store now sits inside the working directory, so an unignored key file is one `git add .` away from a commit. Never echo the key back, never paste it into a plan file, a markdown document, a commit, or an external app. When a key fails, report the HTTP status, not the key.
