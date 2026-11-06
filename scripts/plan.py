#!/usr/bin/env python3
"""Author, check and render a Canonical Plan. Local only, for every Training App.

A Canonical Plan is plans/meso-NN.plan.json (see the app-agnostic adapters spec).
This script owns that file's shape; meso-NN.md is generated from it by `render`
and is never hand-edited.

Usage:
  plan.py validate <plan>        # name every failure, exit 1 if any
  plan.py stats <plan> [--json]  # working sets per muscle, per week
  plan.py render <plan>          # write meso-NN.md next to the plan

Equipment for the 2.5 kg rule is read from the Canonical Exercise Name suffix
(", Barbell", ", Smith Machine", ", EZ Bar", ", Trap Bar"). A few names carry no
suffix because Liftosaur's built-in list treats the bare name as the barbell lift
(Squat, Bench Press, Deadlift...); BARE_BARBELL lists them. Dumbbell and machine
loads are not checked.
"""

from __future__ import annotations

import argparse
import json
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("validate", cmd_validate), ("stats", cmd_stats), ("render", cmd_render)):
        sp = sub.add_parser(name)
        sp.add_argument("plan")
        if name == "stats":
            sp.add_argument("--json", action="store_true")
        sp.set_defaults(fn=fn)
    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
