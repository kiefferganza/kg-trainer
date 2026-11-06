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
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import paths  # noqa: E402
from adapters import http as apphttp  # noqa: E402

BASE = "https://www.liftosaur.com/api/v1"
EXERCISES = pathlib.Path(__file__).resolve().parent.parent / "references" / "exercises.txt"


# Redirect refusal, key resolution and status-only errors are shared by every
# Adapter. Aliases keep the names existing importers and tests use.
_NoRedirect = apphttp.NoRedirect
_OPENER = apphttp.OPENER
_dotenv_value = apphttp.dotenv_value

_KEY_HELP = dict(
    display="Liftosaur", example="lftsk_...",
    hint="Create one in the Liftosaur app under Settings -> API Keys. "
         "A premium subscription is required.")

_HINTS = {
    401: "  (check the key, and that the subscription is active)",
    422: "  (invalid Liftoscript -- run program-stats or playground to locate it)",
}


def api_key() -> str:
    return apphttp.api_key("liftosaur", **_KEY_HELP)


def call(method: str, path: str, params: dict | None = None, body: dict | None = None):
    return apphttp.request("liftosaur", BASE, method, path, params, body, **_KEY_HELP)


def ok(status: int, payload, what: str):
    """Unwrap {"data": ...}, or exit with the status. Never echoes the key."""
    return apphttp.check(status, payload, what, _HINTS)


def _field(data, name: str, what: str):
    value = data.get(name) if isinstance(data, dict) else None
    if value is None:
        sys.exit(f"{what} failed: response has no '{name}'")
    return value


def paginate(path: str, key: str, params: dict | None = None, limit: int = 200) -> list[dict]:
    """Follow Liftosaur's cursor pagination. `limit` maxes out at 200."""
    query = dict(params or {})
    query["limit"] = limit
    out: list[dict] = []
    seen_cursors: set = set()
    while True:
        data = ok(*call("GET", path, params=query), f"GET {path}")
        if not isinstance(data, dict):
            sys.exit(f"GET {path} failed: response has no data object")
        records = data.get(key, [])
        if not isinstance(records, list):
            sys.exit(f"GET {path} failed: '{key}' is not a list")
        out.extend(records)
        if not data.get("hasMore"):
            return out
        cursor = data.get("nextCursor")
        # The live API sends a millisecond timestamp; accept a string too. Anything
        # else would end the loop with a partial history that looks complete.
        if isinstance(cursor, bool) or not isinstance(cursor, (str, int)) or cursor == "":
            sys.exit(f"GET {path} failed: hasMore with no usable nextCursor "
                     f"({len(out)} records read); refusing to return a partial list")
        if cursor in seen_cursors:
            sys.exit(f"GET {path} failed: server repeated a cursor; stopping instead of looping")
        seen_cursors.add(cursor)
        query["cursor"] = cursor


LB_TO_KG = 0.45359237
_WEIGHT_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(kg|lb)\s*")


def weigh_in(entry, tz: dt.tzinfo | None = None) -> tuple[dt.date, float] | None:
    """One /measurements/weight value as (local date, kg), or None if unreadable.

    The API sends {"timestamp": ms, "date": ISO-UTC, "value": "69.9kg"}. The day
    is taken in the local zone (or `tz`), because a morning weigh-in east of UTC
    falls on the previous UTC date. A value with no unit is refused, never assumed.
    """
    if not isinstance(entry, dict):
        return None
    stamp, value = entry.get("date"), entry.get("value")
    m = _WEIGHT_RE.fullmatch(value) if isinstance(value, str) else None
    if not m or not isinstance(stamp, str):
        return None
    try:
        when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    kg = float(m.group(1)) * (LB_TO_KG if m.group(2) == "lb" else 1.0)
    return when.astimezone(tz).date(), round(kg, 2)


# --- commands -------------------------------------------------------------

def cmd_ping(_):
    data = ok(*call("GET", "/settings"), "GET /settings")
    print(json.dumps(data, indent=2))
    units = data.get("units") if isinstance(data, dict) else None
    if units not in ("kg", None):
        print(f"note: the account reports units={units}; "
              "kg-trainer works in kilograms and converts on read.", file=sys.stderr)


def cmd_settings(_):
    print(json.dumps(ok(*call("GET", "/settings"), "GET /settings"), indent=2))


def cmd_exercises(a):
    if not EXERCISES.exists():
        sys.exit(f"No exercise list at {EXERCISES}. Run scripts/fetch_reference.py.")
    names = [n.strip() for n in EXERCISES.read_text().splitlines() if n.strip()]
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
    print(json.dumps(paginate("/history", "records", params=params), indent=2))


def cmd_measurements(_):
    print(json.dumps(ok(*call("GET", "/measurements"), "GET /measurements"), indent=2))


def cmd_measurement(a):
    key = urllib.parse.quote(a.key, safe="")
    data = paginate(f"/measurements/{key}", "values")
    print(json.dumps(data, indent=2))


def cmd_programs(_):
    print(json.dumps(ok(*call("GET", "/programs"), "GET /programs"), indent=2))


def cmd_program_get(a):
    pid = urllib.parse.quote(a.id, safe="")
    what = f"GET /programs/{pid}"
    data = ok(*call("GET", f"/programs/{pid}"), what)
    text = _field(data, "text", what)
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
        data = ok(*call("PUT", f"/programs/{pid}", body=body), what)
    else:
        if not a.name:
            sys.exit("--name is required when creating a program.")
        what = "POST /programs"
        data = ok(*call("POST", "/programs", body={"name": a.name, "text": text}), what)
    print(json.dumps({"id": _field(data, "id", what), "name": _field(data, "name", what)},
                     indent=2))


def cmd_program_stats(a):
    text = pathlib.Path(a.file).read_text()
    data = ok(*call("POST", "/program-stats", body={"programText": text}),
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
    print(json.dumps(ok(*call("POST", "/playground", body=body), "POST /playground"), indent=2))


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
