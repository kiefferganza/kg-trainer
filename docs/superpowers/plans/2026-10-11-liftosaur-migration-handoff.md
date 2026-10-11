# Handoff — Liftosaur migration, stopped during Task 3

**Written:** 2026-10-11
**Stopped at:** Task 3 of 8, fix round 1 committed, **re-review not yet run**
**Branch:** `worktree-liftosaur-migration`
**Worktree:** `.claude/worktrees/liftosaur-migration` (inside the repo, git-ignored via `.claude/`)
**Plan:** `docs/superpowers/plans/2026-10-11-liftosaur-migration.md`
**Spec:** `docs/superpowers/specs/2026-10-11-liftosaur-migration-spec.md`

`main` is untouched at `a0ec246`. Nothing has been merged. No agents are running. The working tree is clean apart from an untracked `.venv/`.

---

## The one thing to do first

**Task 3's fix round 1 is committed but has not been re-reviewed.** The process requires every fix round to end with a scoped re-review before the task can be called complete. That re-review is the next action, not a fresh task.

```
bash <SDD_SKILL>/scripts/review-package docs/superpowers/plans/2026-10-11-liftosaur-migration.md 14adf90 HEAD
```

Then dispatch `re-review-prompt.md` with the seven findings listed under "Task 3 — findings sent for fix" below, the brief, the report file, and the printed diff path. Fix base is `14adf90`; head is `211e798`.

---

## Commit history on this branch

| Commit | What |
|---|---|
| `e893631` | Spec and plan |
| `2f67ad0` | Task 1 — Liftohistory parser + test scaffolding |
| `0c955db` | Task 1 fix round 1 — record-boundary and malformed-input hardening |
| `3190178` | Task 2 — Liftosaur REST client |
| `85c2b7f` | Task 2 fix round 1 — failure paths, redirect refusal |
| `4fa4cfa` | Task 2 fix round 2 — module-private opener |
| `14adf90` | Task 3 — vendored references + 374-entry muscle map |
| `211e798` | Task 3 fix round 1 — MCP validation, no persistent cache, map consistency |

Branch base for a final whole-branch review: `a0ec246`.

---

## Task status

| Task | State |
|---|---|
| 1. Liftohistory parser | **Complete.** Review clean after 1 fix round. 2 minors parked. |
| 2. REST client | **Complete.** Review clean after 2 fix rounds. 1 minor parked. |
| 3. Vendored references + muscle map | **Fix round 1 committed, re-review outstanding.** |
| 4. Migrate `analyze.py` | Not started. Brief regenerable. |
| 5. Migrate `checkin.py` | Not started. |
| 6. Migrate `dashboard.py` | Not started. |
| 7. Delete Hevy, rewrite docs | Not started. |
| 8. Live verification | **Blocked on the user** — needs their API key and their completed Hevy→Liftosaur history import. |

After Task 7, a whole-branch review runs on the most capable model, pointed at the deferred-minor list below.

---

## Verified state, as of this handoff

Checked directly, not taken from a subagent's report:

- `69 passed` — `./.venv/bin/python -m pytest`
- `references/exercise-muscles.json`: 374 entries, **0** stored `unknown`
- All 20 exercises this user actually trains map correctly (`Lat Pulldown`→lats, `Hip Thrust`→glutes, `Cable Crunch`→abdominals, `Seated Row`→upper_back, `Pullover`→lats, `Leg Extension`→quadriceps, `Shrug`→traps, …)
- Compound-lift families now internally consistent: every deadlift variant including `Deficit` and `Trap Bar` is `hamstrings`; `Clean`/`Snatch`/`Clean and Jerk`/`Hang Clean` are all `quadriceps`; the explicit high-pulls remain `traps`; `Reverse Hyperextension` is `glutes`; `Bent Over One Arm Row` aligns with `Bent Over Row` at `upper_back`
- No `.superpowers/sdd` scratch path remains in `scripts/fetch_reference.py`
- `import liftosaur` does **not** mutate urllib's global opener, and a bare `urlopen` to the jsDelivr CDN still returns HTTP 200 with the module imported — this was the Task 6 failure mode and it is closed

