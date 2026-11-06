"""Liftosaur Adapter: reads (ping, pull_history, pull_bodyweight, find_exercise) and
plans (compile, push_plan, update_plan, get_plan, check).

Base https://www.liftosaur.com/api/v1, `Authorization: Bearer lftsk_...`, premium
subscription required, cursor pagination (`limit` capped at 200). History comes
as Liftohistory text, parsed by liftohistory.py; weights are "69.9kg" / "154lb"
strings converted to kg. Exercise names are Liftosaur's own, which are the
Canonical Exercise Names, so translation is the identity.

`compile` turns a Canonical Plan into one Liftoscript program covering every week.
`check` runs Liftosaur's server-side `program-stats` and `playground` as a
cross-check on the local `plan.py stats`.

This module also holds the HTTP helpers (call, ok, paginate) and the raw program
and playground calls that the Liftosaur extras CLI, scripts/liftosaur.py, reuses.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from adapters import http as apphttp  # noqa: E402
from liftohistory import parse_liftohistory  # noqa: E402

NAME = "liftosaur"
CAPABILITIES = {"ping", "pull_history", "pull_bodyweight", "find_exercise",
                "compile", "push_plan", "update_plan", "get_plan", "check"}

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


# --- compile: Canonical Plan -> Liftoscript --------------------------------

def _n(x) -> str:
    return f"{x:g}"


def _weight(load_kg, rpe) -> str:
    """A set's load. Liftoscript wants every set to carry a weight, an RPE or a
    percentage: a null load with an RPE is `@rpe`; a null load with neither is
    `0kg` (bodyweight work), the only reading of "no external load"."""
    if load_kg is not None:
        return f"{_n(load_kg)}kg" + (f" @{_n(rpe)}" if rpe is not None else "")
    return f"@{_n(rpe)}" if rpe is not None else "0kg"


def _set_text(s: dict) -> str:
    plus = "+" if s["kind"] == "failure" else ""   # to failure = AMRAP
    return f"{s['reps']}{plus} {_weight(s.get('load_kg'), s.get('rpe'))}"


def _sets(sets: list[dict]) -> str:
    """`3x8 80kg, 1x10+ 60kg`: consecutive identical sets collapse to `NxR`."""
    groups: list[list] = []
    for s in sets:
        text = _set_text(s)
        if groups and groups[-1][0] == text:
            groups[-1][1] += 1
        else:
            groups.append([text, 1])
    return ", ".join(f"{n}x{text}" for text, n in groups)


def _progress(rule) -> str:
    """The `progress:` value for an effective Progression Rule (None = off)."""
    if rule is None:
        return "none"
    if "native" in rule:
        return rule["text"].strip()
    kind = rule["rule"]
    if kind == "linear":
        return f"lp({_n(rule['increment_kg'])}kg)"
    if kind == "double":
        return f"dp({_n(rule['increment_kg'])}kg, {rule['low']}, {rule['high']})"
    if kind == "reps_threshold":
        return f"sum({rule['total']}, {_n(rule['increment_kg'])}kg)"
    return "none"


def _foreign_natives(plan: dict) -> list[str]:
    blocks = [("block", plan.get("progression") or {})]
    blocks += [(f"week {w.get('number')}", w.get("progression") or {})
               for w in plan.get("weeks", [])]
    return [f"{name} ({where}) has a Native Rule for {rule['native']!r}"
            for where, block in blocks for name, rule in block.items()
            if isinstance(rule, dict) and "native" in rule and rule["native"] != NAME]


def _description(ex: dict) -> list[str]:
    lines = (ex.get("cue") or "").strip().splitlines()
    if ex.get("rir_target") is not None:
        lines.append(f"Target: {_n(ex['rir_target'])} RIR")
    return lines


def compile(plan: dict, week: int | None = None) -> str:
    """The whole plan as one Liftoscript program (`# Week N`, `## Day`). `week` is
    ignored: Liftosaur holds every week. Local, no network.

    Progression is declared where the effective rule (plan.effective_rule: deload
    and per-week overrides included) first appears for an exercise and again
    wherever it changes, so `progress: none` marks deload weeks. A Native Rule for
    another app fails before anything is produced. Drop sets compile as plain
    working sets; a failure set is an AMRAP (`N+`). Cue and RIR become `//`
    description lines, repeated on every occurrence and cleared with an empty `//`.
    """
    from plan import effective_rule  # lazy: plan.py pulls in the analysis modules
    refusals = _foreign_natives(plan)
    if refusals:
        sys.exit("Liftosaur cannot compile a Native Rule written for another app:\n  "
                 + "\n  ".join(refusals)
                 + "\nChange it to linear/double/reps_threshold/none, or remove it.")
    out: list[str] = []
    state: dict[str, str] = {}       # exercise -> progress value last declared
    described: dict[str, bool] = {}  # exercise -> last occurrence had a description
    for wk in plan["weeks"]:
        out += [f"# Week {wk['number']}",
                f"/// {wk['phase']}"
                + (", deload" if wk.get("deload") and "deload" not in wk["phase"].lower() else ""),
                ""]
        for day in wk["days"]:
            out += [f"## {day['name']}"]
            for ex in day["exercises"]:
                name = ex["exercise"]
                desc = _description(ex)
                if desc:
                    out += [f"// {line}" if line.strip() else "//" for line in desc]
                elif described.get(name):
                    out.append("//")     # stop description inheritance
                described[name] = bool(desc)
                work = [s for s in ex["sets"] if s["kind"] != "warmup"]
                warm = [s for s in ex["sets"] if s["kind"] == "warmup"]
                parts = [name, _sets(work)]
                if ex.get("rest_seconds") is not None:
                    parts.append(f"{ex['rest_seconds']}s")
                parts.append(f"warmup: {_sets(warm)}" if warm else "warmup: none")
                rule = effective_rule(plan, wk, name)
                text = _progress(rule)
                explicit_none = isinstance(rule, dict) and rule.get("rule") == "none"
                if name not in state:
                    declare = text != "none" or explicit_none
                else:
                    declare = text != state[name]
                state[name] = text
                if declare:
                    parts.append(f"progress: {text}")
                if ex.get("superset") is not None:
                    parts.append(f"superset: {ex['superset']}")
                out.append(" / ".join(parts))
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# --- programs and the playground (raw calls) -------------------------------

def _write_program(text: str, name: str | None = None, pid: str | None = None):
    """POST /programs (needs `name`) or PUT /programs/:id. Returns (data, what)."""
    if pid:
        path = f"/programs/{urllib.parse.quote(pid, safe='')}"
        body = {"text": text}
        if name:
            body["name"] = name
        what = f"PUT {path}"
        return ok(*call("PUT", path, body=body), what), what
    if not name:
        sys.exit("--name is required when creating a program.")
    what = "POST /programs"
    return ok(*call("POST", "/programs", body={"name": name, "text": text}), what), what


def push_plan(compiled: str, name: str) -> str:
    """Create a program from compiled Liftoscript; returns its id. A response with
    no id is a failure, never an empty success."""
    data, what = _write_program(compiled, name=name)
    return str(_field(data, "id", what))


def update_plan(compiled: str, plan_id: str) -> str:
    """Replace program `plan_id`'s text (`current` is accepted); returns its id."""
    data, what = _write_program(compiled, pid=plan_id)
    return str(_field(data, "id", what))


