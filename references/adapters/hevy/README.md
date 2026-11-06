# Hevy Adapter

Module: `scripts/adapters/hevy.py`. Auth header `api-key`, key from `$HEVY_API_KEY`, then `.env`, then `kg-trainer-data/hevy-key`. Hevy Pro required.

## Capabilities (ticket 02, read side)

`ping`, `pull_history`, `pull_bodyweight`, `find_exercise`. Everything else falls back (see `app.py apps`).

## Exercise map

`references/adapters/hevy/exercises.json` maps Canonical Exercise Name to the Hevy title and template id. The shipped file is a small **seed** with titles only (ids null): it covers common lifts and has not been built from the live catalogue. Rebuild it with `python scripts/adapters/hevy.py build-map` (live, needs the key) or `--catalogue FILE` on a saved JSONL; exact `Name (Equipment)` matches are written, default-equipment guesses are listed for review and never written.

`kg-trainer-data/exercise-map-hevy.json` overrides the shipped map entry by entry (same shape, or `{"Canonical": "Hevy title"}`). Unmatched Hevy titles are kept as they are and show up in `unclassified_exercises`.

## Verification status

- Offline: recorded-shape fixtures only (tests/test_adapter_hevy.py).
- Live: **not yet verified**. Facts carried over from 2026-09-18 (page size 10, `page_count`, `api-key` header, `weight_kg`, logged-set `rpe`) must be re-checked with a Hevy Pro key and the date recorded here.
