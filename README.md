# kg-trainer

An AI personal trainer skill for Claude Code. It builds a training program from your own logged
training data — powerlifting, bodybuilding, endurance or hybrid — pushes it into the Liftosaur app
as a Liftoscript program, runs weekly check-ins against your weigh-ins and workout logs, and
generates a static dashboard of your history.

It is built on two rules: **never invent a number**, and **every step writes state to disk** so
next week is a comparison rather than a cold start.

---

## Requirements

| | |
|---|---|
| Python | 3.11 or newer, standard library only — no `pip install` |
| Liftosaur | Optional but recommended. The REST API requires a **Liftosaur premium** subscription |
| Network | Only for the Liftosaur API and, for dashboard photos, the free-exercise-db CDN |

Everything works without Liftosaur; you supply your numbers by hand and the skill labels them as
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

### 2. Get a Liftosaur API key

In the Liftosaur app, go to **Settings → API Keys** and create one. It starts with `lftsk_`, and
the API only answers while the account has an active premium subscription.

### 3. Give the skill the key

The scripts look for the key in this order, and the first one found wins:

1. An exported `$LIFTOSAUR_API_KEY`.
2. The nearest `.env` file, searching the working directory and then its parents.
3. `kg-trainer-data/liftosaur-key`.

Preferred — a `.env` at the root of the project you open Claude Code in, so the key never appears
in a chat transcript. Copy `.env.example` from this repo, fill in `LIFTOSAUR_API_KEY=`, and lock
it down:

```bash
chmod 600 .env
```

Make sure the project's `.gitignore` covers `.env`. The scripts never echo the key, and it never
reaches a plan file, the dashboard, or any external service other than Liftosaur.

### 4. Confirm it works

From your project directory:

```bash
~/.claude/skills/kg-trainer/scripts/liftosaur.py ping
```

Run the scripts from your project, not from this repo: they refuse to keep personal data inside the
skill's own directory.

A JSON object with your account settings means you are set; it includes `units`. kg-trainer works
in kilograms and converts pounds on read. `401` means a bad key or a lapsed subscription.

---

## First run

Just tell Claude what you want — "build me a training program" — and the skill takes over. To do
the data steps yourself first:

```bash
~/.claude/skills/kg-trainer/scripts/analyze.py pull          # cache every workout and the bodyweight series
~/.claude/skills/kg-trainer/scripts/analyze.py report        # the baseline tables
```

Liftosaur pages 200 records at a time, so even a long history is a handful of requests, and
every pull re-reads the whole history and replaces the cache, so late-logged, edited and deleted
sessions always come out right.

### What the seven steps do

| Step | What happens | Ends with |
|---|---|---|
| 1 | Pulls your Liftosaur history and computes frequency, best sets, estimated 1RM trend, muscle balance, RPE | Tables on screen, cache in `kg-trainer-data/data/` |
| 2 | Interviews you one short group of questions at a time, skipping whatever the data already answered | `profile.json` written |
| 3 | Tells you what your data actually says, including where it disagrees with you | **Stops and waits for you** |
| 4 | Designs a 6–12 week block as one Liftoscript program, every week written out, progression built in, loads derived from your own estimated 1RMs | **Stops and waits for your approval** |
| 5 | Checks every exercise name, verifies weekly volume with `program-stats`, simulates it with `playground`, pushes it | The real program id reported back |
| 6 | Generates the dashboard | `kg-trainer-data/dashboard/index.html` |
| 7 | Ongoing coaching via keywords | A check-in record per week |

Steps 3 and 4 stop on purpose. The plan you get is meant to survive your objections first.

---

## Day-to-day use

Say any of these to Claude:

| Say | What it does |
|---|---|
| `UPDATE` | Re-pulls your history, rebuilds the dashboard, lists what changed |
| `HOW AM I DOING` | Compares your logged sets against the program's targets; says which lifts are ready to go up |
| `PROGRESS IT` | Fetches the current program, edits it, checks it, shows before/after, and pushes it back on your yes |
| `IT HURTS` | Asks where and how bad, then swaps the aggravating exercises and updates the program |
| `NEXT BLOCK` | Re-runs the analysis, truth-telling, design and push using everything logged since |
| `check-in` | The weekly loop: weigh-in trend, training review, one adjustment, recorded |

### The weekly check-in

```bash
~/.claude/skills/kg-trainer/scripts/checkin.py import-liftosaur   # or: log-weight 2026-10-11 70.3
~/.claude/skills/kg-trainer/scripts/checkin.py trend --calories 2650
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

### `scripts/liftosaur.py` — Liftosaur API client

```
ping                                      validate the key, show account units
exercises [pattern]                       match the built-in exercise list
settings                                  account settings
history [--since YYYY-MM-DD] [--until]    raw workout records (Liftohistory text)
measurements                              overview of tracked metrics
measurement weight                        one full measurement series
programs                                  your programs
program-get <id|current> [--out FILE]     fetch a program's Liftoscript
program-push <file> [--name NAME] [--id]  create (POST) or replace (PUT) a program
program-stats <file>                      per-day content and weekly sets per muscle
playground <file> [--week N] [--day N] [--command CMD ...]
                                          simulate one day; it lists completed sets only,
                                          so pass complete_set(ex, set) to see targets
```

### `scripts/analyze.py` — history and baseline

```
pull [--full]        re-read every workout + bodyweight and replace the cache (--full: same)
report [--json]      frequency, best sets, 1RM trend, muscle balance, RPE
deep-dive <pattern>  one lift, every logged set, ranked by estimated 1RM
```

### `scripts/checkin.py` — weight trend and check-in log

```
log-weight <date> <kg> [--fat --waist]
import-liftosaur                   pull weigh-ins from Liftosaur into the local log
trend [--window 7] [--calories N]  weekly averages, rate, noise, observed TDEE
record --calories --protein --carbs --fat [--sessions --planned --notes]
history [--last N]                 past check-ins
```

### `scripts/dashboard.py` — the board

```
build [--name NAME] [--goal TEXT] [--open] [--no-images]
```

Writes `kg-trainer-data/dashboard/`. Opens straight from the filesystem — no server, and no API key
in the output. Sections: hero lift, stat tiles, plan tabs (one per week and day of your current
Liftosaur program) with exercise photos, a 52-week consistency heatmap, estimated-1RM small
multiples, sets per muscle, and personal records. Every mark has a hover tooltip with the exact
numbers.

Photos come from the public-domain [free-exercise-db](https://github.com/yuhonas/free-exercise-db);
each exercise has a start and finish frame that crossfade so a card reads as a rep in motion. An
exercise with no confident title match gets a clean text tile rather than a wrong photo — the
script prints the match rate so you can see how many.

### `scripts/fetch_reference.py` — vendored references

```
fetch_reference.py               refresh references/ from Liftosaur's reference server
fetch_reference.py --check       fail if a vendored file is out of date
fetch_reference.py --fedb PATH   seed muscles from a local free-exercise-db JSON
```

---

## Where your data lives

One `kg-trainer-data/` per project, at the project root: the scripts walk up from wherever they
are run to the nearest existing `kg-trainer-data/` or git root, so running from a subdirectory
reuses the same store. `scripts/paths.py` prints which one is in use.

```
kg-trainer-data/
  profile.json               your intake answers and current targets
  liftosaur-key              optional, chmod 600
  weight-log.csv             date,weight_kg,fat_percent,waist_cm
  checkins.jsonl             append-only, one record per check-in
  data/                      cached workouts, bodyweight, last-pull timestamp
  plans/                     the markdown plans and the exact Liftoscript pushed
  dashboard/                 generated board