def get_plan(plan_id: str) -> dict:
    """The program payload ({id, name, text, ...}); `current` is the active program."""
    path = f"/programs/{urllib.parse.quote(plan_id, safe='')}"
    what = f"GET {path}"
    data = ok(*call("GET", path), what)
    _field(data, "text", what)
    return data


def program_stats(text: str):
    return ok(*call("POST", "/program-stats", body={"programText": text}),
              "POST /program-stats")


def playground(text: str, week: int | None = None, day: int | None = None,
               commands: list[str] | None = None):
    body = {"programText": text}
    if week:
        body["week"] = week
    if day:
        body["day"] = day
    if commands:
        body["commands"] = commands
    return ok(*call("POST", "/playground", body=body), "POST /playground")


# --- check: program-stats + playground parse check --------------------------

# Liftosaur's muscle names -> the skill's muscle vocabulary (fetch_reference.MUSCLES).
# Names are matched lower-case with spaces/underscores/hyphens folded together.
# UNVERIFIED against a live response until ticket 05's live check is recorded.
MUSCLE_TO_SKILL = {
    "abs": "abdominals", "abdominals": "abdominals", "core": "abdominals",
    "chest": "chest", "pectorals": "chest",
    "shoulders": "shoulders", "deltoids": "shoulders",
    "triceps": "triceps", "biceps": "biceps", "lats": "lats",
    "upperback": "upper_back", "lowerback": "lower_back", "traps": "traps",
    "trapezius": "traps", "quadriceps": "quadriceps", "quads": "quadriceps",
    "hamstrings": "hamstrings", "glutes": "glutes", "calves": "calves",
    "adductors": "adductors", "forearms": "forearms",
}
# Liftosaur groups that span several skill muscles; compared against their sum.
MUSCLE_GROUPS = {"back": ["lats", "upper_back", "lower_back", "traps"]}

