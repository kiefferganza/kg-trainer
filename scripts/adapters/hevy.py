#!/usr/bin/env python3
"""Hevy Adapter (read side): ping, pull_history, pull_bodyweight, find_exercise.

Facts (verified live 2026-09-18, re-verification pending; see
references/adapters/hevy/README.md): base https://api.hevyapp.com, header
`api-key: <key>`, Hevy Pro required, every weight in kg, `pageSize` capped at 10
(100 for exercise_templates), paginate on `page_count`.

Hevy exercise titles ("Bench Press (Barbell)") become Canonical Exercise Names
("Bench Press") through a shipped map plus a personal override in the store
(exercise-map-hevy.json, which wins). An unmatched title is kept as it is so it
lands in `unclassified_exercises`; it is never fuzzy-matched.

CLI (maintenance only; the day-to-day entry point is scripts/app.py):
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
CAPABILITIES = {"ping", "pull_history", "pull_bodyweight", "find_exercise"}

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
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
