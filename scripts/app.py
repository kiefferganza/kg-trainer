#!/usr/bin/env python3
"""The one CLI over the Active App's Adapter.

Usage:
  app.py apps                  # shipped Adapters and their Capabilities
  app.py use <name>            # make <name> the Active App (writes profile.json)
  app.py ping                  # validate the key; show account and units
  app.py pull                  # replace the local Workout Log and bodyweight cache
  app.py bodyweight            # print the app's weigh-ins, one per day
  app.py exercises <pattern>   # canonical names matching the pattern

An operation the Active App does not declare prints its Fallback and exits
non-zero. A store that has never chosen an app is a Liftosaur store.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from adapters import contract  # noqa: E402

DEFAULT_APP = "liftosaur"


def active():
    return contract.load(contract.active_app() or DEFAULT_APP)


def cmd_apps(_):
    chosen = contract.active_app()
    for adapter in contract.all_adapters():
        mark = "*" if adapter.NAME == chosen else " "
        print(f"{mark} {adapter.NAME:<12} {', '.join(contract.capabilities(adapter))}")
        missing = [op for op in contract.OPERATIONS if op not in adapter.CAPABILITIES]
        for op in missing:
            print(f"    {op}: not supported -> {contract.FALLBACKS[op]}")
    if chosen is None:
        print("\nNo Active App chosen yet: run `app.py use <name>`.")


def cmd_use(a):
    adapter = contract.load(a.name)
    previous = contract.active_app()
    contract.set_active_app(adapter.NAME)
    print(f"Active App: {adapter.NAME}")
    if previous and previous != adapter.NAME:
        print(f"Switched from {previous}. The data/ cache is replaced on the next pull; "
              "checkins.jsonl, weight-log.csv and plans/ are untouched.")


def cmd_ping(_):
    adapter = active()
    info = contract.require(adapter, "ping")()
    print(json.dumps(info, indent=2))
    if info.get("units") not in (None, "kg"):
        print(f"note: the account reports units={info['units']}; "
              "kg-trainer works in kilograms and converts on read.", file=sys.stderr)


def cmd_pull(_):
    import analyze  # lazy: it resolves the store at import
    analyze.cmd_pull(argparse.Namespace(full=True))


def cmd_bodyweight(_):
    adapter = active()
    for day, kg in contract.require(adapter, "pull_bodyweight")():
        print(f"{day.isoformat()}\t{kg}")


def cmd_exercises(a):
    adapter = active()
    hits = contract.require(adapter, "find_exercise")(a.pattern)
    if not hits:
        sys.exit(f"No exercise matches {a.pattern!r} in {adapter.NAME}.")
    for hit in hits:
        print(hit["canonical"] + (f"\t{hit['app_id']}" if hit.get("app_id") else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("apps").set_defaults(fn=cmd_apps)
    c = sub.add_parser("use"); c.add_argument("name"); c.set_defaults(fn=cmd_use)
    sub.add_parser("ping").set_defaults(fn=cmd_ping)
    sub.add_parser("pull").set_defaults(fn=cmd_pull)
    sub.add_parser("bodyweight").set_defaults(fn=cmd_bodyweight)
    c = sub.add_parser("exercises"); c.add_argument("pattern", nargs="?", default="")
    c.set_defaults(fn=cmd_exercises)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