---

## Task 3 — findings sent for fix (what the re-review must verdict)

From the task review of `14adf90`, all addressed by `211e798` per the implementer:

1. **Important** — persistent cache hardcoded to the migration scratch directory; non-atomic write left a truncated file that later runs skipped and failed to parse. Required: no persistent cache, per-run temp download, `--fedb PATH` retained.
2. **Important** — `payload["result"]["content"][0]["text"]` indexed with no shape validation; a tool-level `isError` body would be written into `references/*.md` as if it were the reference document, and `--check` would then report "stale" rather than "failed". Required: validate shape, check `isError`, refuse empty text, fetch all three documents before writing any.
3. **Important** — compound-lift family inconsistencies (see R16).
4. **Minor, folded in** — the vocabulary test imported the same `MUSCLES` it validated against, so adding `unknown` to `MUSCLES` would silently defeat the only gate protecting the map. Required: pin the literal 15-name list.
5. **Minor, folded in** — `build_muscle_map` wrote a literal `"unknown"` into the stored JSON. Required: omit unmatched and report them.
6. **Minor, folded in** — no test for the preserve-existing behaviour, the only thing guarding 314 rows of hand-mapping.
7. The implementer additionally claims a live run against the real MCP server matched 374/374 and left the vendored files byte-identical. **Unverified — the re-review should treat it as a claim.**

---

## Rulings made on the user's behalf

The working ledger lives at `.superpowers/sdd/2026-10-11-liftosaur-migration/progress.md`, which is git-ignored scratch and will not survive a clean. Every ruling is reproduced here so it survives in git. Each line is: what was decided — why — what it costs if wrong.

**R1** — `cmd_deep_dive` is rewritten to compute from the local `data/workouts.jsonl` cache instead of a per-exercise history endpoint. — Liftosaur has no `exercise_history` equivalent, so the plan's one-line instruction was not implementable. — If wrong: deep-dive's format may differ slightly from the Hevy version and no longer reflects sets logged since the last `pull`. **Applies to Task 4, not yet implemented.**

**R2** — Added `tests/conftest.py` setting `KG_TRAINER_HOME` before test modules import. — `paths.state_dir()` refuses to resolve inside the skill tree and `analyze.py`/`checkin.py`/`dashboard.py` all call it at import time; without this, Tasks 4–6 cannot be tested at all. — If wrong: nothing material, test isolation only.

**R3 / R3a / R15** — The exercise-database seed is supplied by `--fedb PATH`, otherwise downloaded per run to a temp file with no persistent cache. R3a's "cache it in the workspace" wording was my error and produced a hardcoded migration scratch path in a shipped script; R15 supersedes it. — A build-time script needs no cache, and removing it removes the truncated-file bug. — If wrong: re-downloads ~1 MB when Liftosaur's catalogue changes.

**R4** — Task 4's `cmd_pull` must also fetch `GET /measurements/weight` into `data/measurements.json` as `[{"date","weight_kg"}]`. — The old `cmd_pull` pulled measurements and the new one as drafted does not, which would quietly empty the bodyweight table. — If wrong: bodyweight section under-reports; Task 8 Step 5 catches it. **Not yet implemented.**

**R5** — `unknown` is a runtime fallback from `muscle_for()` only, never a stored value; the `MUSCLES` gate stands and the plan's Interfaces text was wrong. — A stored `unknown` is the silent-undercount failure the map exists to prevent. — If wrong: nil.

**R6** — Dropped Task 3's unimplemented claim that it cross-checks Task 1's fixture. — Cheaper than inventing a test that two copies of one upstream document match. — If wrong: an upstream format change is caught by Task 8 against real records instead.

