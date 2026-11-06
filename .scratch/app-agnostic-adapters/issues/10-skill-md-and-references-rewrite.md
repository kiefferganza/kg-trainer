# 10: SKILL.md and references rewrite

**What to build:** The skill's instructions describe coaching from any Training App with an API, using the final commands. The description triggers on "a training app with an API" as well as Liftosaur and Hevy by name. STEP 1 asks which app when none is set and shows each one's Capabilities. STEP 2's equipment list uses the skill's canonical equipment suffixes. STEP 4 writes a Canonical Plan. STEP 5 becomes "send it to the Active App" (validate, stats, compile, check if available, approval, push) with the Fallback path written out. STEP 7's PROGRESS IT uses skill-owned progression. The Liftosaur-specific references move under a per-adapter folder, and the state layout doc covers the new plan files, `app` block, per-app key files and exercise-map overrides.

**Blocked by:** 05 (Send a plan to Liftosaur), 06 (Send a plan to Hevy), 07 (Skill-owned progression), 08 (Dashboard plan tabs from the Canonical Plan), 11 (Native progression and the drift guard)

**Status:** ready-for-agent

- [ ] Every command in SKILL.md exists and runs as written
- [ ] Red flags and rationalizations no longer assume Liftosaur, and still forbid invented numbers and unverified pushes
- [ ] The state layout doc matches what the scripts actually write
- [ ] Vocabulary matches GLOSSARY.md throughout
- [ ] A walk-through of STEP 1 to STEP 7 on a Hevy store and on a Liftosaur store works end to end
