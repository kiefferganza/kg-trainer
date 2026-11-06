# 08: Dashboard plan tabs from the Canonical Plan

**What to build:** The dashboard's plan tabs come from the Canonical Plan on disk, so they work under any app, including one that cannot receive a push, and the build makes no API call. Until a store has a Canonical Plan, the tabs fall back to the newest Liftoscript plan file on disk, so an existing Liftosaur store keeps its tabs.

**Blocked by:** 04 (Canonical Plan authoring)

**Status:** ready-for-agent

- [ ] Plan tabs render one tab per week and day from a fixture Canonical Plan
- [ ] An existing Liftosaur store with only a Liftoscript plan produces the same dashboard as before
- [ ] The build makes no network call for the plan, proven by a test that fails on any request
- [ ] The output still contains no API key
