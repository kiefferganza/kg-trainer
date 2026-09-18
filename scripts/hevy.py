#!/usr/bin/env python3
"""Thin CLI over the Hevy public API. No dependencies beyond the stdlib.

The API key is read from $HEVY_API_KEY, or from ~/.kg-trainer/hevy-key if that
file exists. The key is never echoed.

Usage:
  hevy.py ping                             # validate the key -> user name
  hevy.py catalog [--refresh]              # cache exercise templates locally
  hevy.py find <pattern>                   # resolve exercise name -> template id
  hevy.py workouts [--pages N]             # recent logged workouts (paginated)
  hevy.py history <template_id> [--since ISO]
  hevy.py measurements [--pages N]
  hevy.py put-measurement <YYYY-MM-DD> [--weight KG] [--fat PCT] [--waist CM]
  hevy.py folders
  hevy.py new-folder <title>
  hevy.py routines [--pages N]
  hevy.py pull-routine <id> [--out FILE]   # GET, stripped to PUT-ready shape
  hevy.py push-routine <routine.json>      # POST, or PUT with --id <routineId>
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.hevyapp.com"
STATE = pathlib.Path(os.environ.get("KG_TRAINER_HOME", os.path.expanduser("~/.kg-trainer")))
CATALOG = STATE / "exercise-templates.jsonl"


def api_key() -> str:
    key = os.environ.get("HEVY_API_KEY")
    if not key:
        keyfile = STATE / "hevy-key"
        if keyfile.exists():
            key = keyfile.read_text().strip()
    if not key:
        sys.exit("No API key. Set HEVY_API_KEY or write it to ~/.kg-trainer/hevy-key")
    return key


def call(method: str, path: str, params: dict | None = None, body: dict | None = None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("api-key", api_key())
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode(errors="replace") or None)


def ok(status: int, payload, what: str):
    """Fail loudly with the API's own words. Never pretend a write succeeded."""
    if status not in (200, 201):
        hint = {
            401: "bad API key",
            403: "forbidden — needs Hevy Pro, or the account cannot create more of these",
            409: "already exists for that date — use put-measurement instead",
            404: "not found",
        }.get(status, "")
        sys.exit(f"{what} failed: HTTP {status} {hint}\n{payload}")
    return payload


def paginate(path: str, key: str, page_size: int, max_pages: int | None):
    page, out = 1, []
    while True:
        status, body = call("GET", path, {"page": page, "pageSize": page_size})
        ok(status, body, f"GET {path}")
        out.extend(body.get(key, []))
        if page >= body.get("page_count", 1) or (max_pages and page >= max_pages):
            return out
        page += 1


def cmd_ping(_):
    body = ok(*call("GET", "/v1/user/info"), "ping")
    print(json.dumps(body.get("data", body)))


def cmd_catalog(a):
    STATE.mkdir(parents=True, exist_ok=True)
    if CATALOG.exists() and not a.refresh:
        print(f"{CATALOG} already cached ({sum(1 for _ in CATALOG.open())} templates). --refresh to rebuild.")
        return
    rows = paginate("/v1/exercise_templates", "exercise_templates", 100, None)
    with CATALOG.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"cached {len(rows)} exercise templates -> {CATALOG}")


def cmd_find(a):
    if not CATALOG.exists():
        sys.exit("No catalog cached. Run: hevy.py catalog")
    pat = re.compile(a.pattern, re.I)
    hits = [json.loads(l) for l in CATALOG.open() if pat.search(json.loads(l)["title"])]
    if not hits:
        sys.exit(f"No exercise matches {a.pattern!r}. Widen the pattern before creating a custom exercise.")
    for h in hits:
        tag = "\tCUSTOM" if h.get("is_custom") else ""
        print("\t".join([h["id"], h["title"], str(h.get("equipment")),
                         str(h.get("primary_muscle_group"))]) + tag)


def cmd_workouts(a):
    print(json.dumps(paginate("/v1/workouts", "workouts", 10, a.pages), indent=2))


def cmd_history(a):
    body = ok(*call("GET", f"/v1/exercise_history/{a.template_id}",
                    {"start_date": a.since}), "exercise history")
    print(json.dumps(body.get("exercise_history", []), indent=2))


def cmd_measurements(a):
    print(json.dumps(paginate("/v1/body_measurements", "body_measurements", 10, a.pages), indent=2))


def cmd_put_measurement(a):
    fields = {"weight_kg": a.weight, "fat_percent": a.fat, "waist": a.waist}
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        sys.exit("Nothing to write — pass at least one of --weight/--fat/--waist")
    status, body = call("POST", "/v1/body_measurements", body={"date": a.date, **fields})
    if status == 409:  # the documented "already exists" case
        status, body = call("PUT", f"/v1/body_measurements/{a.date}", body=fields)
    ok(status, body, "write measurement")
    print(f"wrote {a.date}: {fields}")


def cmd_folders(_):
    print(json.dumps(paginate("/v1/routine_folders", "routine_folders", 10, None), indent=2))


def cmd_new_folder(a):
    body = ok(*call("POST", "/v1/routine_folders",
                    body={"routine_folder": {"title": a.title}}), "create folder")
    print(json.dumps(body))


