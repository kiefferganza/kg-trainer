# kg-trainer

An AI personal trainer skill for Claude Code. It builds a training program from your own logged
training data — powerlifting, bodybuilding, endurance or hybrid — pushes the routines into the
Hevy app, runs weekly check-ins against your weigh-ins and workout logs, and generates a static
dashboard of your history.

It is built on two rules: **never invent a number**, and **every step writes state to disk** so
next week is a comparison rather than a cold start.

---

## Requirements

| | |
|---|---|
| Python | 3.9 or newer, standard library only — no `pip install` |
| Hevy | Optional but recommended. The public API requires a **Hevy Pro** subscription |
| Network | Only for the Hevy API and, for dashboard photos, the free-exercise-db CDN |

Everything works without Hevy; you supply your numbers by hand and the skill labels them as
self-reported rather than logged.

---

## Setup

### 1. Install the skill

Personal skills live in `~/.claude/skills/`. Symlink this directory so edits stay live:

```bash
ln -sfn "$PWD" ~/.claude/skills/kg-trainer
```

Or copy it if you would rather freeze a version:

```bash
cp -R "$PWD" ~/.claude/skills/kg-trainer
```

Start a new Claude Code session afterwards — skills are read at session start.

### 2. Get a Hevy API key

Go to <https://hevy.com/settings?developer> and copy the key. It is a UUID, and the endpoint
requires Hevy Pro.

### 3. Give the skill the key

Preferred — set it in your shell, so it never appears in a chat transcript:

```bash
export HEVY_API_KEY=your-key-here
```

To persist it across sessions instead, write it to a file the scripts read automatically:

```bash
mkdir -p ~/.kg-trainer && printf '%s' 'your-key-here' > ~/.kg-trainer/hevy-key
chmod 600 ~/.kg-trainer/hevy-key
```

`$HEVY_API_KEY` wins if both are present. The scripts never echo the key, and it never reaches a
plan file, the dashboard, or any external service.

### 4. Confirm it works

```bash
scripts/hevy.py ping
```

A JSON object with your Hevy display name means you are set. `401` means a bad key; `403` usually
means the account has no Pro subscription.

---

## First run

Just tell Claude what you want — "build me a training program" — and the skill takes over. To do
the data steps yourself first:

```bash
scripts/hevy.py catalog          # cache the ~490-exercise catalog (needed before any push)
scripts/analyze.py pull --full   # cache every logged workout and body measurement
scripts/analyze.py report        # the baseline tables
```

The full pull is slow by design: Hevy caps `pageSize` at 10, so it is one request per 10 workouts
(a 330-workout history takes roughly 75 seconds). You only do it once — after that
`scripts/analyze.py pull` is incremental via `/v1/workouts/events` and takes a few seconds.

### What the seven steps do

| Step | What happens | Ends with |
|---|---|---|
| 1 | Pulls your Hevy history and computes frequency, best sets, estimated 1RM trend, muscle balance, RPE | Tables on screen, cache in `~/.kg-trainer/data/` |
| 2 | Interviews you one short group of questions at a time, skipping whatever the data already answered | `profile.json` written |
| 3 | Tells you what your data actually says, including where it disagrees with you | **Stops and waits for you** |
| 4 | Designs a 6–12 week block, one routine per session, loads derived from your own estimated 1RMs | **Stops and waits for your approval** |
| 5 | Maps every exercise to a real Hevy template id, creates the folder, pushes each routine | Real routine ids reported back |
| 6 | Generates the dashboard | `~/.kg-trainer/dashboard/index.html` |
| 7 | Ongoing coaching via keywords | A check-in record per week |

Steps 3 and 4 stop on purpose. The plan you get is meant to survive your objections first.

---

## Day-to-day use

Say any of these to Claude:

| Say | What it does |
|---|---|
| `UPDATE` | Incremental pull of new or edited workouts, rebuilds the dashboard, lists what changed |
| `HOW AM I DOING` | Compares your logged sets against the routine targets; says which lifts are ready to go up |
| `PROGRESS IT` | Applies the progression rule and pushes new loads to Hevy, showing before/after first |
| `IT HURTS` | Asks where and how bad, then swaps the aggravating exercises and updates the routine |
| `NEXT BLOCK` | Re-runs the analysis, truth-telling, design and push using everything logged since |
| `check-in` | The weekly loop: weigh-in trend, training review, one adjustment, recorded |

