#!/usr/bin/env python3
"""Liftosaur REST client.

Usage:
  liftosaur.py ping                        # validate the key, show account units
  liftosaur.py exercises [pattern]         # match the built-in exercise list
  liftosaur.py settings
  liftosaur.py history [--since YYYY-MM-DD] [--until YYYY-MM-DD]
  liftosaur.py measurements                # overview of tracked metrics
  liftosaur.py measurement weight          # one full series
  liftosaur.py programs
  liftosaur.py program-get <id|current> [--out FILE]
  liftosaur.py program-push <file> [--name NAME] [--id ID]
  liftosaur.py program-stats <file>
  liftosaur.py playground <file> [--week N] [--day N] [--command CMD ...]
                                           # no commands: parse check only; the
                                           # workout lists completed sets, so pass
                                           # complete_set(ex, set) to see targets
                                           # --record FILE saves the response as a fixture

The key is read from $LIFTOSAUR_API_KEY, else from a .env file in the project
directory, else from <state>/liftosaur-key. It is never printed, and a failure
reports the HTTP status, not the key.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from adapters import liftosaur as client  # noqa: E402

# The client (HTTP, pagination, programs, playground, plan compile/check) lives in the
# Liftosaur Adapter. This file is the Liftosaur extras CLI: raw reads, the playground
# simulator (Liftosaur-only) and program-* shims over the Adapter. Planning goes
# through `app.py compile|push|update|get|check`.


# --- commands -------------------------------------------------------------

def cmd_ping(_):
    data = client.ok(*client.call("GET", "/settings"), "GET /settings")
    print(json.dumps(data, indent=2))
    units = data.get("units") if isinstance(data, dict) else None
    if units not in ("kg", None):
        print(f"note: the account reports units={units}; "
              "kg-trainer works in kilograms and converts on read.", file=sys.stderr)


def cmd_settings(_):
    print(json.dumps(client.ok(*client.call("GET", "/settings"), "GET /settings"), indent=2))


def cmd_exercises(a):
    if not client.EXERCISES.exists():
        sys.exit(f"No exercise list at {client.EXERCISES}. Run scripts/fetch_reference.py.")
    names = [n.strip() for n in client.EXERCISES.read_text().splitlines() if n.strip()]
    needle = (a.pattern or "").lower()
    hits = [n for n in names if needle in n.lower()]
    if not hits:
        sys.exit(f"No built-in exercise matches {a.pattern!r}. "
                 "Pick the closest built-in name, or create a custom exercise in the app.")
    for name in hits:
        print(name)


def cmd_history(a):
    params = {}
    if a.since:
        params["startDate"] = a.since
    if a.until:
        params["endDate"] = a.until
    print(json.dumps(client.paginate("/history", "records", params=params), indent=2))


def cmd_measurements(_):
    print(json.dumps(client.ok(*client.call("GET", "/measurements"), "GET /measurements"), indent=2))


def cmd_measurement(a):
    key = urllib.parse.quote(a.key, safe="")
    data = client.paginate(f"/measurements/{key}", "values")
    print(json.dumps(data, indent=2))


def cmd_programs(_):
    print(json.dumps(client.ok(*client.call("GET", "/programs"), "GET /programs"), indent=2))


def cmd_program_get(a):
    data = client.get_plan(a.id)
    if a.out:
        pathlib.Path(a.out).write_text(data["text"])
        print(f"wrote {a.out}  ({data.get('name', '')}, id {data.get('id', a.id)})")
    else:
        print(data["text"])


def cmd_program_push(a):
    text = pathlib.Path(a.file).read_text()
    data, what = client._write_program(text, name=a.name, pid=a.id)
    print(json.dumps({"id": client._field(data, "id", what), "name": client._field(data, "name", what)},
                     indent=2))


def cmd_program_stats(a):
    print(json.dumps(client.program_stats(pathlib.Path(a.file).read_text()), indent=2))


def cmd_playground(a):
    text = pathlib.Path(a.file).read_text()
    data = client.playground(text, a.week, a.day, a.command)
    print(json.dumps(data, indent=2))
    if a.record:
        if not a.command or not any("finish_workout" in c for c in a.command):
            print("note: no finish_workout() among the commands, so the recording has "
                  "no updatedProgramText to compare progression against.", file=sys.stderr)
        record = {"recorded": dt.date.today().isoformat(), "week": a.week, "day": a.day,
                  "commands": a.command or [], "program_text": text, "response": data}
        pathlib.Path(a.record).write_text(json.dumps(record, indent=2) + "\n")
        print(f"recorded {a.record}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping").set_defaults(fn=cmd_ping)
    sub.add_parser("settings").set_defaults(fn=cmd_settings)
    c = sub.add_parser("exercises"); c.add_argument("pattern", nargs="?", default="")
    c.set_defaults(fn=cmd_exercises)
    c = sub.add_parser("history"); c.add_argument("--since"); c.add_argument("--until")
    c.set_defaults(fn=cmd_history)
    sub.add_parser("measurements").set_defaults(fn=cmd_measurements)
    c = sub.add_parser("measurement"); c.add_argument("key"); c.set_defaults(fn=cmd_measurement)
    sub.add_parser("programs").set_defaults(fn=cmd_programs)
    c = sub.add_parser("program-get"); c.add_argument("id"); c.add_argument("--out")
    c.set_defaults(fn=cmd_program_get)
    c = sub.add_parser("program-push"); c.add_argument("file")
    c.add_argument("--name"); c.add_argument("--id"); c.set_defaults(fn=cmd_program_push)
    c = sub.add_parser("program-stats"); c.add_argument("file"); c.set_defaults(fn=cmd_program_stats)
    c = sub.add_parser("playground"); c.add_argument("file")
    c.add_argument("--week", type=int); c.add_argument("--day", type=int)
    c.add_argument("--command", action="append",
                   help="simulated action, repeatable: complete_set(1, 1), finish_workout()")
    c.add_argument("--record", metavar="FILE",
                   help="also save {program_text, commands, response} as a JSON fixture")
    c.set_defaults(fn=cmd_playground)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
