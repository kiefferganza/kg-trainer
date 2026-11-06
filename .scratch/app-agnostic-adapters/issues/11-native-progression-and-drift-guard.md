# 11: Native progression and the drift guard

**What to build:** On Liftosaur the app keeps moving the weights, the Canonical Plan stays in step with it, and no update can silently overwrite what the app progressed. Two parts:

1. **Progression semantics match the app.** Liftosaur's recorded run (`tests/fixtures/playground_finish.json`, 2026-11-07) shows that when a rule fires, the change applies to that exercise in every later week of the program, keeping the authored ramp. The deload week is included, even though its line says `progress: none`. For example, double progression took Bench Press from 3x6 to 3x7 in week 1 (80 kg), week 2 (82.5 kg) and the week 3 deload (2x6 to 2x7 at 70 kg). Linear progression added 2.5 kg to Lat Pulldown in week 2 (57.5 to 60 kg) and in the deload (45 to 47.5 kg). `plan.py progress` currently replaces only the next week with values derived from the log, and leaves deload weeks alone. Change it to the app's semantics for every app. A deload week, or a `none` rule in `current_week`, only means finishing that week triggers nothing.
2. **Drift guard.** An Adapter declares whether its app runs Progression Rules itself. Before `app.py update` writes to such an app, it fetches the app's program and compares the current week's working prescriptions (sets, reps, load per exercise) with the Canonical Plan. On any difference it refuses and shows both. Only an explicit override flag writes anyway, after showing the difference. Liftosaur reformats stored text (`3x7 / 80kg 180s`, `///` lines dropped), so the comparison parses prescriptions and never compares text.

**Blocked by:** 05 (Send a plan to Liftosaur), 07 (Skill-owned progression)

**Status:** ready-for-agent

- [x] `plan.py progress` applies a fired rule's change to the exercise in every later week of the block, deload weeks included, and keeps each week's authored offset
- [x] The offline agreement test compares every week of the recorded `updatedProgramText` for Bench Press and Lat Pulldown, not only week 1, and passes
- [x] Hold, miss and no-log behaviour from ticket 07 is unchanged
- [x] The Liftosaur Adapter declares native progression; Hevy does not
- [x] `app.py update` on a native-progression app refuses on drift, shows the app's and the Canonical Plan's prescriptions side by side, and writes only with the override flag
- [x] The drift comparison parses Liftosaur's reformatted program text (covered by a fixture taken from a live `get`)
- [x] With no drift, update proceeds as before
