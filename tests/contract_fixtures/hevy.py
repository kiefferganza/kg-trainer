"""Fixture provider for the Hevy Adapter (see tests/test_contract.py).

Hand-built responses shaped like the documented Hevy API (no Hevy Pro key, so
nothing here is recorded live). test_adapter_hevy.py and test_hevy_write.py import
their workout, measurement and catalogue data from here so there is one copy.
"""

import json
import pathlib
import re

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def _set(i, kg, reps, rpe=None, kind="normal"):
    return {"index": i, "type": kind, "weight_kg": kg, "reps": reps, "rpe": rpe,
            "distance_meters": None, "duration_seconds": None, "custom_metric": None}


def _workout(wid, start, exercises, title="Upper A"):
    return {"id": wid, "title": title, "description": "", "start_time": start,
            "end_time": start.replace("T10", "T11"), "exercises": exercises}


BENCH = {"index": 0, "title": "Bench Press (Barbell)", "exercise_template_id": "AAAA0001",
         "notes": "", "sets": [_set(0, 60, 8, kind="warmup"), _set(1, 80, 8, rpe=8.5),
                               _set(2, 80, 7)]}
CURL = {"index": 1, "title": "Zottman Curl (Dumbbell)", "exercise_template_id": "BBBB0002",
        "notes": "", "sets": [_set(0, 14, 10)]}
PULLDOWN = {"index": 0, "title": "Lat Pulldown (Cable)", "exercise_template_id": "CCCC0003",
            "notes": "", "sets": [_set(0, 60, 10, rpe=7)]}

WORKOUTS = [  # 3 workouts, newest first, pageSize 10 -> page_count 1 normally
    _workout("w3", "2026-10-06T10:00:00+00:00", [PULLDOWN]),
    _workout("w2", "2026-10-02T10:00:00+00:00", [BENCH, CURL]),
    _workout("w1", "2026-09-28T10:00:00+00:00", [BENCH]),
]

MEASUREMENTS = [
    {"id": 3, "date": "2026-10-05", "weight_kg": 80.4, "created_at": "2026-10-05T07:00:00Z"},
    {"id": 2, "date": "2026-10-04", "weight_kg": None, "created_at": "2026-10-04T07:00:00Z"},
    {"id": 1, "date": "2026-10-03", "weight_kg": 80.9, "created_at": "2026-10-03T07:00:00Z"},
    {"id": 0, "date": "2026-10-03", "weight_kg": 81.1, "created_at": "2026-10-03T06:00:00Z"},
]

CATALOGUE = [
    {"id": "AAAA0001", "title": "Bench Press (Barbell)"},
    {"id": "AAAA0002", "title": "Squat (Barbell)"},
    {"id": "AAAA0003", "title": "Lat Pulldown (Cable)"},
    {"id": "AAAA0004", "title": "Bicep Curl (Cable)"},
    {"id": "AAAA0005", "title": "Pull Up"},
]

PLAN = json.loads((FIX / "hevy_plan.json").read_text())

FOLDER = {"routine_folder": {"id": 3670546, "index": 0, "title": "Block"}}


def workout_pages(workouts, per=10):
    chunks = [workouts[i:i + per] for i in range(0, len(workouts), per)] or [[]]
    return [{"page": i + 1, "page_count": len(chunks), "workouts": c}
            for i, c in enumerate(chunks)]


def get_shape(rid, title, folder=3670546):
    """A routine as GET returns it: with exercise/set `index` and exercise `title`."""
    return {"id": rid, "title": title, "folder_id": folder, "created_at": "x", "updated_at": "y",
            "exercises": [{"index": 0, "title": "Squat (Barbell)", "exercise_template_id": "AAAA0002",
                           "rest_seconds": 60, "notes": "n", "superset_id": None,
                           "sets": [{"index": 0, "type": "normal", "weight_kg": 1, "reps": 1,
                                     "rep_range": None}]}]}


EXPECT = {
    "history": {
        "count": 3,
        "unclassified": {"Zottman Curl (Dumbbell)"},
        "weights_kg": [("2026-10-02T10:00:00Z", "Bench Press", 1, 80.0),
                       ("2026-10-06T10:00:00Z", "Lat Pulldown", 0, 60.0)],
        "rpe": [("2026-10-02T10:00:00Z", "Bench Press", 1, True, 8.5),
                ("2026-10-02T10:00:00Z", "Bench Press", 2, False, None)],
    },
    "bodyweight": {"2026-10-03": 80.9, "2026-10-05": 80.4},
    "find": ("curl", "Bicep Curl, Cable"),
    "plan_id": "3670546",     # an id the fixtures can update and get
}


def setup(store):
    """Prepare the store: the cached exercise catalogue `compile` reads."""
    (store / "hevy-exercise-templates.jsonl").write_text(
        "".join(json.dumps(t) + "\n" for t in CATALOGUE))


def _page(items, key, query):
    return {"page": int(query.get("page", 1)), "page_count": 1 if items else 0, key: items}


def respond(method, path, query, body):
    """(status, body) for one request, or None when no route is recorded."""
    if (method, path) == ("GET", "/v1/user/info"):
        return 200, {"data": {"id": "u1", "name": "Kieffer", "url": "x"}}
    if (method, path) == ("GET", "/v1/workouts"):
        pages = workout_pages(WORKOUTS)
        return 200, pages[int(query["page"]) - 1]
    if (method, path) == ("GET", "/v1/body_measurements"):
        return 200, _page(MEASUREMENTS, "body_measurements", query)
    if (method, path) == ("POST", "/v1/routine_folders"):
        return 201, FOLDER
    if (method, path) == ("POST", "/v1/routines"):
        return 201, {"routine": {"id": f"r-{body['routine']['title']}", "title": "x"}}
    if (method, path) == ("GET", "/v1/routine_folders/3670546"):
        return 200, FOLDER
    if (method, path) == ("GET", "/v1/routines"):
        return 200, _page([get_shape("r1", "Upper A"), get_shape("r2", "Lower A")],
                          "routines", query)
    put = re.fullmatch(r"/v1/routines/([^/]+)", path)
    if put and method == "PUT":
        return 200, {"routine": [{"id": put[1]}]}
    return None
