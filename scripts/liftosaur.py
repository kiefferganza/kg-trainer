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
import http.client
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import paths  # noqa: E402

BASE = "https://www.liftosaur.com/api/v1"
EXERCISES = pathlib.Path(__file__).resolve().parent.parent / "references" / "exercises.txt"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib re-sends the Authorization header on redirects, even to another host.
    Refuse every redirect instead: the redirect status surfaces as an HTTP error."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Module-private on purpose: installing it globally would change urllib for every
# importer of this module (dashboard.py downloads through plain urlopen).
_OPENER = urllib.request.build_opener(_NoRedirect)


def _dotenv_value(name: str) -> str | None:
    """Read one variable from the nearest .env, searching the cwd then its parents.

    Nothing in the standard library reads .env, so this is a deliberately small
    parser: `KEY=VALUE` lines, optional surrounding quotes, `#` comments and blank
    lines skipped. The walk continues past a .env that does not define `name`, so
    running from a nested directory (a git worktree, say) still finds the project
    root's file. An exported variable always wins over .env -- see api_key().
    """
    try:
        start = pathlib.Path.cwd().resolve()
    except OSError:
        return None
    for directory in (start, *start.parents):
        env_file = directory / ".env"
        if not env_file.is_file():
            continue
        try:
            text = env_file.read_text()
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != name:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            value = value.strip()
            if value:
                return value
    return None


def api_key() -> str:
    env = os.environ.get("LIFTOSAUR_API_KEY")
    if env and env.strip():
        return env.strip()
    from_dotenv = _dotenv_value("LIFTOSAUR_API_KEY")
    if from_dotenv:
        return from_dotenv
    key_file = paths.state_dir() / "liftosaur-key"
    if key_file.exists():
        value = key_file.read_text().strip()
        if value:
            return value
    sys.exit("No Liftosaur API key. Set $LIFTOSAUR_API_KEY, or put "
             "LIFTOSAUR_API_KEY=lftsk_... in a .env file in the project directory "
             f"(keep it out of git), or save the key to {key_file} and chmod 600 it. "
             "Create one in the Liftosaur app under Settings -> API Keys. "
             "A premium subscription is required.")


def call(method: str, path: str, params: dict | None = None, body: dict | None = None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {api_key()}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=60) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        try:
            status, raw = exc.code, exc.read()
        except (OSError, http.client.HTTPException):
            return exc.code, None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        sys.exit(f"{method} {path} failed: network error ({type(exc).__name__})")
    try:
        return status, json.loads(raw or b"null")
    except ValueError as exc:
        if status >= 300:
            return status, None
        sys.exit(f"{method} {path} failed: HTTP {status}, response was not JSON "
                 f"({type(exc).__name__})")


def ok(status: int, payload, what: str):
    """Unwrap {"data": ...}, or exit with the status. Never echoes the key."""
    if status >= 300:
        error = payload.get("error") if isinstance(payload, dict) else None
        error = error if isinstance(error, dict) else {}
        detail = " ".join(str(x) for x in (error.get("code"), error.get("message")) if x)
        hint = ""
        if status == 401:
            hint = "  (check the key, and that the subscription is active)"
        elif status == 422:
            hint = "  (invalid Liftoscript -- run program-stats or playground to locate it)"
        elif status < 400:
            hint = "  (redirect not followed: the API moved or a proxy answered; the key was not forwarded)"
        sys.exit(f"{what} failed: HTTP {status} {detail}{hint}".rstrip())
    if not isinstance(payload, dict):
        sys.exit(f"{what} failed: response was not a JSON object")
    return payload.get("data")


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
        # The live API sends a millisecond timestamp; accept a string too.
        if isinstance(cursor, bool) or not isinstance(cursor, (str, int)) or cursor == "":
            return out
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
