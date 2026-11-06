# 09: Contract test suite and new-app guide

**What to build:** Adding a third app becomes a repeatable job. One shared test suite runs against every Adapter on recorded fixtures and checks the behaviour the contract promises. Each Adapter has a reference doc stating its Capabilities, its quirks and when it was last verified live. A guide explains how to scaffold a new Adapter from an app's API docs, and when it counts as done: passes the suite unchanged, has fixtures, has its reference doc. New Adapters are built in the skill repo, never in a user's store.

**Blocked by:** 05 (Send a plan to Liftosaur), 06 (Send a plan to Hevy)

**Status:** ready-for-agent

- [x] The shared suite discovers every Adapter automatically and runs against both Liftosaur and Hevy
- [x] The suite checks Workout Log shape, kg units, logged-only RPE, Capability declarations matching implemented operations, and key-free errors
- [x] Liftosaur and Hevy each have a reference doc with Capabilities and a dated verification status
- [x] The new-adapter guide exists and a dry run of following it produces a stub that the suite fails in clear, actionable ways

**Done 2026-11-07.** `tests/test_contract.py` runs on `contract.names()` with a per-Adapter fixture provider in `tests/contract_fixtures/<app>.py`. The guide is `references/adapters/README.md`; reference docs are `references/adapters/{liftosaur,hevy}/README.md`. Dry run: a naive stub Adapter (scaffolded from an imaginary API, kept outside the repo) failed the suite with messages naming each fix: no fixture provider; declared-but-missing `compile`/`get_plan`; implemented-but-undeclared `find_exercise`; `ping` without `units`; missing Workout Log fields; oldest-first history; pounds returned as kg; no `rpe_logged`; a swallowed HTTP error returned as an empty history; the key printed in an error. The Liftosaur and Hevy Adapters passed unchanged (no Adapter bug found). Left for ticket 10: SKILL.md still points at the old `references/liftosaur-api.md` and `references/liftoscript.md` paths (lines 15 and 97).
