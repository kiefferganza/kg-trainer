#!/usr/bin/env python3
"""Author, check and render a Canonical Plan. Local only, for every Training App.

A Canonical Plan is plans/meso-NN.plan.json (see the app-agnostic adapters spec).
This script owns that file's shape; meso-NN.md is generated from it by `render`
and is never hand-edited.

Usage:
  plan.py validate <plan>        # name every failure, exit 1 if any
  plan.py stats <plan> [--json]  # working sets per muscle, per week
  plan.py render <plan>          # write meso-NN.md next to the plan
  plan.py progress <plan> [--since YYYY-MM-DD] [--dry-run]
                                 # apply each rule to what was logged in current_week,
                                 # write week current_week+1, advance current_week

Equipment for the 2.5 kg rule is read from the Canonical Exercise Name suffix
(", Barbell", ", Smith Machine", ", EZ Bar", ", Trap Bar"). A few names carry no
suffix because Liftosaur's built-in list treats the bare name as the barbell lift
(Squat, Bench Press, Deadlift...); BARE_BARBELL lists them. Dumbbell and machine
loads are not checked.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from analyze import load_muscles  # noqa: E402  — one muscle-map loader (shipped + personal)
from fetch_reference import muscle_for  # noqa: E402

SCHEMA = 1
SET_KINDS = ("working", "warmup", "drop", "failure")
RULES = ("linear", "double", "reps_threshold", "none")
LOAD_STEP_KG = 2.5
STEPPED_SUFFIXES = ("barbell", "smith machine", "ez bar", "trap bar")
# Bare names that Liftosaur's built-in list means as the barbell lift.
BARE_BARBELL = frozenset({
    "Bench Press", "Bench Press Close Grip", "Bench Press Wide Grip", "Incline Bench Press",
    "Incline Bench Press Wide Grip", "Legs Up Bench Press", "Bent Over Row", "Pendlay Row",
    "Deadlift", "Sumo Deadlift", "Stiff Leg Deadlift", "Straight Leg Deadlift",
    "Deficit Deadlift", "Romanian Deadlift", "Trap Bar Deadlift", "Squat", "Front Squat",
    "Box Squat", "Overhead Squat", "Zercher Squat", "Overhead Press", "Push Press",
    "Hip Thrust",
})


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _on_step(x: float) -> bool:
    q = x / LOAD_STEP_KG
    return abs(q - round(q)) < 1e-9


def needs_step(name: str) -> bool:
    """True when the exercise is loaded on a barbell-type bar (2.5 kg steps)."""
    if name in BARE_BARBELL:
        return True
    _, sep, suffix = name.rpartition(",")
    return bool(sep) and suffix.strip().lower() in STEPPED_SUFFIXES


def rule_errors(rule, where: str, exercise: str) -> list[str]:
    """Why a Progression Rule is malformed; empty when it is fine."""
    if not isinstance(rule, dict):
        return [f"{where}: Progression Rule must be an object, got {rule!r}"]
    if "native" in rule:
        extra = sorted(set(rule) - {"native", "text"})
        errs = [f"{where}: Native Rule has unknown keys {extra}"] if extra else []
        if not (isinstance(rule["native"], str) and rule["native"].strip()):
            errs.append(f"{where}: Native Rule needs a non-empty app tag in 'native'")
        if not (isinstance(rule.get("text"), str) and rule["text"].strip()):
            errs.append(f"{where}: Native Rule needs non-empty 'text'")
        return errs
    kind = rule.get("rule")
    if kind not in RULES:
        return [f"{where}: unknown rule {kind!r}; use one of {', '.join(RULES)} "
                "or a Native Rule {\"native\": \"<app>\", \"text\": \"...\"}"]
    fields = {"linear": {"increment_kg"}, "double": {"increment_kg", "low", "high"},
              "reps_threshold": {"total", "increment_kg"}, "none": set()}[kind]
    errs = []
    missing, extra = sorted(fields - set(rule)), sorted(set(rule) - fields - {"rule"})
    if missing:
        errs.append(f"{where}: {kind} is missing {', '.join(missing)}")
    if extra:
        errs.append(f"{where}: {kind} has unknown keys {', '.join(extra)}")
    if "increment_kg" in rule:
        inc = rule["increment_kg"]
        if not (_num(inc) and inc > 0):
            errs.append(f"{where}: increment_kg must be a number above 0, got {inc!r}")
        elif needs_step(exercise) and not _on_step(inc):
            errs.append(f"{where}: increment_kg {inc} is not a multiple of {LOAD_STEP_KG} kg "
                        f"for a barbell-type lift ({exercise})")
    for key in ("low", "high", "total"):
        if key in rule and not (_int(rule[key]) and rule[key] > 0):
            errs.append(f"{where}: {key} must be a whole number above 0, got {rule[key]!r}")
    if _int(rule.get("low")) and _int(rule.get("high")) and rule["low"] >= rule["high"]:
        errs.append(f"{where}: low ({rule['low']}) must be below high ({rule['high']})")
    return errs


def _progression_errors(block, where: str, known: set) -> list[str]:
    if not isinstance(block, dict):
        return [f"{where}: progression must be an object of exercise -> rule"]
    errs = []
    for name, rule in block.items():
        if name not in known:
            errs.append(f"{where}.{name}: unknown exercise name {name!r}")
        errs += rule_errors(rule, f"{where}.{name}", name)
    return errs


def _set_errors(s, where: str, name: str) -> list[str]:
    if not isinstance(s, dict):
        return [f"{where}: set must be an object"]
    errs = []
    if not (_int(s.get("reps")) and s["reps"] > 0):
        errs.append(f"{where}: reps must be a whole number above 0, got {s.get('reps')!r}")
    load = s.get("load_kg")
    if load is not None:
        if not (_num(load) and load >= 0):
            errs.append(f"{where}: load_kg must be a number of 0 or more, or null, got {load!r}")
        elif needs_step(name) and not _on_step(load):
            errs.append(f"{where}: {name} load {load} kg is not a multiple of {LOAD_STEP_KG} kg")
    rpe = s.get("rpe")
    if rpe is not None and not (_num(rpe) and 1 <= rpe <= 10):
        errs.append(f"{where}: rpe must be between 1 and 10, or null, got {rpe!r}")
    if s.get("kind") not in SET_KINDS:
        errs.append(f"{where}: kind must be one of {', '.join(SET_KINDS)}, got {s.get('kind')!r}")
    return errs


def _exercise_errors(ex, where: str, known: set) -> list[str]:
    if not isinstance(ex, dict):
        return [f"{where}: exercise entry must be an object"]
    name = ex.get("exercise")
    if not (isinstance(name, str) and name):
        return [f"{where}: missing 'exercise' name"]
    errs = [f"{where}: unknown exercise name {name!r}"] if name not in known else []
    sets = ex.get("sets")
    if not (isinstance(sets, list) and sets):
        errs.append(f"{where} ({name}): needs at least one set")
    else:
        for i, s in enumerate(sets, 1):
            errs += _set_errors(s, f"{where} ({name}) set {i}", name)
    rest = ex.get("rest_seconds")
    if rest is not None and not (_int(rest) and rest >= 0):
        errs.append(f"{where} ({name}): rest_seconds must be a whole number or null, got {rest!r}")
    rir = ex.get("rir_target")
    if rir is not None and not (_num(rir) and 0 <= rir <= 10):
        errs.append(f"{where} ({name}): rir_target must be between 0 and 10, or null, got {rir!r}")
    if ex.get("cue") is not None and not isinstance(ex["cue"], str):
        errs.append(f"{where} ({name}): cue must be text or null")
    sup = ex.get("superset")
    if sup is not None and not (isinstance(sup, str) or _int(sup)):
        errs.append(f"{where} ({name}): superset must be a label or null, got {sup!r}")
    return errs


def validate_plan(plan, muscles: dict) -> list[str]:
    """Every problem with a Canonical Plan, one string each; empty means valid."""
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"]
    known = set(muscles)
    errs = []
    if plan.get("schema") != SCHEMA:
        errs.append(f"schema must be {SCHEMA}, got {plan.get('schema')!r}")
    for key in ("name", "created"):
        if not (isinstance(plan.get(key), str) and plan[key].strip()):
            errs.append(f"{key} must be non-empty text")
    if isinstance(plan.get("created"), str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", plan["created"]):
        errs.append(f"created must be YYYY-MM-DD, got {plan['created']!r}")
    started = plan.get("week_started")  # written by `progress`; optional
    if started is not None and not (isinstance(started, str)
                                    and re.fullmatch(r"\d{4}-\d{2}-\d{2}", started)):
        errs.append(f"week_started must be YYYY-MM-DD, got {started!r}")
    weeks = plan.get("weeks")
    if not (isinstance(weeks, list) and weeks):
        errs.append("weeks must be a non-empty list")
        weeks = []
    numbers = []
    for i, wk in enumerate(weeks, 1):
        if not isinstance(wk, dict):
            errs.append(f"weeks[{i}]: must be an object")
            continue
        num = wk.get("number")
        w = f"week {num}" if _int(num) else f"weeks[{i}]"
        if not (_int(num) and num > 0):
            errs.append(f"{w}: number must be a whole number above 0, got {num!r}")
        elif num in numbers:
            errs.append(f"{w}: duplicate week number")
        else:
            numbers.append(num)
        if not (isinstance(wk.get("phase"), str) and wk["phase"].strip()):
            errs.append(f"{w}: phase must be non-empty text")
        if not isinstance(wk.get("deload"), bool):
            errs.append(f"{w}: deload must be true or false, got {wk.get('deload')!r}")
        days = wk.get("days")
        if not (isinstance(days, list) and days):
            errs.append(f"{w}: days must be a non-empty list")
            days = []
        for j, day in enumerate(days, 1):
            if not isinstance(day, dict):
                errs.append(f"{w} day {j}: must be an object")
                continue
            d = f"{w} {day.get('name') or f'day {j}'}"
            if not (isinstance(day.get("name"), str) and day["name"].strip()):
                errs.append(f"{d}: day needs a name")
            exs = day.get("exercises")
            if not (isinstance(exs, list) and exs):
                errs.append(f"{d}: exercises must be a non-empty list")
                continue
            for k, ex in enumerate(exs, 1):
                errs += _exercise_errors(ex, f"{d} exercise {k}", known)
        if "progression" in wk:
            errs += _progression_errors(wk["progression"], f"{w} progression", known)
    cw = plan.get("current_week")
    if not _int(cw):
        errs.append(f"current_week must be a whole number, got {cw!r}")
    elif numbers and cw not in numbers:
        errs.append(f"current_week {cw} is not one of the plan's weeks {sorted(numbers)}")
    errs += _progression_errors(plan.get("progression", {}), "progression", known)
    return errs


def effective_rule(plan: dict, week: dict, exercise: str):
    """The Progression Rule in force for an exercise in a week, or None when off.

    A week's own entry overrides the block-level rule for that week only. A
    deload week has progression off for every exercise except those it names.
    """
    if exercise in week.get("progression", {}):
        return week["progression"][exercise]
    if week.get("deload"):
        return None
    return plan.get("progression", {}).get(exercise)


def weekly_sets(plan: dict, muscles: dict) -> dict[int, dict[str, int]]:
    """Working (non-warmup) sets per muscle for each week number."""
    out: dict[int, dict[str, int]] = {}
    for wk in plan["weeks"]:
        counts: dict[str, int] = {}
        for day in wk["days"]:
            for ex in day["exercises"]:
                n = sum(1 for s in ex["sets"] if s["kind"] != "warmup")
                if n:
                    m = muscle_for(ex["exercise"], muscles)
                    counts[m] = counts.get(m, 0) + n
        out[wk["number"]] = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return out


def _kg(x) -> str:
    return f"{x:g}"


def _set_key(s: dict) -> tuple:
    return (s["reps"], s.get("load_kg"), s.get("rpe"), s["kind"])


def _prescription(sets: list[dict]) -> str:
    groups: list[list] = []
    for s in sets:
        if groups and _set_key(groups[-1][0]) == _set_key(s):
            groups[-1][1] += 1
        else:
            groups.append([s, 1])
    parts = []
    for s, n in groups:
        load = f" @ {_kg(s['load_kg'])} kg" if s.get("load_kg") is not None else ""
        rpe = f" RPE {_kg(s['rpe'])}" if s.get("rpe") is not None else ""
        tag = "" if s["kind"] == "working" else f" ({s['kind']})"
        parts.append(f"{n} x {s['reps']}{load}{rpe}{tag}")
    return "; ".join(parts)


def _rule_text(rule) -> str:
    if rule is None:
        return "off"
    if "native" in rule:
        return f"native ({rule['native']})"
    kind = rule["rule"]
    if kind == "linear":
        return f"linear +{_kg(rule['increment_kg'])} kg"
    if kind == "double":
        return f"double {rule['low']}-{rule['high']}, +{_kg(rule['increment_kg'])} kg"
    if kind == "reps_threshold":
        return f"{rule['total']} total reps, +{_kg(rule['increment_kg'])} kg"
    return "none"


def _cell(text) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(plan: dict, source: str) -> str:
    L = [f"# {plan['name']}", "",
         f"Generated by `plan.py render` from `{source}`. Do not edit; change the plan and render again.",
         "", f"Created {plan['created']}. Current week: {plan['current_week']}.", ""]
    for wk in plan["weeks"]:
        flag = " (deload, progression off)" if wk["deload"] else ""
        L += [f"## Week {wk['number']}: {wk['phase']}{flag}", ""]
        for day in wk["days"]:
            L += [f"### {day['name']}", "",
                  "| # | Exercise | Sets | RIR | Rest | Progression | Cue |",
                  "|---|---|---|---|---|---|---|"]
            for i, ex in enumerate(day["exercises"], 1):
                label = ex["exercise"]
                if ex.get("superset") is not None:
                    label += f" [superset {ex['superset']}]"
                rest = f"{ex['rest_seconds']} s" if ex.get("rest_seconds") is not None else ""
                rir = ex.get("rir_target")
                L.append("| " + " | ".join(_cell(c) for c in (
                    i, label, _prescription(ex["sets"]), "" if rir is None else _kg(rir), rest,
                    _rule_text(effective_rule(plan, wk, ex["exercise"])), ex.get("cue"))) + " |")
            L.append("")
    return "\n".join(L)


def md_path_for(plan_path: pathlib.Path) -> pathlib.Path:
    name = plan_path.name
    stem = name[: -len(".plan.json")] if name.endswith(".plan.json") else plan_path.stem
    return plan_path.with_name(stem + ".md")


def _load(path: str) -> tuple[pathlib.Path, dict]:
    p = pathlib.Path(path)
    try:
        return p, json.loads(p.read_text())
    except FileNotFoundError:
        sys.exit(f"No plan at {p}")
    except json.JSONDecodeError as e:
        sys.exit(f"{p} is not valid JSON: {e}")


def _checked(path: str) -> tuple[pathlib.Path, dict, dict]:
    p, plan = _load(path)
    muscles = load_muscles()
    errs = validate_plan(plan, muscles)
    if errs:
        sys.exit(f"{p} is not a valid Canonical Plan:\n" + "\n".join(f"  - {e}" for e in errs))
    return p, plan, muscles


def cmd_validate(a):
    p, plan = _load(a.plan)
    errs = validate_plan(plan, load_muscles())
    if errs:
        print(f"{p}: {len(errs)} problem(s)")
        for e in errs:
            print(f"  - {e}")
        return 1
    blocks = [plan.get("progression", {})] + [w.get("progression", {}) for w in plan["weeks"]]
    natives = sorted({r["native"] for b in blocks for r in b.values() if "native" in r})
    extra = f" Native Rules for: {', '.join(natives)}." if natives else ""
    print(f"{p}: valid ({len(plan['weeks'])} weeks).{extra}")
    return 0


def cmd_stats(a):
    _, plan, muscles = _checked(a.plan)
    stats = weekly_sets(plan, muscles)
    if a.json:
        print(json.dumps({str(k): v for k, v in stats.items()}, indent=2))
        return 0
    for wk in plan["weeks"]:
        counts = stats[wk["number"]]
        print(f"Week {wk['number']} ({wk['phase']}{', deload' if wk['deload'] else ''}): "
              f"{sum(counts.values())} working sets")
        for m, n in counts.items():
            print(f"  {m:<12} {n}")
    return 0


def cmd_render(a):
    p, plan, _ = _checked(a.plan)
    out = md_path_for(p)
    out.write_text(render_markdown(plan, p.name))
    print(f"wrote {out}")
    return 0


# --- progress: the skill, not the app, moves the prescriptions on --------------
#
# Matching logged sessions to the plan's days (see progress_plan):
#   * a plan day is matched by name to a Workout Log session's day_name, ignoring
#     case and a leading "Week N - " (Liftosaur writes "Week 1 - Upper A"; Hevy
#     titles a workout with the routine title, which is the day name);
#   * when the session carries a `week` (Liftosaur), it must equal current_week;
#     when it carries none (Hevy), the date window decides;
#   * the date window starts at the latest of the plan's `created`, its
#     `week_started` (written by `progress` itself: the day after the last session
#     it consumed) and `--since`; sessions before it never count;
#   * if a day was logged more than once in the window, the latest session wins.

_WEEK_PREFIX = re.compile(r"^\s*week\s*\d+\s*[-:–—]\s*", re.I)


def _day_key(name) -> str:
    return _WEEK_PREFIX.sub("", name or "").strip().casefold()


def _day_of(start_time) -> str:
    return str(start_time)[:10]


def _working(sets: list[dict]) -> list[dict]:
    return [s for s in sets if s["kind"] != "warmup"]


def _find_session(workouts: list[dict], day_name: str, week_no: int, floor: str):
    best = None
    for w in workouts:
        if _day_key(w.get("day_name")) != _day_key(day_name):
            continue
        if w.get("week") is not None and w["week"] != week_no:
            continue
        if _day_of(w["start_time"]) < floor:
            continue
        if best is None or str(w["start_time"]) > str(best["start_time"]):
            best = w
    return best


def _logged_sets(session: dict, exercise: str):
    """Non-warmup logged sets of an exercise in a session, or None if it was not done."""
    for e in session.get("exercises", []):
        if e.get("title") == exercise:
            return [s for s in e.get("sets", []) if s.get("type") != "warmup"]
    return None


def _set_done(target: dict, logged: dict) -> bool:
    """A set counts when it reached the prescribed reps at no less than the prescribed load."""
    reps = logged.get("reps")
    if not (_num(reps) and reps >= target["reps"]):
        return False
    if target.get("load_kg") is not None:
        kg = logged.get("weight_kg")
        if not (_num(kg) and kg >= target["load_kg"] - 1e-6):
            return False
    return True


def _bump(load, inc):
    return None if load is None else round(load + inc, 3)


def apply_rule(rule: dict, sets: list[dict], logged: list[dict]):
    """(new working sets, outcome, why) for a linear/double/reps_threshold rule.

    `sets` are the week's prescribed non-warmup sets and `logged` the same number
    or more logged ones. Outcome is "progressed" or "held". Matches Liftosaur's own
    lp/dp/sum for a fully completed session (recorded live in
    tests/fixtures/playground_finish.json); the failure cases are the skill's:
    lp and sum hold on a miss (Liftosaur's defaults apply no decrement), and dp
    adds a rep only to the sets that hit their reps.
    """
    new = [copy.deepcopy(s) for s in sets]  # one copy per set: callers may alias dicts
    done = [_set_done(t, l) for t, l in zip(sets, logged)]
    kind = rule["rule"]
    inc = rule["increment_kg"]
    if kind == "linear":
        if not all(done):
            return new, "held", f"missed reps or load in {done.count(False)} of {len(done)} sets"
        if any(s.get("load_kg") is None for s in new):
            return new, "held", "a set has no load to add to"
        for s in new:
            s["load_kg"] = _bump(s["load_kg"], inc)
        return new, "progressed", f"all {len(done)} sets done: +{_kg(inc)} kg on each"
    if kind == "double":
        low, high = rule["low"], rule["high"]
        if all(d and s["reps"] >= high for d, s in zip(done, sets)):
            for s in new:
                s["reps"] = low
                s["load_kg"] = _bump(s["load_kg"], inc)
            return new, "progressed", f"every set reached {high}: +{_kg(inc)} kg, reps back to {low}"
        grew = 0
        for s, d in zip(new, done):
            if d and s["reps"] < high:
                s["reps"] += 1
                grew += 1
        if grew:
            return new, "progressed", f"+1 rep on {grew} of {len(done)} sets"
        return new, "held", "no set both hit its reps and had room below the top of the range"
    total = sum(l["reps"] for l in logged if _num(l.get("reps")))  # reps_threshold
    if total < rule["total"]:
        return new, "held", f"{total} total reps, threshold {rule['total']}"
    if any(s.get("load_kg") is None for s in new):
        return new, "held", "a set has no load to add to"
    for s in new:
        s["load_kg"] = _bump(s["load_kg"], inc)
    return new, "progressed", f"{total} total reps >= {rule['total']}: +{_kg(inc)} kg on each"


def progress_plan(plan: dict, workouts: list[dict], since: str | None = None):
    """Apply each exercise's rule to what was logged in current_week.

    Returns (new_plan, rows, info) and never touches the input. `rows` is one dict
    per exercise of the current week: day, exercise, rule, action ("progressed",
    "held", "unchanged"), why, before (what week current_week+1 said), after.
    When no session of the week was logged, every prescription is held, `info
    ["advanced"]` is False and the plan comes back unchanged (current_week stays).
    A week current_week+1 that is a deload keeps its own prescriptions.
    """
    out = copy.deepcopy(plan)
    cur = out["current_week"]
    wk = next(w for w in out["weeks"] if w["number"] == cur)
    nxt = next((w for w in out["weeks"] if w["number"] == cur + 1), None)
    if nxt is None:
        sys.exit(f"The plan has no week {cur + 1} to write: current_week {cur} is the last. "
                 "Start the next block instead; nothing was changed.")
    floor = max(x for x in (out["created"], out.get("week_started"), since) if x)
    sessions = {d["name"]: _find_session(workouts, d["name"], cur, floor) for d in wk["days"]}
    advanced = any(sessions.values())
    info = {"week": cur, "next_week": cur + 1, "floor": floor, "advanced": advanced,
            "sessions": {n: (_day_of(s["start_time"]) if s else None) for n, s in sessions.items()}}
    rows = []
    for day in wk["days"]:
        session = sessions[day["name"]]
        nday = next((d for d in nxt["days"] if d["name"] == day["name"]), None)
        for ex in day["exercises"]:
            name = ex["exercise"]
            rule = effective_rule(plan, wk, name)
            target = next((e for e in (nday or {}).get("exercises", []) if e["exercise"] == name), None)
            row = {"day": day["name"], "exercise": name, "rule": _rule_text(rule),
                   "action": "unchanged", "why": "",
                   "before": _prescription(_working(target["sets"])) if target else "-"}
            rows.append(row)
            written = None  # the working sets to put into the next week, if any
            if rule is None:
                row["why"] = "deload week: progression off" if wk["deload"] else "no Progression Rule"
            elif "native" in rule:
                row["why"] = f"run by {rule['native']}"
            elif rule["rule"] == "none":
                row["why"] = "rule none: next week stays as planned"
            elif not advanced:
                row.update(action="held", why=f"no session logged in week {cur} since {floor}")
            elif target is None:
                row["why"] = f"not in week {cur + 1} {day['name']}"
            elif nxt["deload"]:
                row["why"] = f"week {cur + 1} is a deload: its prescription is kept"
            else:
                current = _working(ex["sets"])
                logged = _logged_sets(session, name) if session else None
                if session is None:
                    row.update(action="held", why=f"{day['name']} not logged")
                elif logged is None:
                    row.update(action="held", why="not logged in the session")
                elif len(logged) < len(current):
                    row.update(action="held", why=f"partial: {len(logged)} of {len(current)} sets logged")
                else:
                    written, action, why = apply_rule(rule, current, logged)
                    row.update(action=action, why=why)
                if written is None or row["action"] == "held":
                    written = [copy.deepcopy(s) for s in current]  # hold: repeat this week's prescription
            if written is not None:
                target["sets"] = [s for s in target["sets"] if s["kind"] == "warmup"] + written
            row["after"] = _prescription(_working(target["sets"])) if target else "-"
    if not advanced:
        return copy.deepcopy(plan), rows, info
    last = max(_day_of(s["start_time"]) for s in sessions.values() if s)
    out["week_started"] = (dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat()
    out["current_week"] = cur + 1
    return out, rows, info


def _write_json(path: pathlib.Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def format_progress(rows: list[dict], info: dict) -> str:
    n = info["next_week"]
    head = ("day", "exercise", "rule", "result", f"week {n} before", f"week {n} after", "why")
    body = [(r["day"], r["exercise"], r["rule"], r["action"], r["before"], r["after"], r["why"])
            for r in rows]
    widths = [max(len(str(x)) for x in col) for col in zip(head, *body)]
    line = lambda row: "  ".join(str(c).ljust(w) for c, w in zip(row, widths)).rstrip()  # noqa: E731
    return "\n".join([line(head), line(["-" * w for w in widths])] + [line(r) for r in body])


def cmd_progress(a):
    import analyze  # the one Workout Log loader
    p, plan, _ = _checked(a.plan)
    new, rows, info = progress_plan(plan, analyze.load_workouts(), a.since)
    cur = info["week"]
    print(f"Progress {plan['name']}: week {cur} -> week {info['next_week']}")
    seen = ", ".join(f"{n} {d or 'NOT LOGGED'}" for n, d in info["sessions"].items())
    print(f"Sessions since {info['floor']}: {seen}")
    print(format_progress(rows, info))
    if not info["advanced"]:
        print(f"\nNo session of week {cur} is in the Workout Log since {info['floor']}: every "
              f"prescription is held, current_week stays {cur} and the plan is unchanged. "
              "Run `app.py pull` if you have trained, or pass --since if the dates are off.")
        return 0
    errs = validate_plan(new, load_muscles())
    if errs:
        sys.exit("Progression produced an invalid plan; nothing was written:\n"
                 + "\n".join(f"  - {e}" for e in errs))
    if a.dry_run:
        print(f"\n--dry-run: {p} not written. It would advance current_week to {new['current_week']}.")
        return 0
    _write_json(p, new)
    print(f"\nWrote week {info['next_week']} into {p}; current_week is now {new['current_week']}. "
          "Nothing was sent to the app: run `app.py update` to push it.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("validate", cmd_validate), ("stats", cmd_stats), ("render", cmd_render)):
        sp = sub.add_parser(name)
        sp.add_argument("plan")
        if name == "stats":
            sp.add_argument("--json", action="store_true")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("progress")
    sp.add_argument("plan")
    sp.add_argument("--since", help="ignore sessions before this date (YYYY-MM-DD)")
    sp.add_argument("--dry-run", action="store_true", help="print the table, write nothing")
    sp.set_defaults(fn=cmd_progress)
    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
