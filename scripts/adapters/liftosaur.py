"""Liftosaur Adapter (read side): ping, pull_history, pull_bodyweight, find_exercise.

Base https://www.liftosaur.com/api/v1, `Authorization: Bearer lftsk_...`, premium
subscription required, cursor pagination (`limit` capped at 200). History comes
as Liftohistory text, parsed by liftohistory.py; weights are "69.9kg" / "154lb"
strings converted to kg. Exercise names are Liftosaur's own, which are the
Canonical Exercise Names, so translation is the identity.

This module also holds the HTTP helpers (call, ok, paginate) that the Liftosaur
extras CLI, scripts/liftosaur.py, reuses for programs and the playground.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from adapters import http as apphttp  # noqa: E402
from liftohistory import parse_liftohistory  # noqa: E402

NAME = "liftosaur"
CAPABILITIES = {"ping", "pull_history", "pull_bodyweight", "find_exercise"}

BASE = "https://www.liftosaur.com/api/v1"
EXERCISES = pathlib.Path(__file__).resolve().parent.parent.parent / "references" / "exercises.txt"


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


# --- the contract's operations --------------------------------------------

def ping() -> dict:
    data = ok(*call("GET", "/settings"), "GET /settings")
    units = data.get("units") if isinstance(data, dict) else None
    return {"account": "liftosaur", "units": units or "kg"}


def pull_history() -> list[dict]:
    workouts = []
    for record in paginate("/history", "records"):
        for workout in parse_liftohistory(record.get("text") or ""):
            workout["id"] = record.get("id")
            workouts.append(workout)
    return sorted(workouts, key=lambda w: w["start_time"], reverse=True)


def pull_bodyweight() -> list[tuple]:
    """One (date, kg) per day, the latest weigh-in of the day winning."""
    best: dict = {}
    for v in paginate("/measurements/weight", "values"):
        read = weigh_in(v)
        if read is None:
            continue
        ts = v.get("timestamp") or 0
        if read[0] not in best or ts > best[read[0]][0]:
            best[read[0]] = (ts, read[1])
    return [(d, kg) for d, (_, kg) in sorted(best.items())]


def find_exercise(pattern: str) -> list[dict]:
    needle = (pattern or "").lower()
    names = [n.strip() for n in EXERCISES.read_text().splitlines()[1:] if n.strip()]
    return [{"canonical": n, "app_id": None} for n in names if needle in n.lower()]
