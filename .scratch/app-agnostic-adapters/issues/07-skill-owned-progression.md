# 07: Skill-owned progression

**What to build:** The skill guarantees progression whatever the app. A progress command reads what the athlete actually logged in `current_week`, applies each exercise's Progression Rule, writes the next week's prescriptions back into the Canonical Plan and advances `current_week`. It prints a before/after table and touches nothing in the app; the coach then sends the change with the existing update. Exercises under a Native Rule are reported as "run by <app>" and left to the app. Deload weeks do not progress.

**Blocked by:** 04 (Canonical Plan authoring), 05 (Send a plan to Liftosaur), 06 (Send a plan to Hevy)

**Status:** done

- [x] Each of `linear`, `double`, `reps_threshold` and `none` has fixture tests covering progress, hold and missed sessions
- [x] Running progress then update moves a Hevy account's routines to the next week
- [x] On Liftosaur, progress agrees with the recorded playground response from ticket 05 for the same logged sets, checked offline
- [x] Native Rule exercises and deload weeks are reported, not changed
- [x] A week with no logged sessions holds every prescription and says why
