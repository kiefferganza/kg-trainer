# 01: Shared HTTP and key handling

**What to build:** Prefactor only, with no behaviour change. Pull the parts of the Liftosaur client that every Adapter will need into one shared place: requests that refuse redirects, per-app API key resolution (`$<APP>_API_KEY`, then the nearest `.env`, then the store's `<app>-key` file), and failures that report the operation and HTTP status but never the key. The existing Liftosaur client uses it, and every current command behaves exactly as before.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Key resolution takes the app name and works for any app, with the same precedence as today
- [ ] Redirects are refused for every app, so a key is never re-sent to another host
- [ ] Every failure message names the operation and HTTP status, and a test proves the key never appears in output
- [ ] `.gitignore` ignores any `<app>-key` file, not only `liftosaur-key`
- [ ] The full existing test suite passes unchanged