def cmd_routines(a):
    print(json.dumps(paginate("/v1/routines", "routines", 10, a.pages), indent=2))


# PUT rejects fields that GET returns. Verified live: sending a routine back
# verbatim fails with 400 "Unrecognized key(s) in object: 'index'... 'title'".
SET_PUT_KEYS = {"type", "weight_kg", "reps", "rep_range",
                "distance_meters", "duration_seconds", "custom_metric"}
EX_PUT_KEYS = {"exercise_template_id", "superset_id", "rest_seconds", "notes", "sets"}


def to_put_shape(routine: dict) -> dict:
    """Strip a GET routine down to what PUT accepts, preserving everything else."""
    out = {"title": routine.get("title"), "folder_id": routine.get("folder_id"),
           "exercises": []}
    for ex in routine.get("exercises", []):
        e = {k: v for k, v in ex.items() if k in EX_PUT_KEYS}
        e["sets"] = [{k: v for k, v in s.items() if k in SET_PUT_KEYS}
                     for s in ex.get("sets", [])]
        out["exercises"].append(e)
    return {"routine": out}


def cmd_pull_routine(a):
    """Fetch a routine already stripped to PUT shape, ready to edit and push back."""
    body = ok(*call("GET", f"/v1/routines/{a.routine_id}"), "get routine")
    r = body.get("routine", body)
    if isinstance(r, list):
        r = r[0] if r else {}
    payload = to_put_shape(r)
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(payload, indent=2))
        n = len(payload["routine"]["exercises"])
        print(f"wrote PUT-ready routine ({n} exercises) -> {a.out}\n"
              f"Edit loads, then: hevy.py push-routine {a.out} --id {a.routine_id}")
    else:
        print(json.dumps(payload, indent=2))


def cmd_push_routine(a):
    payload = json.loads(pathlib.Path(a.file).read_text())
    if "routine" not in payload:
        sys.exit('Payload must be wrapped: {"routine": {...}}')
    # Validate ids against the cached catalog — the only authoritative check. A
    # blocklist of placeholder spellings does not work: "<ID:Bench Press>" is as
    # invalid as "TODO" and matches no prefix worth guessing at.
    known = set()
    if CATALOG.exists():
        known = {json.loads(l)["id"] for l in CATALOG.open() if l.strip()}
    bad = []
    for ex in payload["routine"].get("exercises", []):
        tid = ex.get("exercise_template_id") or ""
        if known:
            if tid not in known:
                bad.append(tid)
        elif not re.fullmatch(r"[0-9A-Fa-f]{8}", tid):
            bad.append(tid)
    if bad:
        sys.exit("Refusing to push — these are not real exercise template ids:\n" +
                 "\n".join(f"  {t!r}" for t in bad) +
                 "\nResolve each one with `hevy.py find <pattern>` first." +
                 ("" if known else "\nNo catalog cached — run `hevy.py catalog` for a real check."))
    if a.id:
        body = ok(*call("PUT", f"/v1/routines/{a.id}", body=payload), "update routine")
    else:
        body = ok(*call("POST", "/v1/routines", body=payload), "create routine")
    # Shapes differ per verb and the spec documents neither: POST returns
    # {"routine": {...}}, PUT returns {"routine": [{...}]}. Unwrap both.
    r = body.get("routine", body) if isinstance(body, dict) else body
    if isinstance(r, list):
        r = r[0] if r else {}
    print(json.dumps({"id": r.get("id"), "title": r.get("title")}))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping").set_defaults(fn=cmd_ping)
    c = sub.add_parser("catalog"); c.add_argument("--refresh", action="store_true"); c.set_defaults(fn=cmd_catalog)
    c = sub.add_parser("find"); c.add_argument("pattern"); c.set_defaults(fn=cmd_find)
    c = sub.add_parser("workouts"); c.add_argument("--pages", type=int, default=3); c.set_defaults(fn=cmd_workouts)
    c = sub.add_parser("history"); c.add_argument("template_id"); c.add_argument("--since"); c.set_defaults(fn=cmd_history)
    c = sub.add_parser("measurements"); c.add_argument("--pages", type=int, default=3); c.set_defaults(fn=cmd_measurements)
    c = sub.add_parser("put-measurement"); c.add_argument("date")
    c.add_argument("--weight", type=float); c.add_argument("--fat", type=float); c.add_argument("--waist", type=float)
    c.set_defaults(fn=cmd_put_measurement)
    sub.add_parser("folders").set_defaults(fn=cmd_folders)
    c = sub.add_parser("new-folder"); c.add_argument("title"); c.set_defaults(fn=cmd_new_folder)
    c = sub.add_parser("routines"); c.add_argument("--pages", type=int, default=3); c.set_defaults(fn=cmd_routines)
    c = sub.add_parser("pull-routine"); c.add_argument("routine_id"); c.add_argument("--out")
    c.set_defaults(fn=cmd_pull_routine)
    c = sub.add_parser("push-routine"); c.add_argument("file"); c.add_argument("--id"); c.set_defaults(fn=cmd_push_routine)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