**R7** — Task 7 adds `liftosaur-key` to the ignore file and makes no `hevy-key` replacement, because that string was never there. — Verified by reading the file. — If wrong: nil.

**R8** — Rebuilt `.venv` on Python 3.11.16 (from 3.9.6). — The plan's constraint names 3.11+, and testing on 3.9 verifies something other than what ships. — If wrong: nil.

**R9** — Fixed the parser's record-boundary leniency even though the plan's own comment mandated it. A record closed only on a bare `}`, so a truncated page merged the following record into the previous one and attributed one session's sets to another. — The spec requires every figure to trace to a logged set, and `/history` is paginated so truncation is realistic. — If wrong: the parser could reject a record shape Liftosaur really emits, which Task 8 would surface as a parse error rather than a silent wrong number.

**R10** — Folded four Task 1 minors into its fix round (comment-only-block crash, phantom set from a malformed count, silently dropped weight, unit-less weight) plus the conftest `setdefault`. — Each produced a confidently wrong figure rather than an error, and `setdefault` meant a developer with `KG_TRAINER_HOME` set ran tests against their real training store. — If wrong: more churn in an approved task.

**R11** — A unit-less-weight set group stays **dropped** rather than retained with a null load; parked rather than fixed. — The re-reviewer preferred retention and its evidence was right (`analyze.py` tolerates null weights at :175, :181, :237), but the format reference says units are always explicit, so this is a defensive-only path, and minors do not extend the fix loop. — If wrong: a malformed record undercounts set volume silently. **Open for the final review to decide.**

**R12** — Broadened the client's exception handling beyond the plan's narrow `except HTTPError`. — A dropped connection, timeout or non-JSON 200 escaped as a traceback; this is a CLI run on gym wifi and the spec requires a status-only failure. — If wrong: a broad except could mask a programming error, mitigated by naming the exception type.

**R13** — Raised two Task 2 findings the reviewer filed as Minor: the vacuous key-leak test, and urllib forwarding the `Authorization` header across a redirect. — A worthless test over a hard requirement reads as coverage, and forwarding a credential off-origin is a security defect regardless of filing. — If wrong: more churn in a spec-compliant task.

**R14** — Folded Task 2's crash-path and robustness minors into one hardening round. — One coherent pass over a module whose job is HTTP error handling. — If wrong: nil.

**R16** — Compound-lift map fixes: `Deficit Deadlift`, `Deficit Deadlift, Trap Bar`, `Trap Bar Deadlift` → `hamstrings`; `Clean`, `Snatch`, `Hang Clean, Kettlebell`, `Clean and Jerk` → `quadriceps`; high-pulls stay `traps`; `Reverse Hyperextension` → `glutes`; `Bent Over One Arm Row` → `upper_back`. Deviates from the reviewer on `Clean and Jerk` (it offered shoulders or quadriceps) in favour of family consistency, since the inconsistency is the finding. — One movement family reporting three primary muscles defeats an honest per-muscle set count. — If wrong: a handful of Olympic and deadlift variants attribute sets to one defensible group rather than another. **None of these lifts are in this user's program.**

**R17** — Folded two Task 3 minors in: pin the literal vocabulary in the test, and omit unmatched names rather than storing `"unknown"`; plus a test for preserve-existing. — A gate disabled by editing the thing it validates is the same defect class as the vacuous key-leak test, and untested preservation of 314 hand-mapped rows is a silent-loss risk. — If wrong: nil.

---

## Deferred minors for the final whole-branch review

