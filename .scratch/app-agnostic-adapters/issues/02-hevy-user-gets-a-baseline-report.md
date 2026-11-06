# 02: Hevy user gets a baseline report

**What to build:** Introduce the Adapter Contract's read side, with Hevy as the first Adapter written against it, so the contract is not shaped by Liftosaur. A Hevy user can list the shipped Adapters with their Capabilities, choose Hevy as the Active App, ping their key, pull their full history and bodyweight, and get the same baseline report a Liftosaur user gets today. Hevy exercise titles are translated to Canonical Exercise Names through a shipped map, with a personal override in the store; anything unmatched shows up in `unclassified_exercises` rather than being guessed. `profile.json` records the Active App in an `app` block. A store still holding the old `liftosaur` block is migrated idempotently on first read.

Start from the Hevy facts recorded in the spec (restored from git history at `a9c6d08^`). Re-verify each one live with the user's Hevy Pro key before relying on it.

**Blocked by:** 01 (Shared HTTP and key handling)

**Status:** ready-for-agent

- [ ] Each Adapter declares its Capabilities; asking for an undeclared one prints its Fallback and exits non-zero
- [ ] Listing apps shows Liftosaur and Hevy with their Capabilities; choosing an app writes it to `profile.json`
- [ ] Hevy history is pulled through every page (page size 10) and becomes a Workout Log in kg with Canonical Exercise Names; pagination that does not terminate fails loudly
- [ ] Only RPE the athlete logged reaches the Workout Log
- [ ] Hevy bodyweight becomes one reading per day
- [ ] A shipped Hevy exercise map exists, built from Hevy's catalogue and reviewed; the store's personal override wins over it
- [ ] The baseline report runs on a Hevy store, with recorded-fixture tests
- [ ] The `liftosaur` → `app` profile migration is idempotent and logs once
- [ ] Live verification against the user's Hevy account is recorded with a date