### The weekly check-in

```bash
scripts/checkin.py import-hevy                  # or: log-weight 2026-09-18 81.7
scripts/checkin.py trend --calories 2650
```

`trend` returns a `verdict` you should read before changing anything:

- `OK` — the change is bigger than your day-to-day noise; act on it
- `WITHIN_NOISE` — smaller than the week's standard deviation; treat as flat
- `INSUFFICIENT_DATA` — fewer than 4 weigh-ins in a window; hold targets, retry with
  `--window 14` or `--window 30`
- `NO_DATA` — nothing logged yet

It also reports **observed TDEE**, which replaces the BMR formula once you have two clean windows:
`intake − (rate_kg_per_week × 7700 / 7)`. A formula is a guess about a person; two weeks of
weigh-ins is a measurement of you.

---

## Command reference

### `scripts/hevy.py` — Hevy API client

```
ping                              validate the key, print your account
catalog [--refresh]               cache the exercise catalog locally
find <regex>                      resolve an exercise name to a template id
workouts [--pages N]              recent logged workouts
history <template_id> [--since]   every logged set for one exercise
measurements [--pages N]          body measurements
put-measurement <date> [--weight --fat --waist]
folders / new-folder <title>      routine folders
routines [--pages N]              your routines
pull-routine <id> [--out FILE]    fetch a routine already stripped to PUT-safe shape
push-routine <file> [--id <id>]   create (POST) or replace (PUT) a routine
```

### `scripts/analyze.py` — history and baseline

```
pull [--full]        cache workouts + measurements; incremental unless --full
report [--json]      frequency, best sets, 1RM trend, muscle balance, RPE
deep-dive <pattern>  one lift, every logged set, ranked by estimated 1RM
```

### `scripts/checkin.py` — weight trend and check-in log

```
log-weight <date> <kg> [--fat --waist]
import-hevy                        pull weigh-ins from Hevy into the local log
trend [--window 7] [--calories N]  weekly averages, rate, noise, observed TDEE
record --calories --protein --carbs --fat [--sessions --planned --notes]
history [--last N]                 past check-ins
```

### `scripts/dashboard.py` — the board

```
build [--name NAME] [--goal TEXT] [--open] [--no-images]
```

Writes `~/.kg-trainer/dashboard/`. Opens straight from the filesystem — no server, and no API key
in the output. Sections: hero lift, stat tiles, plan tabs with exercise photos, a 52-week
consistency heatmap, estimated-1RM small multiples, sets per muscle, and personal records. Every
mark has a hover tooltip with the exact numbers.

