---
status: accepted
---

# Adapter contract instead of a single hard-wired training app

The 2026-10-11 Liftosaur migration deliberately chose "no provider abstraction" and wired every script to Liftosaur. We are reversing that: kg-trainer now defines its own Adapter Contract, a Canonical Plan and a Workout Log, and ships one Adapter per Training App (Liftosaur and Hevy first), with reading workout history as the only required Capability and a named Fallback for every other one. We chose pre-built, tested Adapters over having the agent drive an arbitrary app's API live each session, because live-improvised calls would make every number depend on what the agent wrote that day and break the rule that figures come from reproducible scripts.

## Consequences

- Progression is guaranteed by the skill (`plan.py progress`), not by the app. Liftosaur's native `progress:` becomes one compiled form of a Progression Rule, not the mechanism.
- Volume and validity checks run locally on the Canonical Plan for every app. An app's server-side checks (Liftosaur `program-stats`, `playground`) are a cross-check, and a disagreement is reported.
- Hevy is supported again, so `tests/test_no_hevy.py` is replaced by a structural test: no script outside `scripts/adapters/` imports an app module.
- Exercise names, the muscle map and the dashboard key on Canonical Exercise Names that the skill owns, not on any app's catalogue.
