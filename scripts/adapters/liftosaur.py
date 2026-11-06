"""Liftosaur Adapter: a thin registry entry over scripts/liftosaur.py's read functions.

Ticket 03 moves the rest of Liftosaur behind the contract. Until then this only
lets `app.py apps` list it and `app.py ping`/`pull` reach it. Exercise names are
Liftosaur's own, so translation is the identity.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import liftosaur as _client  # noqa: E402  -- scripts/liftosaur.py, not this module
from liftohistory import parse_liftohistory  # noqa: E402

NAME = "liftosaur"
CAPABILITIES = {"ping", "pull_history", "pull_bodyweight", "find_exercise"}


def ping() -> dict:
    data = _client.ok(*_client.call("GET", "/settings"), "GET /settings")
    units = data.get("units") if isinstance(data, dict) else None
    return {"account": "liftosaur", "units": units or "kg"}


def pull_history() -> list[dict]:
    workouts = []
    for record in _client.paginate("/history", "records"):
        for workout in parse_liftohistory(record.get("text") or ""):
            workout["id"] = record.get("id")
            workouts.append(workout)
    return sorted(workouts, key=lambda w: w["start_time"], reverse=True)


def pull_bodyweight() -> list[tuple]:
    best: dict = {}
    for v in _client.paginate("/measurements/weight", "values"):
        read = _client.weigh_in(v)
        if read is None:
            continue
        ts = v.get("timestamp") or 0
        if read[0] not in best or ts > best[read[0]][0]:
            best[read[0]] = (ts, read[1])
    return [(d, kg) for d, (_, kg) in sorted(best.items())]


def find_exercise(pattern: str) -> list[dict]:
    needle = (pattern or "").lower()
    names = [n.strip() for n in _client.EXERCISES.read_text().splitlines()[1:] if n.strip()]
    return [{"canonical": n, "app_id": None} for n in names if needle in n.lower()]
