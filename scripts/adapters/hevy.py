#!/usr/bin/env python3
"""Hevy Adapter: ping, pull_history, pull_bodyweight, find_exercise, compile,
push_plan, update_plan, get_plan. No `check`: Hevy has no server-side validation.

Facts (verified live 2026-09-18, re-verification pending; see
references/adapters/hevy/README.md): base https://api.hevyapp.com, header
`api-key: <key>`, Hevy Pro required, every weight in kg, `pageSize` capped at 10
(100 for exercise_templates), paginate on `page_count`.

Hevy exercise titles ("Bench Press (Barbell)") become Canonical Exercise Names
("Bench Press") through a shipped map plus a personal override in the store
(exercise-map-hevy.json, which wins). An unmatched title is kept as it is so it
lands in `unclassified_exercises`; it is never fuzzy-matched.

Hevy has no weeks and no progression, so `compile` emits one week of the Canonical
Plan (default: its `current_week`), one routine per day. `push_plan` creates one
routine folder (its id is the plan id) and a routine per day; `update_plan` PUTs
each routine in that folder, matched by day name. Hevy has no DELETE, so the CLI
asks before every write (scripts/app.py). The write path is UNVERIFIED against the
live API (no Hevy Pro key): it is tested on fixtures only.

CLI (maintenance only; the day-to-day entry point is scripts/app.py):
  hevy.py sync-catalogue
      Cache Hevy's exercise catalogue (read-only) so `compile` can turn a mapped
      Hevy title into a template id without the network.
  hevy.py build-map [--catalogue FILE] [--out FILE]
      Build the shipped exercise map from Hevy's exercise catalogue. Without
      --catalogue it reads the live catalogue (needs the key). Exact
      "Name (Equipment)" matches are written; guesses that depend on a default
      equipment are listed for review and never written.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import paths  # noqa: E402
from adapters import http as apphttp  # noqa: E402

NAME = "hevy"
CAPABILITIES = {"ping", "pull_history", "pull_bodyweight", "find_exercise",
                "compile", "push_plan", "update_plan", "get_plan"}

BASE = "https://api.hevyapp.com"
PAGE_SIZE = 10
MAX_PAGES = 2000  # 20,000 workouts; a larger page_count is a server fault, not a history
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SHIPPED_MAP = ROOT / "references" / "adapters" / "hevy" / "exercises.json"
MUSCLES = ROOT / "references" / "exercise-muscles.json"

_KEY_HELP = dict(
    display="Hevy", example="<uuid>",
    hint="Get one at https://hevy.com/settings?developer. Hevy Pro is required.")
_HINTS = {
    401: "  (check the key)",
    403: "  (the key was refused: Hevy Pro is required, or the account hit a limit)",
}


def personal_map_path() -> pathlib.Path:
    return paths.state_dir() / "exercise-map-hevy.json"


def _get(path: str, params: dict | None = None):
    what = f"GET {path}"
    status, payload = apphttp.request(NAME, BASE, "GET", path, params,
                                      auth=apphttp.header_auth("api-key"), **_KEY_HELP)
    return apphttp.check(status, payload, what, _HINTS, unwrap=False)


def paginate(path: str, key: str, page_size: int = PAGE_SIZE) -> list[dict]:
    """Every record of a page_count-paginated list, or exit.

    Fails loudly instead of returning a partial list that looks complete: a
    missing or absurd page_count, a page that comes back empty or out of order,
    a page_count that moves while reading, or the same record twice (a workout
    logged mid-pull shifts the pages).
    """
    out: list[dict] = []
    page, total = 1, None
    while True:
        body = _get(path, {"page": page, "pageSize": page_size})
        count = body.get("page_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            sys.exit(f"GET {path} failed: no usable page_count; refusing to guess "
                     f"({len(out)} records read)")
        if count > MAX_PAGES:
            sys.exit(f"GET {path} failed: page_count {count} is implausible; stopping")
        if total is not None and count != total:
            sys.exit(f"GET {path} failed: page_count changed from {total} to {count} "
                     "while reading; run the pull again")
        total = count
        records = body.get(key)
        if not isinstance(records, list):
            sys.exit(f"GET {path} failed: '{key}' is not a list")
        echoed = body.get("page")
        if echoed is not None and echoed != page:
            sys.exit(f"GET {path} failed: asked for page {page}, got page {echoed}")
        if total == 0:
            return out
        if not records:
            sys.exit(f"GET {path} failed: page {page} of {total} was empty; "
                     f"refusing to return a partial list ({len(out)} records read)")
        out.extend(records)
        if page >= total:
            return out
        page += 1


# --- exercise translation --------------------------------------------------

def _entry(value) -> dict | None:
    """A map value is a title string or {"title", "id"}."""
    if isinstance(value, str) and value.strip():
        return {"title": value.strip(), "id": None}
    if isinstance(value, dict) and (value.get("title") or value.get("id")):
        return {"title": value.get("title"), "id": value.get("id")}
    return None


def _read_map(path: pathlib.Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError:
        sys.exit(f"{path} is not valid JSON")
    if isinstance(data, dict) and isinstance(data.get("exercises"), dict):
        data = data["exercises"]
    if not isinstance(data, dict):
        sys.exit(f"{path} must be a JSON object of canonical name -> Hevy title or "
                 '{"title", "id"}')
    out = {}
    for canonical, value in data.items():
        if canonical in ("schema", "note"):
            continue
        entry = _entry(value)
        if entry is None:
            sys.exit(f"{path}: entry for {canonical!r} needs a Hevy title or id")
        out[canonical] = entry
    return out


def exercise_map() -> dict[str, dict]:
    """canonical name -> {"title", "id"}: the shipped map, personal entries winning."""
    merged = _read_map(SHIPPED_MAP)
    merged.update(_read_map(personal_map_path()))
    return merged


def translator(table: dict[str, dict] | None = None):
    """A function Hevy (template id, title) -> Canonical Exercise Name, or the
    title unchanged when nothing maps it."""
    table = exercise_map() if table is None else table
    by_id: dict[str, str] = {}
    by_title: dict[str, str] = {}
    for canonical, entry in table.items():
        if entry.get("id"):
            by_id[str(entry["id"]).upper()] = canonical
        if entry.get("title"):
            by_title[entry["title"].strip().lower()] = canonical

    def translate(template_id, title: str) -> str:
        if template_id and str(template_id).upper() in by_id:
            return by_id[str(template_id).upper()]
        return by_title.get((title or "").strip().lower(), title)

    return translate


# --- contract operations ---------------------------------------------------

def ping() -> dict:
    status, payload = apphttp.request(NAME, BASE, "GET", "/v1/user/info",
                                      auth=apphttp.header_auth("api-key"), **_KEY_HELP)
    data = apphttp.check(status, payload, "GET /v1/user/info", _HINTS)
    if not isinstance(data, dict):
        sys.exit("GET /v1/user/info failed: response has no data object")
    account = data.get("name") or data.get("id")
    if not account:
        sys.exit("GET /v1/user/info failed: response names no account")
    return {"account": str(account), "units": "kg"}


def _utc(stamp) -> str | None:
    if not isinstance(stamp, str):
        return None
    try:
        when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _set(raw: dict) -> dict | None:
    weight, reps = raw.get("weight_kg"), raw.get("reps")
    seconds = raw.get("duration_seconds")
    if weight is None and reps is None and seconds is None:
        return None
    rpe = raw.get("rpe")
    return {"type": raw.get("type") or "normal", "reps": reps, "reps_left": None,
            "amrap": False,
            "weight_kg": round(weight, 2) if isinstance(weight, (int, float)) else None,
            "rpe": rpe,
            # Hevy sets in a logged workout carry only RPE the athlete entered.
            "rpe_logged": rpe is not None,
            "duration": f"{int(seconds)}s" if isinstance(seconds, (int, float)) else None}


def workout_record(raw: dict, translate) -> dict | None:
    start = _utc(raw.get("start_time"))
    if start is None:
        return None
    end = _utc(raw.get("end_time"))
    duration = None
    if end:
        parse = lambda s: dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        duration = max(0, int((parse(end) - parse(start)).total_seconds()))
    exercises = []
    for ex in raw.get("exercises") or []:
        sets = [s for s in (_set(x) for x in ex.get("sets") or []) if s]
        exercises.append({"title": translate(ex.get("exercise_template_id"),
                                             ex.get("title") or ""),
                          "target": None, "sets": sets,
                          "note": (ex.get("notes") or "").strip() or None})
    return {"start_time": start, "program": None, "day_name": raw.get("title"),
            "week": None, "day_in_week": None, "duration_seconds": duration,
            "note": (raw.get("description") or "").strip() or None,
            "exercises": exercises, "id": raw.get("id")}


def pull_history() -> list[dict]:
    """The full Workout Log, newest first: kg, Canonical Exercise Names."""
    raw = paginate("/v1/workouts", "workouts")
    ids = [w.get("id") for w in raw if w.get("id")]
    if len(ids) != len(set(ids)):
        sys.exit("GET /v1/workouts failed: the same workout came back twice (history "
                 "changed while reading); run the pull again")
    translate = translator()
    records = []
    for w in raw:
        record = workout_record(w, translate)
        if record is None:
            sys.exit(f"GET /v1/workouts failed: workout {w.get('id')!r} has no readable "
                     "start_time; refusing to drop it silently")
        records.append(record)
    return sorted(records, key=lambda r: r["start_time"], reverse=True)


def pull_bodyweight() -> list[tuple[dt.date, float]]:
    """[(date, kg)], oldest first, one per day. Hevy keys measurements by date;
    if a day somehow repeats, the most recently entered reading wins."""
    best: dict[dt.date, tuple] = {}
    for m in paginate("/v1/body_measurements", "body_measurements"):
        kg = m.get("weight_kg")
        if isinstance(kg, bool) or not isinstance(kg, (int, float)):
            continue
        try:
            day = dt.date.fromisoformat(str(m.get("date"))[:10])
        except ValueError:
            continue
        rank = (str(m.get("created_at") or ""), m.get("id") or 0)
        if day not in best or rank > best[day][0]:
            best[day] = (rank, round(float(kg), 2))
    return [(d, kg) for d, (_, kg) in sorted(best.items())]


def find_exercise(pattern: str) -> list[dict]:
    """Canonical names in the exercise map containing `pattern` (case-insensitive),
    with their Hevy template id when the map knows it. The map is the whole
    answer here: nothing is fuzzy-matched."""
    needle = (pattern or "").lower()
    return [{"canonical": c, "app_id": entry.get("id")}
            for c, entry in sorted(exercise_map().items()) if needle in c.lower()]


# --- compiling a Canonical Plan -------------------------------------------

SET_TYPE = {"working": "normal", "warmup": "warmup", "failure": "failure", "drop": "dropset"}
# What PUT /v1/routines/{id} accepts. GET also returns exercise `index` and `title`
# and set `index`, which PUT rejects.
SET_PUT_KEYS = ("type", "weight_kg", "reps", "rep_range", "distance_meters",
                "duration_seconds", "custom_metric")
EX_PUT_KEYS = ("exercise_template_id", "superset_id", "rest_seconds", "notes")
_LIMIT_403 = ("Hevy refused with 403: the account's routine limit is reached "
              "(or the key lacks Hevy Pro).")


def catalogue_path() -> pathlib.Path:
    return paths.state_dir() / "hevy-exercise-templates.jsonl"


def _catalogue_ids() -> dict[str, list[str]]:
    """lowercased Hevy title -> template ids, from the cached catalogue (may be empty)."""
    path = catalogue_path()
    out: dict[str, list[str]] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            t = json.loads(line)
        except ValueError:
            sys.exit(f"{path} is not valid JSON lines; run `hevy.py sync-catalogue` again")
        if t.get("id") and t.get("title"):
            out.setdefault(t["title"].strip().lower(), []).append(str(t["id"]))
    return out


def resolve_template(canonical: str, table: dict, catalogue: dict) -> tuple[str | None, str]:
    """(template id, "") or (None, why not). Exact only: a map id, else the map's
    Hevy title found exactly once in the cached catalogue."""
    entry = table.get(canonical)
    if entry is None:
        return None, "not in the Hevy exercise map"
    if entry.get("id"):
        return str(entry["id"]), ""
    title = (entry.get("title") or "").strip().lower()
    ids = catalogue.get(title, [])
    if len(ids) == 1:
        return ids[0], ""
    if len(ids) > 1:
        return None, (f"Hevy title {entry['title']!r} is ambiguous in the catalogue; "
                      "set its id in exercise-map-hevy.json")
    return None, (f"no template id (map title {entry.get('title')!r} not in the cached catalogue)"
                  if catalogue else "no template id and no cached catalogue")


def _native_refusals(plan: dict) -> list[str]:
    blocks = [("progression", plan.get("progression") or {})]
    blocks += [(f"week {w.get('number')} progression", w.get("progression") or {})
               for w in plan.get("weeks", [])]
    return [f"{name}: {where} carries a Native Rule for {rule['native']!r}"
            for where, block in blocks for name, rule in block.items()
            if isinstance(rule, dict) and "native" in rule and rule["native"] != NAME]


def _rep_range(plan: dict, week: dict, exercise: str, kind: str):
    if kind != "working":
        return None
    from plan import effective_rule  # lazy: plan.py pulls in the analysis modules
    rule = effective_rule(plan, week, exercise)
    if isinstance(rule, dict) and rule.get("rule") == "double":
        return {"start": rule["low"], "end": rule["high"]}
    return None


def _notes(ex: dict) -> str | None:
    parts = []
    if ex.get("cue"):
        parts.append(ex["cue"].strip())
    rir = ex.get("rir_target")
    if rir is not None:
        parts.append(f"Target: {rir:g} RIR.")
    rpes = sorted({s["rpe"] for s in ex["sets"] if s.get("rpe") is not None})
    if rpes:
        parts.append("RPE " + "/".join(f"{r:g}" for r in rpes) + ".")
    return " ".join(parts) or None


def compile(plan: dict, week: int | None = None) -> dict:
    """{"week": n, "routines": [{"routine": {...}}, ...]}: one routine per day of
    one week (default: the plan's current_week). Local, no network. Refuses, before
    producing anything, a plan with another app's Native Rule or an exercise with no
    Hevy template id. A working set under a `double` rule is written as a rep_range."""
    number = plan.get("current_week") if week is None else week
    wk = next((w for w in plan.get("weeks", []) if w.get("number") == number), None)
    if wk is None:
        have = [w.get("number") for w in plan.get("weeks", [])]
        sys.exit(f"The plan has no week {number}; its weeks are {have}")
    refusals = _native_refusals(plan)
    if refusals:
        sys.exit("Hevy cannot compile a Native Rule written for another app:\n  "
                 + "\n  ".join(refusals)
                 + "\nChange it to linear/double/reps_threshold/none, or remove it.")
    titles = [d["name"] for d in wk["days"]]
    dupes = sorted({t for t in titles if titles.count(t) > 1})
    if dupes:
        sys.exit(f"Day names must be unique to push to Hevy (update matches by name): {dupes}")
    table, catalogue = exercise_map(), _catalogue_ids()
    unmatched: dict[str, str] = {}
    routines = []
    for day in wk["days"]:
        labels: dict = {}
        exercises = []
        for ex in day["exercises"]:
            tid, why = resolve_template(ex["exercise"], table, catalogue)
            if tid is None:
                unmatched.setdefault(ex["exercise"], why)
            sup = ex.get("superset")
            if sup is not None:
                labels.setdefault(sup, len(labels))
            sets = []
            for s in ex["sets"]:
                rng = _rep_range(plan, wk, ex["exercise"], s["kind"])
                sets.append({"type": SET_TYPE[s["kind"]], "weight_kg": s.get("load_kg"),
                             "reps": None if rng else s["reps"], "rep_range": rng})
            exercises.append({
                "exercise_template_id": tid,
                "superset_id": None if sup is None else labels[sup],
                "rest_seconds": ex.get("rest_seconds"),
                "notes": _notes(ex),
                "sets": sets})
        routines.append({"routine": {"title": day["name"], "folder_id": None,
                                     "exercises": exercises}})
    if unmatched:
        sys.exit("No Hevy exercise template for these; nothing was compiled. Create each as a "
                 "custom exercise in Hevy, then add its id to exercise-map-hevy.json in the "
                 "store (or run `hevy.py sync-catalogue` if it only needs the catalogue):\n  "
                 + "\n  ".join(f"{n}: {w}" for n, w in sorted(unmatched.items())))
    return {"week": number, "routines": routines}


def to_put_shape(routine: dict) -> dict:
    """Strip a routine (compiled, or as GET returned it) to what PUT accepts."""
    out = {"title": routine.get("title"), "folder_id": routine.get("folder_id"),
           "exercises": []}
    for ex in routine.get("exercises") or []:
        e = {k: ex[k] for k in EX_PUT_KEYS if k in ex}
        e["sets"] = [{k: s[k] for k in SET_PUT_KEYS if k in s} for s in ex.get("sets") or []]
        out["exercises"].append(e)
    return {"routine": out}


# --- talking to Hevy: writes ----------------------------------------------

def _send(method: str, path: str, body: dict | None = None):
    return apphttp.request(NAME, BASE, method, path, None, body,
                           auth=apphttp.header_auth("api-key"), **_KEY_HELP)


def _routine_of(payload) -> dict | None:
    """POST/GET wrap one routine as {"routine": {...}}; PUT wraps a one-element array."""
    r = payload.get("routine") if isinstance(payload, dict) else None
    if isinstance(r, list):
        r = r[0] if r else None
    return r if isinstance(r, dict) else None


def _stop(what: str, status: int, payload, done: list[str], hint: str = ""):
    detail = ""
    if isinstance(payload, dict):
        err = payload.get("error")
        detail = err if isinstance(err, str) else ""
        if isinstance(err, dict):
            detail = " ".join(str(x) for x in (err.get("code"), err.get("message")) if x)
    state = ("Already written to Hevy (it has no delete; remove these in the app if unwanted):\n  "
             + "\n  ".join(done)) if done else "Nothing had been written to Hevy before this."
    sys.exit(f"{what} failed: HTTP {status} {detail}".rstrip() + (f" {hint}" if hint else "")
             + f"\nThe operation is NOT complete. {state}")


def _need_ids(compiled: dict):
    bad = [r["routine"]["title"] for r in compiled["routines"]
           for e in r["routine"]["exercises"] if not e.get("exercise_template_id")]
    if bad:
        sys.exit("Refusing to write: compiled routines have exercises with no Hevy template id "
                 f"({sorted(set(bad))}). Nothing was written.")


def push_plan(compiled: dict, name: str) -> str:
    """Create a routine folder called `name`, then one routine per day in it.
    Returns the folder id as a string. Stops loudly, listing exactly what was
    already created, on any failure."""
    _need_ids(compiled)
    status, payload = _send("POST", "/v1/routine_folders", {"routine_folder": {"title": name}})
    folder = payload.get("routine_folder") if isinstance(payload, dict) else None
    if status >= 300 or not isinstance(folder, dict) or folder.get("id") is None:
        _stop("POST /v1/routine_folders", status, payload, [])
    folder_id = folder["id"]
    done = [f"folder {name!r} (id {folder_id})"]
    for item in compiled["routines"]:
        routine = {**item["routine"], "folder_id": folder_id}
        title = routine["title"]
        status, payload = _send("POST", "/v1/routines", {"routine": routine})
        made = _routine_of(payload)
        if status >= 300 or not made or made.get("id") is None:
            _stop(f"POST /v1/routines ({title!r})", status, payload, done,
                  _LIMIT_403 if status == 403 else "")
        done.append(f"routine {title!r} (id {made['id']})")
    return str(folder_id)


def get_plan(plan_id: str) -> dict:
    """{"folder": {...}, "routines": [...]} for a folder id, or for "current" the
    folder recorded in profile.json."""
    if plan_id == "current":
        from adapters import contract
        app = (contract.load_profile() or {}).get("app")
        plan_id = app.get("program_id") if isinstance(app, dict) else None
        if not plan_id:
            sys.exit("No current Hevy plan: nothing has been pushed from this store yet.")
    try:
        fid = int(plan_id)
    except (TypeError, ValueError):
        sys.exit(f"A Hevy plan id is the numeric folder id, got {plan_id!r}")
    status, payload = _send("GET", f"/v1/routine_folders/{fid}")
    folder = payload.get("routine_folder") if isinstance(payload, dict) else None
    if status >= 300 or not isinstance(folder, dict):
        _stop(f"GET /v1/routine_folders/{fid}", status, payload, [])
    routines = [r for r in paginate("/v1/routines", "routines") if r.get("folder_id") == fid]
    return {"folder": folder, "routines": routines}


def update_plan(compiled: dict, plan_id: str) -> str:
    """PUT each compiled routine over the routine in folder `plan_id` with the same
    title. Checks every day has exactly one match before writing anything."""
    _need_ids(compiled)
    existing = get_plan(plan_id)["routines"]
    by_title: dict[str, list[dict]] = {}
    for r in existing:
        by_title.setdefault(r.get("title"), []).append(r)
    missing = [i["routine"]["title"] for i in compiled["routines"]
               if len(by_title.get(i["routine"]["title"], [])) != 1]
    if missing:
        sys.exit("Cannot update: each day needs exactly one routine of the same name in "
                 f"Hevy folder {plan_id}; no unique match for {missing}. Nothing was written. "
                 "Push again for a new folder, or rename the routines in Hevy.")
    done = []
    for item in compiled["routines"]:
        title = item["routine"]["title"]
        target = by_title[title][0]
        body = to_put_shape({**item["routine"], "folder_id": target.get("folder_id")})
        status, payload = _send("PUT", f"/v1/routines/{target['id']}", body)
        if status >= 300 or not _routine_of(payload):
            _stop(f"PUT /v1/routines/{target['id']} ({title!r})", status, payload,
                  [f"updated routine {t!r}" for t in done],
                  _LIMIT_403 if status == 403 else "")
        done.append(title)
    return str(plan_id)


def _cmd_sync_catalogue(_):
    templates = paginate("/v1/exercise_templates", "exercise_templates", page_size=100)
    out = catalogue_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(t) + "\n" for t in templates))
    print(f"{len(templates)} exercise templates cached -> {out}")


# --- building the shipped map ---------------------------------------------

EQUIPMENT_SUFFIX = {
    "barbell": "Barbell", "dumbbell": "Dumbbell", "cable": "Cable",
    "smith machine": "Smith Machine", "kettlebell": "Kettlebell",
    "band": "Band", "resistance band": "Band", "ez bar": "EZ Bar",
    "ez-bar": "EZ Bar", "trap bar": "Trap Bar", "machine": "Leverage Machine",
    "bodyweight": "Bodyweight",
}


def build_map(templates: list[dict], canonical_names: set[str]):
    """(map, review) from Hevy exercise templates.

    A title "Base (Equipment)" is written to the map only when "Base, Suffix" is a
    Canonical Exercise Name. A title that matches a bare canonical name is listed
    for review instead (the bare name means that exercise's default equipment,
    which this code cannot know), as is a title nothing matches.
    """
    mapped: dict[str, dict] = {}
    review: list[dict] = []
    for t in sorted(templates, key=lambda t: t.get("title") or ""):
        title, tid = t.get("title") or "", t.get("id")
        if not title or t.get("is_custom"):
            continue
        base, equip = title, None
        if title.endswith(")") and " (" in title:
            base, _, rest = title[:-1].partition(" (")
            equip = EQUIPMENT_SUFFIX.get(rest.strip().lower())
        candidate = f"{base}, {equip}" if equip else base
        if candidate in canonical_names and candidate not in mapped:
            mapped[candidate] = {"title": title, "id": tid}
        elif base in canonical_names:
            review.append({"title": title, "id": tid, "proposed": base,
                           "why": "bare canonical name = default equipment; confirm"})
        else:
            review.append({"title": title, "id": tid, "proposed": None,
                           "why": "no canonical name"})
    return mapped, review


def _cmd_build_map(a):
    if a.catalogue:
        templates = [json.loads(l) for l in pathlib.Path(a.catalogue).read_text().splitlines()
                     if l.strip()]
    else:
        templates = paginate("/v1/exercise_templates", "exercise_templates", page_size=100)
    names = set(json.loads(MUSCLES.read_text()))
    mapped, review = build_map(templates, names)
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"schema": 1, "note": "Built by `hevy.py build-map`; review before committing.",
         "exercises": dict(sorted(mapped.items()))}, indent=2) + "\n")
    print(f"{len(mapped)} exercises mapped -> {out}; {len(review)} need review "
          "(listed below, NOT written):")
    for r in review:
        print(f"  {r['title']}\t{r['id']}\t-> {r['proposed']}\t({r['why']})")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("build-map")
    c.add_argument("--catalogue", help="JSONL of exercise templates; default: fetch live")
    c.add_argument("--out", default=str(SHIPPED_MAP))
    c.set_defaults(fn=_cmd_build_map)
    sub.add_parser("sync-catalogue").set_defaults(fn=_cmd_sync_catalogue)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