_NAME_KEYS = ("name", "muscle", "muscleGroup", "group", "key")
_SETS_KEYS = ("sets", "setsCount", "weeklySets", "totalSets", "total", "count", "value")


def skill_muscle(name: str) -> str:
    """A Liftosaur muscle name in the skill's vocabulary; group names (`back`) and
    anything unknown are returned lower-cased, unchanged, never guessed."""
    folded = re.sub(r"[\s_\-]", "", str(name).lower())
    return MUSCLE_TO_SKILL.get(folded, str(name).strip().lower())


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _stats_weekly(stats) -> dict[str, float] | None:
    """{muscle: sets} from a program-stats payload's `muscleGroups`, or None when
    the shape is not recognised (then the caller reports it, never guesses)."""
    groups = stats.get("muscleGroups") if isinstance(stats, dict) else None
    if isinstance(groups, dict):
        pairs = list(groups.items())
    elif isinstance(groups, list):
        pairs = []
        for g in groups:
            if not isinstance(g, dict):
                return None
            name = next((g[k] for k in _NAME_KEYS if isinstance(g.get(k), str)), None)
            sets = next((g[k] for k in _SETS_KEYS if _number(g.get(k))), None)
            if name is None or sets is None:
                return None
            pairs.append((name, sets))
    else:
        return None
    out: dict[str, float] = {}
    for name, sets in pairs:
        if not _number(sets):
            return None
        key = skill_muscle(name)
        out[key] = out.get(key, 0) + sets
    return out


def _api_error(status: int, payload) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    error = error if isinstance(error, dict) else {}
    detail = " ".join(str(x) for x in (error.get("code"), error.get("message")) if x)
    return f"HTTP {status} {detail}".rstrip()


def check(compiled: str) -> dict:
    """{"ok", "weekly_sets", "errors"}: Liftosaur's own `program-stats` and a
    `playground` parse check on the compiled text. Both calls run even if one
    fails. `weekly_sets` uses the skill's muscle names where Liftosaur's map onto
    them one-to-one (see MUSCLE_TO_SKILL) and is empty when the response shape is
    unrecognised, which is itself reported as an error."""
    errors: list[str] = []
    weekly: dict = {}
    status, payload = call("POST", "/program-stats", body={"programText": compiled})
    if status >= 300:
        errors.append(f"program-stats: {_api_error(status, payload)}")
    else:
        stats = payload.get("data") if isinstance(payload, dict) else None
        found = _stats_weekly(stats)
        if found is None:
            errors.append("program-stats: response has no recognisable muscleGroups; "
                          "run `liftosaur.py program-stats <file>` and record the shape")
        else:
            weekly = found
    status, payload = call("POST", "/playground", body={"programText": compiled})
    if status >= 300:
        errors.append(f"playground: {_api_error(status, payload)}")
    else:
        data = payload.get("data") if isinstance(payload, dict) else None
        problem = (data.get("error") or data.get("errors")) if isinstance(data, dict) else None
        if problem:
            errors.append("playground: "
                          + (problem if isinstance(problem, str) else json.dumps(problem)))
    return {"ok": not errors, "weekly_sets": weekly, "errors": errors}
