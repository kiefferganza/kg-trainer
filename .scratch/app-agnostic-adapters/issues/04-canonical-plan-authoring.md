# 04: Canonical Plan authoring

**What to build:** The coach can write a training block as a Canonical Plan, check it, count its volume and render the readable version, all locally and for any app. The plan holds every week, a `current_week`, days, exercises under Canonical Exercise Names with sets, load, rest, cue and RIR target, and Progression Rules (`linear`, `double`, `reps_threshold`, `none`, or a Native Rule tagged with one app). A deload week has progression off. A per-week rule can override the block-level rule for that week.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Validation rejects unknown exercise names (shipped or personal map), malformed Progression Rules, and barbell, smith-machine, EZ-bar or trap-bar loads that are not multiples of 2.5 kg, naming each failure
- [ ] Stats prints weekly working sets per muscle from the muscle map, so no one counts by hand
- [ ] Rendering writes the human-readable plan, one table per session, and is the only way that file is produced
- [ ] A plan with a Native Rule validates; its app tag is kept for the compile step
- [ ] Fixture tests cover a multi-week block with a deload and a per-week override