- **Task 1** — `liftohistory.py` `"s|" in token` clause is redundant alongside `endswith("s")`, which also matches any unrecognised word ending in s.
- **Task 1** — an unrecognised section overwrites `working`, so a second stray section discards the first. No known producer.
- **Task 1** — `tests/conftest.py` never cleans up its temp directory.
- **Task 1 (R11)** — unit-less weight group dropped rather than retained with `weight_kg=None`. A dropped-group counter would remove the silence either way.
- **Task 1** — the implementer's report claims trailing comment lines of an unterminated record become the next record's note; the re-reviewer measured them as silently discarded instead. Report inaccurate, behaviour harmless.
- **Task 2** — cramped `c = ...; c.add_argument(...)` argparse style; matches the plan's own formatting.
- **Task 3** — `muscle_for`, needed by the runtime balance table, lives in a vendoring script. Task 4 imports it from there as planned.
- **Task 3** — `--check` verifies the three vendored docs but not that `exercise-muscles.json` is complete for the current `exercises.txt`; the coverage test covers that.
- **Task 3** — the coverage test's `n.endswith(":")` filter does not mirror the script's `rstrip`. Harmless.

---

## Environment notes for whoever resumes

- **venv:** `.venv`, Python 3.11.16, pytest 8.4.2. Run tests as `./.venv/bin/python -m pytest`. Built from `/Users/kiefferganza/.local/bin/python3.11`; the system `python3` is 3.9.6 and must not be used.
- **`paths.state_dir()` exits inside the skill tree.** `tests/conftest.py` handles this for tests by setting `KG_TRAINER_HOME`. Any script run by hand from this worktree needs `KG_TRAINER_HOME` set, or it will refuse to start — `.env` has a commented `KG_TRAINER_HOME=` line for this. Note `liftosaur.py ping` does not need it, because `api_key()` reaches `.env` before it touches `state_dir()`.
- **Worktree shell guard.** This session is worktree-isolated, and the harness refuses compound shell commands it cannot prove stay inside the worktree. Heredocs mentioning the ignore file by name, `for` loops calling scripts, and `cmd && cmd` chains were all rejected. Run plain, separate commands, and write files with the file tools rather than heredocs.
- **Task briefs** for Tasks 4–7 were extracted to the git-ignored workspace and are regenerable at any time:
  `bash <SDD_SKILL>/scripts/task-brief docs/superpowers/plans/2026-10-11-liftosaur-migration.md <N>`
- **`<SDD_SKILL>`** above is `/Users/kiefferganza/.claude/plugins/cache/claude-plugins-official/superpowers/6.4.1/skills/subagent-driven-development`.
- **Liftosaur MCP reference tools need no authentication.** `POST https://www.liftosaur.com/mcp`, JSON-RPC `tools/call`, header `Accept: application/json, text/event-stream`, text at `result.content[0].text`. Confirmed working for `get_liftohistory_reference` and `list_exercises`.

---

## Credentials — done

The API key is configured and **verified live** as of this handoff.

- It lives in `.env` at the project root (`/Users/kiefferganza/Desktop/side/skills/kg-trainer/.env`), mode `600`.
- `scripts/liftosaur.py ping` succeeds against the real API.
- **The account reports `units: kg`**, which settles Task 8 Step 1: no unit conversion concerns for this user's own data. Warmup timer 90s, workout 180s.
- `.env` is excluded from git by `.git/info/exclude` (repo-wide, local, untracked) and by this branch's `.gitignore`. `.env.example` is the tracked template.
- Resolution order in `api_key()`: exported `$LIFTOSAUR_API_KEY` → nearest `.env` searching cwd then parents → `<kg-trainer-data>/liftosaur-key`. The upward walk is why a `.env` at the project root is found from inside this worktree too.

**Note:** the key value was accidentally echoed into the session transcript during a debug check on 2026-10-11. Rotate it in Settings → API Keys if that transcript is not fully controlled. No committed code prints the key.

## Still needed from the user

1. **Confirmation that the Hevy→Liftosaur history import is done.** Task 8 Step 2 expects a workout count near the 334 records that were in Hevy, and will stop rather than report over partial data.
2. **A decision they may want to make:** whether RPE survived their import. The Hevy baseline was an average top-set RPE of 8.6 across 194 top sets. If the import dropped the `@` annotations, that table goes empty — the report degrades honestly rather than inventing numbers, but it is a real loss and Task 8 Step 4 checks it.
