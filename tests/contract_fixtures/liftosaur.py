"""Fixture provider for the Liftosaur Adapter (see tests/test_contract.py).

Recorded-shape responses for every route the Adapter calls. Reuses the history
and plan fixtures in tests/fixtures/; test_liftosaur_golden.py imports PAGES and
WEIGHTS from here so the two suites share one copy.
"""

import json
import pathlib
import re

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"

SECOND = """2026-10-06T08:30:00Z / program: "M1" / dayName: "Lower A" / week: 3 / dayInWeek: 2 / exercises: {
  Squat, Barbell / 3x5 120kg @8+ / warmup: 2x5 60kg
  Romanian Deadlift / 3x8 90kg @7
  Custom Sled Thing / 4x10 40kg
}
"""
THIRD = """2026-10-13T08:30:00Z / program: "M1" / dayName: "Upper A" / week: 4 / dayInWeek: 1 / exercises: {
  Bench Press / 3x8 85kg @9+
  Lat Pulldown, Cable / 3x10 55kg @8+
}
"""
WEIGHTS = [  # newest first, as the API returns them
    {"timestamp": 4, "date": "2026-10-12T07:00:00.000Z", "value": "70.1kg"},
    {"timestamp": 3, "date": "2026-10-11T20:12:28.292Z", "value": "69.9kg"},
    {"timestamp": 2, "date": "2026-10-11T07:00:00.000Z", "value": "70.4kg"},
    {"timestamp": 1, "date": "2026-10-09T23:39:58.878Z", "value": "154lb"},
    {"timestamp": 0, "date": "2026-10-08T07:00:00.000Z", "value": "garbage"},
]
PAGES = [  # (records, hasMore, nextCursor)
    ([{"id": "r3", "text": THIRD}, {"id": "r2", "text": SECOND}], True, 1791000000000),
    ([{"id": "r1", "text": (FIX / "history_sample.txt").read_text()}], False, None),
]
# The suite's own history adds a pound-denominated record (Liftosaur's reference
# example), so the kg conversion is exercised. The golden test keeps PAGES as is.
CONTRACT_PAGES = [PAGES[0], (PAGES[1][0] + [
    {"id": "r0", "text": (FIX / "liftohistory_reference.txt").read_text()}], False, None)]

PLAN = json.loads((FIX / "liftosaur_plan.json").read_text())
STATS = json.loads((FIX / "liftosaur_program_stats.live.json").read_text())

EXPECT = {
    "history": {
        "count": 5,
        # Titles that are not Canonical Exercise Names, kept as the app wrote them.
        # (The fixtures' "Squat, Barbell" style names are not in Liftosaur's list either.)
        "unclassified": {"Mystery Machine", "Custom Sled Thing", "OHP", "Pull Ups",
                         "Wall Sit", "Squat, Barbell", "Bench Press, Barbell",
                         "Lat Pulldown, Cable"},
        # (start_time, exercise, set index, kg): pounds converted on read. Warmups
        # come first in a set list, so index 2 here is the first working set.
        "weights_kg": [("2026-09-01T09:00:00Z", "Bench Press", 1, 80.0),
                       ("2026-10-06T08:30:00Z", "Squat, Barbell", 2, 120.0),
                       ("2026-02-28T10:45:30Z", "Bench Press, Barbell", 2, 83.91)],
        # (start_time, exercise, set index, logged, rpe if logged): `@8+` is logged,
        # a bare `@8` is a prescription.
        "rpe": [("2026-09-01T09:00:00Z", "Bench Press", 1, True, 8.0),
                ("2026-09-08T09:00:00Z", "Lat Pulldown, Cable", 0, False, None),
                ("2026-09-01T09:00:00Z", "Mystery Machine", 0, False, None)],
    },
    "bodyweight": {"2026-10-09": 69.85, "2026-10-11": 69.9, "2026-10-12": 70.1},
    "find": ("curl", "Bicep Curl, Cable"),
    "plan_id": "p9",     # an id the fixtures can update and get
}


def respond(method, path, query, body):
    """(status, body) for one request, or None when no route is recorded."""
    path = path.removeprefix("/api/v1")
    if (method, path) == ("GET", "/history"):
        i = 1 if query.get("cursor") else 0
        records, more, cursor = CONTRACT_PAGES[i]
        data = {"records": records, "hasMore": more}
        if more:
            data["nextCursor"] = cursor
        return 200, {"data": data}
    if (method, path) == ("GET", "/measurements/weight"):
        return 200, {"data": {"values": WEIGHTS, "hasMore": False}}
    if (method, path) == ("GET", "/settings"):
        return 200, {"data": {"units": "kg"}}
    if (method, path) == ("POST", "/programs"):
        return 201, {"data": {"id": "p9", "name": body["name"]}}
    program = re.fullmatch(r"/programs/([^/]+)", path)
    if program and method == "PUT":
        return 200, {"data": {"id": "p9" if program[1] == "current" else program[1]}}
    if program and method == "GET":
        return 200, {"data": {"id": "p9", "name": "Contract", "text": "# Week 1\n"}}
    if (method, path) == ("POST", "/program-stats"):
        return 200, {"data": STATS}
    if (method, path) == ("POST", "/playground"):
        return 200, {"data": {"workout": ""}}
    return None
