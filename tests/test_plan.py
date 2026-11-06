import copy
import json
import pathlib
import subprocess
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import analyze
import plan as planmod

PLAN_PY = SCRIPTS / "plan.py"


def _set(reps, load, kind="working", rpe=None):
    return {"reps": reps, "load_kg": load, "rpe": rpe, "kind": kind}


def _ex(name, sets, **kw):
    base = {"exercise": name, "sets": sets, "rest_seconds": 120, "cue": None,
            "rir_target": 2, "superset": None}
    base.update(kw)
    return base


def _week(n, phase, deload, load_bench, extra=None):
    wk = {"number": n, "phase": phase, "deload": deload, "days": [
        {"name": "Upper A", "exercises": [
            _ex("Bench Press", [_set(5, 60, "warmup")] + [_set(8, load_bench)] * 3,
                cue="Pause | one inch off the chest."),
            _ex("Lat Pulldown", [_set(10, 50)] * 3, superset="A"),
            _ex("Bicep Curl", [_set(12, 12.5)] * 2, superset="A"),
        ]},
        {"name": "Lower A", "exercises": [
            _ex("Squat", [_set(6, 100)] * 4),
            _ex("Leg Press", [_set(10, 140)] * 2),
        ]},
    ]}
    if extra:
        wk["progression"] = extra
    return wk


@pytest.fixture
def plan():
    return {
        "schema": 1, "name": "Meso 2 - Upper/Lower", "created": "2026-11-07", "current_week": 1,
        "weeks": [
            _week(1, "accumulation", False, 80),
            _week(2, "accumulation", False, 82.5),
            _week(3, "deload", True, 70,
                  extra={"Squat": {"rule": "linear", "increment_kg": 2.5}}),
        ],
        "progression": {
            "Bench Press": {"rule": "double", "increment_kg": 2.5, "low": 6, "high": 8},
            "Squat": {"rule": "reps_threshold", "total": 24, "increment_kg": 5},
            "Lat Pulldown": {"rule": "linear", "increment_kg": 2.5},
            "Bicep Curl": {"rule": "none"},
            "Leg Press": {"native": "liftosaur", "text": "custom() {~ ~}"},
        },
    }


@pytest.fixture
def muscles(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "PERSONAL_MUSCLE_MAP", tmp_path / "no-personal.json")
    return analyze.load_muscles()


def errors(plan, muscles):
    return planmod.validate_plan(plan, muscles)


def test_multiweek_plan_with_deload_and_override_is_valid(plan, muscles):
    assert errors(plan, muscles) == []


def test_unknown_exercise_name_is_named(plan, muscles):
    plan["weeks"][0]["days"][0]["exercises"][0]["exercise"] = "Bench Pres"
    out = errors(plan, muscles)
    assert any("Bench Pres" in e and "unknown exercise" in e for e in out)


def test_personal_map_makes_a_custom_exercise_known(plan, tmp_path, monkeypatch):
    personal = tmp_path / "personal.json"
    personal.write_text(json.dumps({"Zottman Thing, Cable": "biceps"}))
    monkeypatch.setattr(analyze, "PERSONAL_MUSCLE_MAP", personal)
    plan["weeks"][0]["days"][0]["exercises"][1]["exercise"] = "Zottman Thing, Cable"
    assert errors(plan, analyze.load_muscles()) == []


def test_unknown_name_in_progression_maps_is_rejected(plan, muscles):
    plan["progression"]["Mystery Lift"] = {"rule": "none"}
    plan["weeks"][2]["progression"]["Other Mystery"] = {"rule": "none"}
    out = errors(plan, muscles)
    assert any("Mystery Lift" in e for e in out) and any("Other Mystery" in e for e in out)


@pytest.mark.parametrize("rule", [
    {"rule": "exponential", "increment_kg": 2.5},
    {"rule": "linear"},
    {"rule": "linear", "increment_kg": -2.5},
    {"rule": "linear", "increment_kg": "2.5"},
    {"rule": "double", "increment_kg": 2.5, "low": 8, "high": 6},
    {"rule": "double", "increment_kg": 2.5, "low": 6},
    {"rule": "reps_threshold", "increment_kg": 2.5},
    {"rule": "none", "increment_kg": 2.5},
    {"rule": "linear", "increment_kg": 2.5, "surprise": 1},
    {"native": "liftosaur"},
    {"native": "", "text": "x"},
    "linear",
])
def test_malformed_progression_rules_are_rejected(plan, muscles, rule):
    plan["progression"]["Lat Pulldown"] = rule
    out = errors(plan, muscles)
    assert out and all("progression.Lat Pulldown" in e for e in out)


@pytest.mark.parametrize("name", [
    "Bicep Curl, Barbell", "Bench Press, Smith Machine", "Bicep Curl, EZ Bar", "Squat",
    "Trap Bar Deadlift"])
def test_barbell_type_load_off_the_2_5_grid_is_rejected(plan, muscles, name):
    plan["weeks"][1]["days"][0]["exercises"][1] = _ex(name, [_set(8, 41.0), _set(8, 40)])
    out = errors(plan, muscles)
    assert len(out) == 1
    assert name in out[0] and "41" in out[0] and "week 2" in out[0] and "2.5" in out[0]


def test_barbell_increment_off_the_grid_is_rejected(plan, muscles):
    plan["progression"]["Squat"] = {"rule": "linear", "increment_kg": 1.25}
    assert any("increment_kg 1.25" in e for e in errors(plan, muscles))


