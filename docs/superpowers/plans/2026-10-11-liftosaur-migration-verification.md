# Liftosaur migration: live verification

**Run:** 2026-10-12, against the user's real Liftosaur account, from branch
`worktree-liftosaur-migration`.

**Store:** a throwaway `KG_TRAINER_HOME` under the session scratch directory, so the user's real
`kg-trainer-data/` was not written to. Every API call was a read, except `POST /program-stats` and
`POST /playground`, which compute a result and change nothing. Nothing was pushed.

## Results

| Check | Result | Against the Hevy baseline |
|---|---|---|
| Auth (`liftosaur.py ping`) | 200, `units: kg`, warmup timer 90 s, workout timer 180 s | No unit conversion needed for this account |
| Full pull (`analyze.py pull --full`) | **350 workouts** from 350 records, 2024-02-13 to 2026-10-11 | 334 expected. The 16 extra are sessions logged after the Hevy export, so the import looks complete |
| Unmapped exercises | 11 custom exercises, 87 working sets in the last 8 weeks | Custom exercises carried over from Hevy, so they are not in Liftosaur's built-in list |
| RPE | **5 top sets, average 9.0**, all from one session (2026-10-11) logged natively in Liftosaur | Hevy had **8.6 across 194 top sets**. The import dropped every `@` annotation. See below |
| Weigh-ins (`checkin.py import-liftosaur`) | 265 weigh-in days from 271 values, 2024-01-30 to 2026-10-12, none unreadable | Hevy import was 50 weigh-ins. Liftosaur holds the longer series |
| Trend (`trend --calories 1705 --window 30`) | `WITHIN_NOISE`: 70.47 kg average (n=18, sd 0.51) against 70.27 (n=9), +0.2 kg/week, observed TDEE 1485 kcal | Same verdict as the 2026-09-18 Hevy baseline |
| Programs | One program, `Meso1 Push A`, id `ekjzpuyh`, current | — |
| `program-get current` | 9-line Liftoscript, one week, one day, five exercises | — |
| `program-stats` | Day 1: about 50 min, 11 working sets, all hypertrophy. Shoulders 7, chest 6.5, with synergists counted | — |
| `playground --week 1 --day 1` | Parse OK. The workout is empty unless sets are completed (see below) | — |
| Dashboard build | Built. 350 workouts, 1 program day (`Week 1 · Day 1`), 6 lift charts, 364 days, photos matched for 4 of 5 exercises | — |
| Key leak check | `grep -ric lftsk_` on the dashboard output: 0 in `index.html`, 0 in `data.js` | — |

## Exercises needing a muscle mapping

These are custom exercises, so they belong in the user's own store map
(`kg-trainer-data/exercise-muscles.json`), not in the skill's shipped
`references/exercise-muscles.json`, which `fetch_reference.py` rebuilds from the built-in list. The
verification store used this mapping. The user still needs to confirm it before it goes into
their real store:

| Exercise | Sets, last 8 weeks | Mapped to |
|---|---|---|
| Lat Pullover | 29 | lats |
| Iso-Lateral Row, Leverage Machine | 26 | upper_back |
| SA Tricep Extension | 6 | triceps |
| Circular Lat-Pulldown | 6 | lats |
| Overhead Tricep Extension | 4 | triceps |
| Seated Cable Row - V Grip Cable | 4 | upper_back |
| bayesian curl | 4 | biceps |
| Reverse Flye | 3 | shoulders |
| glute raise machine | 2 | glutes |
| Hip Adduction Machine | 2 | adductors |
| Treadmill | 1 | left unmapped: cardio, no muscle in the vocabulary |

With these mapped, `unknown` drops from 87 sets to 1. The balance table then shows push 222 to
pull 140, a ratio of 1.59, which still trips the 1.5 flag. Before mapping, the ratio was 209 to 71
(2.94) and badly overstated the imbalance, because 69 of the unmapped sets were pulling work and
only 13 were pushing.

## Things that did not match the plan

1. **`nextCursor` is a number.** The live API sends a millisecond timestamp. The Task 2 client
   only followed string cursors, so every full pull would have stopped silently at 200 records.
   This was fixed in Task 4 (R19).
2. **Weigh-in values.** `timestamp` is epoch milliseconds and the ISO date is a separate `date`
   field. The plan's importer read `timestamp[:10]` and would have crashed. Weigh-ins are now dated
   in the local zone, because this user is UTC+8 and a morning weigh-in falls on the previous UTC
   date (R22, R23).
3. **`playground` lists only completed sets.** With no commands it returns an empty workout and
   proves only that the program parses (a parse error comes back as 422). The client now takes
   `--command "complete_set(1, 1)"` (repeatable), and SKILL.md says so.
4. **RPE did not survive the import.** Every Hevy-era `@` annotation is gone, so the RPE table now
   rests on one session. The report says so honestly rather than inventing numbers, and it
   rebuilds as sessions are logged in Liftosaur. Getting the history back would mean re-importing
   with RPE, which is outside this skill.

## Follow-up worth considering

The Liftosaur MCP server's `list_custom_exercises` returns each custom exercise's target muscles.
A future `fetch_reference.py` option could seed the personal muscle map from it instead of mapping
by hand. It needs the authenticated MCP endpoint, so it is out of scope here.
