---
status: accepted
---

# Native progression stays in the app; the Canonical Plan records it and guards updates

On Liftosaur both the app and `plan.py progress` can move a plan's weights, and if both act the copies drift, so the next `app.py update` silently overwrites what the app progressed. We let the app keep progressing natively, since in-app progression without the coach was the reason for the Liftosaur migration. `plan.py progress` mirrors the app's semantics, verified live: a change applies to that exercise in every later week, deloads included. Before every update, a drift guard refuses to write when the app's current week differs from the Canonical Plan. We rejected two alternatives. Turning native progression off (`progress: none` everywhere, with the skill pushing every week) is uniform but makes the coach a weekly dependency. Letting the app progress with no Canonical record is simplest, but makes every later update destructive.