def test_dumbbell_and_machine_loads_are_not_checked(plan, muscles):
    plan["weeks"][0]["days"][0]["exercises"][2]["sets"] = [_set(12, 13.7)]
    plan["weeks"][0]["days"][1]["exercises"][1]["sets"] = [_set(10, 143.3)]
    assert errors(plan, muscles) == []


def test_every_failure_is_named_not_just_the_first(plan, muscles):
    plan["weeks"][0]["days"][0]["exercises"][0]["exercise"] = "Nope"
    plan["weeks"][1]["days"][1]["exercises"][0]["sets"][0]["load_kg"] = 101
    plan["progression"]["Lat Pulldown"] = {"rule": "bogus"}
    plan["current_week"] = 9
    assert len(errors(plan, muscles)) >= 4


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(schema=2),
    lambda p: p.update(weeks=[]),
    lambda p: p.update(created="Nov 7"),
    lambda p: p["weeks"][0].update(deload="no"),
    lambda p: p["weeks"][1].update(number=1),
    lambda p: p["weeks"][0]["days"][0]["exercises"][0]["sets"][0].update(kind="heroic"),
    lambda p: p["weeks"][0]["days"][0]["exercises"][0]["sets"][0].update(reps=0),
    lambda p: p["weeks"][0]["days"][0]["exercises"][0].update(sets=[]),
])
def test_structural_problems_are_rejected(plan, muscles, mutate):
    mutate(plan)
    assert errors(plan, muscles)


def test_native_rule_validates_and_keeps_its_app_tag(plan, muscles, tmp_path):
    assert errors(plan, muscles) == []
    p = tmp_path / "meso-02.plan.json"
    p.write_text(json.dumps(plan))
    out = subprocess.run([sys.executable, str(PLAN_PY), "validate", str(p)],
                         capture_output=True, text=True)
    assert out.returncode == 0 and "liftosaur" in out.stdout
    assert json.loads(p.read_text())["progression"]["Leg Press"]["native"] == "liftosaur"


def test_effective_rule_deload_off_and_week_override(plan):
    w1, _, w3 = plan["weeks"]
    assert planmod.effective_rule(plan, w1, "Squat")["rule"] == "reps_threshold"
    assert planmod.effective_rule(plan, w3, "Bench Press") is None       # deload: off
    assert planmod.effective_rule(plan, w3, "Squat") == {"rule": "linear", "increment_kg": 2.5}
    assert planmod.effective_rule(plan, w1, "Squat") is not plan["weeks"][2]["progression"]["Squat"]


def test_weekly_sets_exclude_warmups_and_use_the_muscle_map(plan, muscles):
    stats = planmod.weekly_sets(plan, muscles)
    w1 = stats[1]
    assert w1["chest"] == 3                       # 3 working + 1 warmup
    assert w1["lats"] == 3 and w1["biceps"] == 2
    assert w1["quadriceps"] == 4 + 2              # Squat + Leg Press
    assert set(stats) == {1, 2, 3}


def test_stats_cli_prints_per_week(plan, tmp_path):
    p = tmp_path / "meso-02.plan.json"
    p.write_text(json.dumps(plan))
    out = subprocess.run([sys.executable, str(PLAN_PY), "stats", str(p)],
                         capture_output=True, text=True)
    assert out.returncode == 0
    for n in (1, 2, 3):
        assert f"Week {n}" in out.stdout
    assert "deload" in out.stdout and "chest" in out.stdout


def test_validate_cli_exits_nonzero_and_names_failures(plan, tmp_path):
    plan["weeks"][0]["days"][0]["exercises"][0]["exercise"] = "Nope Lift"
    plan["weeks"][1]["days"][1]["exercises"][0]["sets"][0]["load_kg"] = 101
    p = tmp_path / "bad.plan.json"
    p.write_text(json.dumps(plan))
    out = subprocess.run([sys.executable, str(PLAN_PY), "validate", str(p)],
                         capture_output=True, text=True)
    assert out.returncode == 1
    assert "Nope Lift" in out.stdout and "101" in out.stdout


def test_render_writes_a_table_per_session_and_refuses_invalid(plan, tmp_path):
    p = tmp_path / "meso-02.plan.json"
    p.write_text(json.dumps(plan))
    out = subprocess.run([sys.executable, str(PLAN_PY), "render", str(p)],
                         capture_output=True, text=True)
    assert out.returncode == 0
    md = (tmp_path / "meso-02.md").read_text()
    assert md.count("### Upper A") == 3 and md.count("### Lower A") == 3
    assert md.count("| # | Exercise |") == 6
    assert "## Week 3: deload (deload, progression off)" in md
    assert "3 x 8 @ 80 kg" in md and "1 x 5 @ 60 kg (warmup)" in md
    assert "Pause \\| one inch" in md                      # pipes escaped in cells
    assert "double 6-8, +2.5 kg" in md and "native (liftosaur)" in md
    assert "Do not edit" in md
    week3 = md.split("## Week 3")[1]
    assert "linear +2.5 kg" in week3                       # the Squat override
    assert "double 6-8" not in week3                       # Bench is off in the deload

    bad = copy.deepcopy(plan)
    bad["weeks"][0]["days"][0]["exercises"][0]["exercise"] = "Nope"
    q = tmp_path / "meso-03.plan.json"
    q.write_text(json.dumps(bad))
    out = subprocess.run([sys.executable, str(PLAN_PY), "render", str(q)],
                         capture_output=True, text=True)
    assert out.returncode != 0 and not (tmp_path / "meso-03.md").exists()
