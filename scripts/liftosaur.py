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

The key is read from $LIFTOSAUR_API_KEY, else from a .env file in the project
directory, else from <state>/liftosaur-key. It is never printed, and a failure
reports the HTTP status, not the key.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from adapters import liftosaur as client  # noqa: E402

# The client (HTTP, pagination, weigh-in parsing) lives in the Liftosaur Adapter;
# this file is the Liftosaur extras CLI: raw reads plus the program and playground
# commands, which move behind the Adapter Contract in a later ticket.


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
    pid = urllib.parse.quote(a.id, safe="")
    what = f"GET /programs/{pid}"
    data = client.ok(*client.call("GET", f"/programs/{pid}"), what)
    text = client._field(data, "text", what)
    if a.out:
        pathlib.Path(a.out).write_text(text)
        print(f"wrote {a.out}  ({data.get('name', '')}, id {data.get('id', a.id)})")
    else:
        print(text)


def cmd_program_push(a):
    text = pathlib.Path(a.file).read_text()
    if a.id:
        pid = urllib.parse.quote(a.id, safe="")
        body = {"text": text}
        if a.name:
            body["name"] = a.name
        what = f"PUT /programs/{pid}"
        data = client.ok(*client.call("PUT", f"/programs/{pid}", body=body), what)
    else:
        if not a.name:
            sys.exit("--name is required when creating a program.")
        what = "POST /programs"
        data = client.ok(*client.call("POST", "/programs", body={"name": a.name, "text": text}), what)
    print(json.dumps({"id": client._field(data, "id", what), "name": client._field(data, "name", what)},
                     indent=2))


def cmd_program_stats(a):
    text = pathlib.Path(a.file).read_text()
    data = client.ok(*client.call("POST", "/program-stats", body={"programText": text}),
              "POST /program-stats")
    print(json.dumps(data, indent=2))


def cmd_playground(a):
    text = pathlib.Path(a.file).read_text()
    body = {"programText": text}
    if a.week:
        body["week"] = a.week
    if a.day:
        body["day"] = a.day
    if a.command:
        body["commands"] = a.command
    print(json.dumps(client.ok(*client.call("POST", "/playground", body=body), "POST /playground"), indent=2))


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
    c.set_defaults(fn=cmd_playground)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