```

Set `$KG_TRAINER_HOME` to pin all of it to one place across projects, which is also how you keep a
test profile separate from your real one.

`data/` is a rebuildable cache. `profile.json`, `weight-log.csv`, `checkins.jsonl` and `plans/` are
not — they are the only record of what was decided and why. Nothing here is ever committed to the
skill's own directory.

---

## Troubleshooting

**`401`** — the key is wrong, revoked, or the premium subscription has lapsed. Create a new key in
Settings → API Keys.

**`422` on `program-push`** — the Liftoscript does not parse. Run `program-stats` or `playground`
on the file to locate the error; `references/liftoscript.md` has the syntax.

**`No built-in exercise matches`** — Liftosaur names carry equipment as a suffix
(`Bicep Curl, Cable`). Try a shorter pattern, or create a custom exercise in the app.

**`trend` always says `INSUFFICIENT_DATA`** — it needs 4+ weigh-ins in each window and anchors
windows to your most recent weigh-in, so sparse weighing fails the 7-day window. Try
`--window 14` or `--window 30`, and weigh in daily if you want weekly decisions.

**The balance table has an `unknown` row** — check `unclassified_exercises` in the report. Those
exercises, usually custom ones, have no row in `references/exercise-muscles.json`, so their sets
cannot be attributed to a muscle and the table undercounts whatever they train. Map them in
`kg-trainer-data/exercise-muscles.json`, e.g. `{"bayesian curl": "biceps"}`, using one of the
muscle names the report already shows; it is layered over the built-in map.

**A dashboard exercise shows a text tile instead of a photo** — no confident match in the photo
database. The fix is a more standard exercise name, not a nearly-right photo.

---

## How Liftosaur shapes the design

- **History is text.** `GET /history` returns each workout as Liftohistory, a line-oriented text
  format, which `scripts/liftohistory.py` parses. A record it cannot read faithfully yields no
  sets rather than guessed ones.
- **A program is one document.** A block is a single Liftoscript program with every week and day
  written out, and progression rules (`lp`, `dp`, `sum`, `custom`) that the app applies itself.
- **The server does the counting.** `program-stats` reports weekly sets per muscle and
  `playground` simulates a day (completing sets you name), so a program is checked before it is pushed.
- **Weigh-ins carry their unit** (`69.9kg`, `154lb`). A value with no unit is skipped rather than
  assumed, and each weigh-in is dated in your local time zone.

`references/liftosaur-api.md`, `references/liftohistory.md` and `references/liftoscript.md` are the
vendored references, refreshed by `scripts/fetch_reference.py`.

---

## Privacy

Your training history, bodyweight and plans stay in `kg-trainer-data/` at the root of the project
you run the skill from. If that project is a git repo, add `kg-trainer-data/` to its `.gitignore`
— the scripts warn until you do. The Liftosaur API key is read from the environment, a `.env`
file, or a `chmod 600` file, is never printed, and never leaves your machine except in the
`Authorization` header to `www.liftosaur.com`; the client refuses to follow a redirect, so the key
cannot be forwarded elsewhere. The generated dashboard contains no key and makes no API calls — it
reads a local `data.js`. The only other outbound request is to a public CDN for exercise photos,
which sends no personal data.

If you paste a key into a chat, revoke it in Settings → API Keys afterwards.

---

## Files

```
SKILL.md                         the skill itself — seven steps, gates, red flags
README.md                        this file
.env.example                     template for the API key
references/liftosaur-api.md      endpoint map and pagination
references/liftohistory.md       the workout record format
references/liftoscript.md        the program language
references/exercises.txt         Liftosaur's built-in exercise names
references/exercise-muscles.json built-in exercise -> primary muscle, for the balance table
references/programming.md        volume landmarks, discipline templates, TDEE math
references/state.md              state layout and profile.json schema
scripts/paths.py                 resolves the kg-trainer-data/ store
scripts/liftosaur.py             API client
scripts/liftohistory.py          Liftohistory parser
scripts/fetch_reference.py       refreshes the vendored references and the muscle map
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