Photos come from the public-domain [free-exercise-db](https://github.com/yuhonas/free-exercise-db);
each exercise has a start and finish frame that crossfade so a card reads as a rep in motion. An
exercise with no confident title match gets a clean text tile rather than a wrong photo — the
script prints the match rate so you can see how many.

---

## Where your data lives

```
~/.kg-trainer/
  profile.json               your intake answers and current targets
  hevy-key                   optional, chmod 600
  exercise-templates.jsonl   cached Hevy catalog
  weight-log.csv             date,weight_kg,fat_percent,waist_cm
  checkins.jsonl             append-only, one record per check-in
  data/                      cached workouts, measurements, sync timestamp
  plans/                     the markdown plans and the exact JSON pushed to Hevy
  dashboard/                 generated board
```

Set `$KG_TRAINER_HOME` to move all of it, which is also how you keep a test profile separate from
your real one.

`data/` is a rebuildable cache. `profile.json`, `weight-log.csv`, `checkins.jsonl` and `plans/` are
not — they are the only record of what was decided and why. Nothing here is ever committed to the
skill's own directory.

---

## Troubleshooting

**`401 bad API key`** — the key is wrong or expired. Re-copy it from
<https://hevy.com/settings?developer>.

**`403 forbidden`** — on `ping`, the account has no Hevy Pro. On `push-routine` or
`POST /exercise_templates`, you have hit the routine or custom-exercise limit; delete something in
the app or reuse a catalog exercise.

**`Refusing to push — these are not real exercise template ids`** — an id is not in your cached
catalog. Run `scripts/hevy.py catalog` first, then `find` each exercise. Template ids are opaque
8-character strings and cannot be guessed from a name.

**`No catalog cached`** — run `scripts/hevy.py catalog`.

**`400 Unrecognized key(s) in object: 'index'`** — something hand-built a PUT body from a GET
response. Use `pull-routine`, which strips the read-only fields.

**`trend` always says `INSUFFICIENT_DATA`** — it needs 4+ weigh-ins in each window and anchors
windows to your most recent weigh-in, so sparse weighing fails the 7-day window. Try
`--window 14` or `--window 30`, and weigh in daily if you want weekly decisions.

**The balance table flags a push:pull imbalance you don't think you have** — check the
`unclassified_exercises` list in the report. Hevy files a lot of machine and custom work under the
`other` muscle group, and misfiled pull work inflates the ratio. The report names those exercises
so you can reassign them by hand.

**A dashboard exercise shows a text tile instead of a photo** — no confident match in the photo
database. The fix is a more standard exercise title in Hevy, not a nearly-right photo.

---

## Known Hevy API quirks

These are verified against the live API and are the reason some steps look indirect. The published
OpenAPI spec is wrong about several of them.

- `pageSize` caps at **10** everywhere except `exercise_templates`, which allows 100.
- There is **no DELETE endpoint** anywhere. Routines and folders created via the API can only be
  removed by hand in the app, so the skill asks before creating anything.
- `POST /v1/routines` returns `{"routine": {…}}`, but `PUT /v1/routines/{id}` returns
  `{"routine": [{…}]}` — a single-element array. The spec documents both as a bare object.
- A routine's own `notes` field is accepted on write and then **never returned**. Coaching cues
  must go in per-exercise `notes`, which do persist.
- A routine **set** has no `rpe` field; `rpe` exists only on logged sets. RIR targets live in the
  exercise `notes`.
- A routine has **no week dimension** — it is one static session. A mesocycle with a week-to-week
  ramp needs either a `PUT` each week or one routine per distinct week.
- `POST /v1/body_measurements` returns **409** if that date already exists; update it with
  `PUT /v1/body_measurements/{date}`.
- Body measurement records carry undocumented `id` and `created_at` fields. `created_at` is when
  the row was *entered*, not the day it describes, so always trend on `date`.
- All weights are kilograms; there is no imperial option.

`references/hevy-api.md` has the full endpoint map, request bodies and enums.
`references/hevy-openapi.json` is the extracted spec.

---

## Privacy

Your training history, bodyweight and plans stay in `~/.kg-trainer/` on your machine. The Hevy API
key is read from the environment or a `chmod 600` file, is never printed, and never leaves your
machine except in the `api-key` header to `api.hevyapp.com`. The generated dashboard contains no
key and makes no API calls — it reads a local `data.js`. The only other outbound request is to a
public CDN for exercise photos, which sends no personal data.

If you paste a key into a chat, rotate it at <https://hevy.com/settings?developer> afterwards.

---

## Files

```
SKILL.md                         the skill itself — seven steps, gates, red flags
README.md                        this file
references/hevy-api.md           endpoint map, request bodies, verified quirks
references/hevy-openapi.json     the extracted OpenAPI spec
references/programming.md        volume landmarks, discipline templates, TDEE math
references/state.md              state layout and profile.json schema
scripts/hevy.py                  API client
scripts/analyze.py               history cache and baseline analysis
scripts/checkin.py               weight trend and check-in log
scripts/dashboard.py             dashboard generator
scripts/dashboard_template.html  dashboard markup, CSS and charts
```

---

## Scope

Programming, load management and calorie/macro targets are in scope. Diagnosing injuries is not.
Radiating pain, numbness, night pain or joint instability are reasons to see a clinician, not to
adjust a program. Nothing here is medical or dietetic advice.
