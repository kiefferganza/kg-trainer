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
# Liftosaur runs Progression Rules itself, so `app.py update` guards against drift:
# see prescriptions() and the contract module.
NATIVE_PROGRESSION = True

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
    warmups: dict[str, str] = {}     # exercise -> warmup declared at first occurrence
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
                # Liftosaur fixes warmups at an exercise's first occurrence: re-declaring
                # them in a later week, even as `warmup: none`, is a parse error
                # (verified live 2026-11-07). A later week whose warmups differ is
                # reported, because the app will keep the first week's.
                warm_text = _sets(warm) if warm else "none"
                if name not in warmups:
                    warmups[name] = warm_text
                    parts.append(f"warmup: {warm_text}")
                elif warm_text != warmups[name]:
                    print(f"warning: {name} week {wk['number']} warmup ({warm_text}) "
                          f"differs from its first week ({warmups[name]}); Liftosaur "
                          "keeps the first.", file=sys.stderr)
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


# --- reading a program back: the drift guard's side of the comparison -------
#
# Liftosaur reformats stored text (`3x7 / 80kg 180s`, `///` lines dropped, weights
# moved between the sets part and a trailing part), so drift is compared on parsed
# prescriptions, never on text.

_WEEK_RE = re.compile(r"^#\s+(?:Week\s+)?(\d+)\b", re.I)
_SCHEME_RE = re.compile(r"^(\d+)x(\d+)(\+?)$")
_KEY_RE = re.compile(r"^[A-Za-z][\w ]*:")        # `warmup: ...`, `progress: ...`, `superset: A`
_LB_TO_KG = 0.45359237


def _load_token(token: str):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(kg|lb)", token)
    if not m:
        return None
    value = float(m[1])
    return round(value * _LB_TO_KG, 2) if m[2] == "lb" else value


def _parse_sets(part: str):
    """[(count, reps, load_kg | None, amrap)] for a `3x8 80kg, 1x10+ 60kg` part; None
    when the part is not a sets part. A group with no load of its own has load None."""
    groups = []
    for group in part.split(","):
        tokens = group.split()
        if not tokens:
            return None
        m = _SCHEME_RE.match(tokens[0])
        if not m:
            return None
        load = next((l for l in map(_load_token, tokens[1:]) if l is not None), None)
        groups.append((int(m[1]), int(m[2]), load, m[3] == "+"))
    return groups


def _exercise_line(line: str):
    """(name, [(reps, load_kg | None, amrap), ...]) for one exercise line, or None."""
    parts = [p.strip() for p in line.split(" / ")]
    groups, default = None, None
    for part in parts[1:]:
        if _KEY_RE.match(part):
            continue
        if groups is None:
            groups = _parse_sets(part)
            continue
        if default is None:       # `80kg 180s` / `20kg @8 60s` after the sets part
            default = next((l for l in map(_load_token, part.split()) if l is not None), None)
    if groups is None:
        return None
    sets = []
    for count, reps, load, amrap in groups:
        sets += [(reps, load if load is not None else default, amrap)] * count
    return parts[0], sets


def parse_program(text: str) -> dict[int, list[tuple]]:
    """{week: [(day, exercise, [(reps, load_kg | None, amrap), ...]), ...]} from
    Liftoscript. Warmups, `//` and `///` lines, timers, `@rpe`, superset and progress
    fields (including a multi-line `custom() {~ ... ~}`) are ignored. A program with
    no `# Week` headers is one week, number 1."""
    weeks: dict[int, list] = {}
    week, day, in_script = 1, None, False
    for raw in text.splitlines():
        line = raw.strip()
        if in_script:
            in_script = "~}" not in line
            continue
        if "{~" in line and "~}" not in line.split("{~", 1)[1]:
            in_script = True
        if not line or line.startswith("//"):
            continue
        m = _WEEK_RE.match(line)
        if m:
            week, day = int(m[1]), None
            weeks.setdefault(week, [])
            continue
        if line.startswith("#"):
            day = line.lstrip("#").strip()
            weeks.setdefault(week, [])
            continue
        parsed = _exercise_line(line)
        if parsed and day is not None:
            weeks.setdefault(week, []).append((day, parsed[0], parsed[1]))
    return weeks


def prescriptions(payload: dict, week: int):
    """The working sets of `week` in a `get_plan` payload (contract.drift's shape)."""
    return parse_program(payload.get("text") or "").get(week)


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
# Verified live 2026-11-07: program-stats names muscles in camelCase (upperBack,
# rearDelts, sideDelts, frontDelts, upperTraps, rotatorCuff, ...). The delt heads
# fold into the skill's single `shoulders`; rotatorCuff has no skill muscle and is
# shown as returned.
MUSCLE_TO_SKILL = {
    "frontdelts": "shoulders", "sidedelts": "shoulders", "reardelts": "shoulders",
    "uppertraps": "traps", "lowertraps": "traps",
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



def skill_muscle(name: str) -> str:
    """A Liftosaur muscle name in the skill's vocabulary; group names (`back`) and
    anything unknown are returned lower-cased, unchanged, never guessed."""
    folded = re.sub(r"[\s_\-]", "", str(name).lower())
    return MUSCLE_TO_SKILL.get(folded, str(name).strip().lower())


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _stats_weekly(stats) -> dict[str, float] | None:
    """{muscle: primary sets} from a program-stats payload, or None when the shape
    is not recognised (then the caller reports it, never guesses).

    Verified shape (2026-11-07): `muscleGroups` is a list of
    {"muscle", "totalSets", "exercises": [{"name", "sets", "isSynergist"}]}.
    `totalSets` adds fractional synergist credit; plan.py stats counts each
    exercise once, under its primary muscle. So only non-synergist exercise sets
    are summed here, which makes the two counts comparable.
    """
    groups = stats.get("muscleGroups") if isinstance(stats, dict) else None
    if not isinstance(groups, list):
        return None
    out: dict[str, float] = {}
    for g in groups:
        if not isinstance(g, dict) or not isinstance(g.get("muscle"), str) \
                or not isinstance(g.get("exercises"), list):
            return None
        primary = 0
        for ex in g["exercises"]:
            if not isinstance(ex, dict) or not _number(ex.get("sets")):
                return None
            if not ex.get("isSynergist"):
                primary += ex["sets"]
        if primary:
            key = skill_muscle(g["muscle"])
            out[key] = out.get(key, 0) + primary
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
