# 09: Contract test suite and new-app guide

**What to build:** Adding a third app becomes a repeatable job. One shared test suite runs against every Adapter on recorded fixtures and checks the behaviour the contract promises. Each Adapter has a reference doc stating its Capabilities, its quirks and when it was last verified live. A guide explains how to scaffold a new Adapter from an app's API docs, and when it counts as done: passes the suite unchanged, has fixtures, has its reference doc. New Adapters are built in the skill repo, never in a user's store.

**Blocked by:** 05 (Send a plan to Liftosaur), 06 (Send a plan to Hevy)

**Status:** ready-for-agent

- [ ] The shared suite discovers every Adapter automatically and runs against both Liftosaur and Hevy
- [ ] The suite checks Workout Log shape, kg units, logged-only RPE, Capability declarations matching implemented operations, and key-free errors
- [ ] Liftosaur and Hevy each have a reference doc with Capabilities and a dated verification status
- [ ] The new-adapter guide exists and a dry run of following it produces a stub that the suite fails in clear, actionable ways
