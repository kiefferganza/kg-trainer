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
  liftosaur.py playground <file> [--week N] [--day N]

The key is read from $LIFTOSAUR_API_KEY, else from <state>/liftosaur-key.
It is never printed, and a failure reports the HTTP status, not the key.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import paths  # noqa: E402

BASE = "https://www.liftosaur.com/api/v1"
EXERCISES = pathlib.Path(__file__).resolve().parent.parent / "references" / "exercises.txt"


def api_key() -> str:
    env = os.environ.get("LIFTOSAUR_API_KEY")
    if env and env.strip():
        return env.strip()
    key_file = paths.state_dir() / "liftosaur-key"
    if key_file.exists():
        value = key_file.read_text().strip()
        if value:
            return value
    sys.exit("No Liftosaur API key. Set $LIFTOSAUR_API_KEY, or save it to "
             f"{key_file} and chmod 600 it. Create one in the Liftosaur app "
             "under Settings -> API Keys. A premium subscription is required.")


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
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except json.JSONDecodeError:
            return exc.code, None


def ok(status: int, payload, what: str):
    """Unwrap {"data": ...}, or exit with the status. Never echoes the key."""
    if status >= 400:
        error = (payload or {}).get("error") or {}
        detail = " ".join(str(x) for x in (error.get("code"), error.get("message")) if x)
        hint = ""
        if status == 401:
            hint = "  (check the key, and that the subscription is active)"
        elif status == 422:
            hint = "  (invalid Liftoscript -- run program-stats or playground to locate it)"
        sys.exit(f"{what} failed: HTTP {status} {detail}{hint}".rstrip())
    return (payload or {}).get("data")


def paginate(path: str, key: str, params: dict | None = None, limit: int = 200) -> list[dict]:
    """Follow Liftosaur's cursor pagination. `limit` maxes out at 200."""
    query = dict(params or {})
    query["limit"] = limit
    out: list[dict] = []
    while True:
        data = ok(*call("GET", path, params=query), f"GET {path}") or {}
        out.extend(data.get(key, []))
        if not data.get("hasMore"):
            return out
        cursor = data.get("nextCursor")
        if not cursor:
            return out
        query["cursor"] = cursor


# --- commands -------------------------------------------------------------

def cmd_ping(_):
    data = ok(*call("GET", "/settings"), "GET /settings")
    print(json.dumps(data, indent=2))
    if (data or {}).get("units") not in ("kg", None):
        print(f"note: the account reports units={data['units']}; "
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
    data = paginate(f"/measurements/{a.key}", "values")
    print(json.dumps(data, indent=2))


def cmd_programs(_):
    print(json.dumps(ok(*call("GET", "/programs"), "GET /programs"), indent=2))


def cmd_program_get(a):
    data = ok(*call("GET", f"/programs/{a.id}"), f"GET /programs/{a.id}")
    if a.out:
        pathlib.Path(a.out).write_text(data["text"])
        print(f"wrote {a.out}  ({data['name']}, id {data['id']})")
    else:
        print(data["text"])


def cmd_program_push(a):
    text = pathlib.Path(a.file).read_text()
    if a.id:
        body = {"text": text}
        if a.name:
            body["name"] = a.name
        data = ok(*call("PUT", f"/programs/{a.id}", body=body), f"PUT /programs/{a.id}")
    else:
        if not a.name:
            sys.exit("--name is required when creating a program.")
        data = ok(*call("POST", "/programs", body={"name": a.name, "text": text}),
                  "POST /programs")
    print(json.dumps({"id": data["id"], "name": data["name"]}, indent=2))


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
    c.set_defaults(fn=cmd_playground)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
