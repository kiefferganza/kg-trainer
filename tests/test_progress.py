"""plan.py progress: rule-by-rule behaviour, log matching, Native/deload reporting, no-log
behaviour, agreement with Liftosaur's recorded playground run, and progress -> app.py update."""
import copy
import json
import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import analyze
import app
import liftohistory
import plan as planmod
from adapters import hevy

# Fixtures and the HTTP double shared with the Hevy write tests.
from test_hevy_write import api, store  # noqa: F401  (pytest fixtures)
from test_hevy_write import get_shape, serve_folder

FIX = pathlib.Path(__file__).parent / "fixtures"


def _set(reps, load, kind="working", rpe=None):
    return {"reps": reps, "load_kg": load, "rpe": rpe, "kind": kind}


def _ex(name, sets):
    return {"exercise": name, "sets": sets, "rest_seconds": 90, "cue": None,
            "rir_target": None, "superset": None}


def _week(n, bench, lat, curl, raise_, press, deload=False, extra=None):
    wk = {"number": n, "phase": "deload" if deload else "accumulation", "deload": deload,
          "days": [{"name": "Upper A", "exercises": [
              _ex("Bench Press", [_set(8, 60, "warmup")] + [_set(*bench)] * 3),
              _ex("Lat Pulldown", [_set(10, lat)] * 3),
              _ex("Bicep Curl, Cable", [_set(12, curl)] * 2),
              _ex("Lateral Raise", [_set(15, raise_)] * 2),
              _ex("Leg Press", [_set(10, press)] * 2)]}]}
    if extra:
        wk["progression"] = extra
    return wk


@pytest.fixture
def plan():
    return {
        "schema": 1, "name": "Progress Meso", "created": "2026-11-01", "current_week": 1,
        "weeks": [_week(1, (6, 80), 55, 20, 8, 160),
                  _week(2, (6, 82.5), 57.5, 20, 8, 160),
                  _week(3, (6, 70), 45, 15, 6, 120, deload=True,
                        extra={"Lat Pulldown": {"rule": "linear", "increment_kg": 2.5}})],
        "progression": {
            "Bench Press": {"rule": "double", "increment_kg": 2.5, "low": 6, "high": 8},
            "Lat Pulldown": {"rule": "linear", "increment_kg": 2.5},
            "Bicep Curl, Cable": {"rule": "reps_threshold", "total": 24, "increment_kg": 2.5},
            "Lateral Raise": {"rule": "none"},
            "Leg Press": {"native": "liftosaur", "text": "custom() {~ ~}"},
        },
    }


def session(day="Upper A", date="2026-11-03", week=None, **done):
    """A Workout Log session. done maps an exercise to [(reps, kg), ...] working sets."""
    exercises = [{"title": name, "target": None, "note": None,
                  "sets": [{"type": "normal", "reps": r, "weight_kg": kg, "rpe": None}
                           for r, kg in sets]} for name, sets in done.items()]
    return {"start_time": f"{date}T10:00:00Z", "program": None, "day_name": day, "week": week,
            "day_in_week": None, "exercises": exercises}


def all_done(**override):
    """Every prescribed week-1 set completed exactly at target."""
    base = {"Bench Press": [(6, 80)] * 3, "Lat Pulldown": [(10, 55)] * 3,
            "Bicep Curl, Cable": [(12, 20)] * 2, "Lateral Raise": [(15, 8)] * 2,
            "Leg Press": [(10, 160)] * 2}
    base.update(override)
    return base


def run(plan, *sessions, since=None):
    new, rows, info = planmod.progress_plan(plan, list(sessions), since)
    return new, {r["exercise"]: r for r in rows}, info


def week2(new, exercise, day="Upper A"):
    wk = next(w for w in new["weeks"] if w["number"] == 2)
    d = next(d for d in wk["days"] if d["name"] == day)
    ex = next(e for e in d["exercises"] if e["exercise"] == exercise)
    return [(s["reps"], s["load_kg"], s["kind"]) for s in ex["sets"]]


# --- linear ----------------------------------------------------------------

def test_linear_progresses_every_set_when_all_were_done(plan):
    new, rows, _ = run(plan, session(**all_done()))
    assert week2(new, "Lat Pulldown") == [(10, 57.5, "working")] * 3
    assert rows["Lat Pulldown"]["action"] == "progressed"


def test_linear_holds_when_a_set_missed_reps_and_repeats_this_weeks_prescription(plan):
    new, rows, _ = run(plan, session(**all_done(**{"Lat Pulldown": [(10, 55), (10, 55), (9, 55)]})))
    assert week2(new, "Lat Pulldown") == [(10, 55, "working")] * 3   # planned 57.5 is overwritten by the hold
    assert rows["Lat Pulldown"]["action"] == "held" and "missed" in rows["Lat Pulldown"]["why"]


def test_linear_holds_when_lifted_lighter_than_prescribed(plan):
    new, rows, _ = run(plan, session(**all_done(**{"Lat Pulldown": [(10, 50)] * 3})))
    assert rows["Lat Pulldown"]["action"] == "held"


def test_linear_holds_on_a_partial_exercise_and_when_it_was_skipped(plan):
    new, rows, _ = run(plan, session(**all_done(**{"Lat Pulldown": [(10, 55)] * 2})))
    assert rows["Lat Pulldown"]["action"] == "held" and "partial" in rows["Lat Pulldown"]["why"]
    skipped = all_done()
    del skipped["Lat Pulldown"]
    new, rows, _ = run(plan, session(**skipped))
    assert rows["Lat Pulldown"]["action"] == "held" and week2(new, "Lat Pulldown")[0][1] == 55


def test_linear_holds_a_missed_day_but_progresses_the_day_that_was_logged(plan):
    lower = copy.deepcopy(plan["weeks"][0]["days"][0])
    lower["name"] = "Lower A"
    lower["exercises"] = [_ex("Lat Pulldown", [_set(10, 55)] * 3)]
    plan["weeks"][0]["days"].append(lower)
    nxt = copy.deepcopy(lower)
    plan["weeks"][1]["days"].append(nxt)
    new, rows, info = run(plan, session(**all_done()))
    assert info["sessions"] == {"Upper A": "2026-11-03", "Lower A": None}
    lower_row = [r for r in planmod.progress_plan(plan, [session(**all_done())])[1]
                 if r["day"] == "Lower A"][0]
    assert lower_row["action"] == "held" and lower_row["why"] == "Lower A not logged"
    assert week2(new, "Lat Pulldown", "Lower A")[0][1] == 55


# --- double ----------------------------------------------------------------

def test_double_adds_a_rep_to_every_set_at_the_same_load(plan):
    new, rows, _ = run(plan, session(**all_done()))
    assert week2(new, "Bench Press") == [(8, 60, "warmup")] + [(7, 80, "working")] * 3


def test_double_adds_a_rep_only_to_the_sets_that_hit_their_reps(plan):
    new, _, _ = run(plan, session(**all_done(**{"Bench Press": [(6, 80), (6, 80), (5, 80)]})))
    assert week2(new, "Bench Press")[1:] == [(7, 80, "working"), (7, 80, "working"), (6, 80, "working")]


def test_double_at_the_top_of_the_range_adds_load_and_resets_to_low(plan):
    for e in plan["weeks"][0]["days"][0]["exercises"][:1]:
        e["sets"] = [_set(8, 60, "warmup")] + [_set(8, 80)] * 3
    new, rows, _ = run(plan, session(**all_done(**{"Bench Press": [(8, 80)] * 3})))
    assert week2(new, "Bench Press")[1:] == [(6, 82.5, "working")] * 3
    assert rows["Bench Press"]["action"] == "progressed"


def test_double_holds_when_nothing_was_hit_and_on_a_missing_session(plan):
    new, rows, _ = run(plan, session(**all_done(**{"Bench Press": [(4, 80)] * 3})))
    assert rows["Bench Press"]["action"] == "held"
    assert week2(new, "Bench Press")[1:] == [(6, 80, "working")] * 3
    skipped = all_done()
    del skipped["Bench Press"]
    _, rows, _ = run(plan, session(**skipped))
    assert rows["Bench Press"]["action"] == "held" and "not logged" in rows["Bench Press"]["why"]


# --- reps_threshold --------------------------------------------------------

def test_reps_threshold_adds_load_when_total_reps_reach_the_threshold(plan):
    new, rows, _ = run(plan, session(**all_done(**{"Bicep Curl, Cable": [(12, 20), (13, 20)]})))
    assert week2(new, "Bicep Curl, Cable") == [(12, 22.5, "working")] * 2
    assert rows["Bicep Curl, Cable"]["action"] == "progressed"


def test_reps_threshold_holds_below_the_threshold(plan):
    new, rows, _ = run(plan, session(**all_done(**{"Bicep Curl, Cable": [(12, 20), (11, 20)]})))
    assert rows["Bicep Curl, Cable"]["action"] == "held" and "23 total" in rows["Bicep Curl, Cable"]["why"]
    assert week2(new, "Bicep Curl, Cable") == [(12, 20, "working")] * 2


def test_reps_threshold_holds_a_partial_or_skipped_exercise(plan):
    _, rows, _ = run(plan, session(**all_done(**{"Bicep Curl, Cable": [(30, 20)]})))
    assert rows["Bicep Curl, Cable"]["action"] == "held" and "partial" in rows["Bicep Curl, Cable"]["why"]
    skipped = all_done()
    del skipped["Bicep Curl, Cable"]
    _, rows, _ = run(plan, session(**skipped))
    assert rows["Bicep Curl, Cable"]["action"] == "held"


# --- none, Native Rule, deload --------------------------------------------

def test_rule_none_leaves_the_planned_next_week_alone_whatever_was_logged(plan):
    before = week2(plan, "Lateral Raise")
    for done in (all_done(), all_done(**{"Lateral Raise": [(3, 4)]}), {"Bench Press": [(6, 80)] * 3}):
        new, rows, _ = run(plan, session(**done))
        assert week2(new, "Lateral Raise") == before
        assert rows["Lateral Raise"]["action"] == "unchanged"


def test_native_rule_is_reported_as_run_by_the_app_and_not_changed(plan):
    new, rows, _ = run(plan, session(**all_done()))
    assert rows["Leg Press"]["why"] == "run by liftosaur" and rows["Leg Press"]["action"] == "unchanged"
    assert week2(new, "Leg Press") == week2(plan, "Leg Press")


def test_a_deload_week_is_reported_not_progressed(plan):
    plan["current_week"] = 3
    plan["weeks"].append(_week(4, (6, 82.5), 57.5, 20, 8, 160))
    new, rows, _ = run(plan, session(week=3, date="2026-11-20", **{
        "Bench Press": [(6, 70)] * 3}))
    assert rows["Bench Press"]["action"] == "unchanged" and "deload" in rows["Bench Press"]["why"]
    assert rows["Bicep Curl, Cable"]["why"].startswith("deload")
    # the week's own override still progresses the lift it names
    assert rows["Lat Pulldown"]["rule"].startswith("linear")


def test_progress_does_not_overwrite_a_deload_next_week(plan):
    plan["current_week"] = 2
    new, rows, info = planmod.progress_plan(plan, [session(week=2, **all_done(**{
        "Lat Pulldown": [(10, 57.5)] * 3}))])
    assert info["advanced"] and new["current_week"] == 3
    assert week3(new, "Lat Pulldown") == week3(plan, "Lat Pulldown")
    r = {r["exercise"]: r for r in rows}["Lat Pulldown"]
    assert r["action"] == "unchanged" and "deload" in r["why"]


def week3(plan, exercise):
    wk = next(w for w in plan["weeks"] if w["number"] == 3)
    return [e["sets"] for e in wk["days"][0]["exercises"] if e["exercise"] == exercise]


# --- no logged sessions ----------------------------------------------------

def test_a_week_with_no_logged_sessions_holds_everything_and_does_not_advance(plan):
    new, rows, info = run(plan)
    assert new == plan and info["advanced"] is False and new["current_week"] == 1
    for name in ("Bench Press", "Lat Pulldown", "Bicep Curl, Cable"):
        assert rows[name]["action"] == "held" and "no session logged" in rows[name]["why"]
    assert rows["Lateral Raise"]["action"] == "unchanged"


def test_sessions_outside_the_window_do_not_count(plan):
    stale = session(date="2026-10-20", **all_done())            # before the plan was created
    other_week = session(date="2026-11-03", week=3, **all_done())  # logged against another week
    _, _, info = run(plan, stale, other_week)
    assert info["advanced"] is False


# --- matching the log to plan days ----------------------------------------

def test_liftosaur_day_names_carry_a_week_prefix_and_match_case_insensitively(plan):
    _, _, info = run(plan, session(day="Week 1 - upper a", week=1, **all_done()))
    assert info["advanced"] and info["sessions"] == {"Upper A": "2026-11-03"}


def test_a_session_with_a_week_must_match_current_week_one_without_uses_the_date_window(plan):
    assert run(plan, session(week=2, **all_done()))[2]["advanced"] is False
    assert run(plan, session(week=None, **all_done()))[2]["advanced"] is True


def test_the_latest_session_of_a_day_wins(plan):
    early = session(date="2026-11-02", **all_done(**{"Lat Pulldown": [(5, 55)] * 3}))
    late = session(date="2026-11-04", **all_done())
    _, rows, info = run(plan, early, late)
    assert info["sessions"]["Upper A"] == "2026-11-04" and rows["Lat Pulldown"]["action"] == "progressed"


def test_progress_records_week_started_so_the_next_run_ignores_the_consumed_sessions(plan):
    done = session(date="2026-11-03", **all_done())
    new, _, _ = run(plan, done)
    assert new["current_week"] == 2 and new["week_started"] == "2026-11-04"
    _, _, info = run(new, done)   # week-less log, same old session
    assert info["advanced"] is False and info["floor"] == "2026-11-04"


def test_since_moves_the_window_floor(plan):
    _, _, info = run(plan, session(date="2026-11-03", **all_done()), since="2026-11-05")
    assert info["advanced"] is False


def test_the_last_week_has_nothing_to_write_into(plan):
    plan["current_week"] = 3
    with pytest.raises(SystemExit, match="no week 4"):
        planmod.progress_plan(plan, [session(week=3, **all_done())])


def test_progress_never_mutates_its_input(plan):
    snapshot = copy.deepcopy(plan)
    planmod.progress_plan(plan, [session(**all_done())])
    assert plan == snapshot


# --- the CLI ---------------------------------------------------------------

@pytest.fixture
def planfile(tmp_path, plan, monkeypatch):
    path = tmp_path / "meso-01.plan.json"
    path.write_text(json.dumps(plan))
    return path


def write_log(monkeypatch, tmp_path, *workouts):
    log = tmp_path / "workouts.jsonl"
    log.write_text("".join(json.dumps(w) + "\n" for w in workouts))
    monkeypatch.setattr(analyze, "WORKOUTS", log)


def ns(**kw):
    return type("A", (), kw)()


def test_cli_writes_the_next_week_advances_and_prints_the_table(planfile, tmp_path, monkeypatch, capsys):
    write_log(monkeypatch, tmp_path, session(**all_done()))
    planmod.cmd_progress(ns(plan=str(planfile), since=None, dry_run=False))
    out = capsys.readouterr().out
    saved = json.loads(planfile.read_text())
    assert saved["current_week"] == 2 and week2(saved, "Lat Pulldown")[0][1] == 57.5
    assert "week 2 before" in out and "week 2 after" in out and "run by liftosaur" in out
    assert "Nothing was sent to the app" in out
    assert planmod.validate_plan(saved, analyze.load_muscles()) == []


def test_cli_dry_run_and_no_log_write_nothing(planfile, tmp_path, monkeypatch, capsys):
    original = planfile.read_text()
    write_log(monkeypatch, tmp_path, session(**all_done()))
    planmod.cmd_progress(ns(plan=str(planfile), since=None, dry_run=True))
    assert planfile.read_text() == original and "--dry-run" in capsys.readouterr().out
    write_log(monkeypatch, tmp_path)
    planmod.cmd_progress(ns(plan=str(planfile), since=None, dry_run=False))
    assert planfile.read_text() == original
    out = capsys.readouterr().out
    assert "No session of week 1" in out and "current_week stays 1" in out


# --- agreement with Liftosaur's recorded playground run -------------------

def liftosaur_week1_exercise(text, name):
    """Sets (count-expanded (reps, kg)) Liftosaur wrote for an exercise in Week 1 Upper A of
    updatedProgramText. Handles `3x7 / 80kg 180s` and `2x10 57.5kg, 1x10 47.5kg`."""
    week1 = text.split("# Week 2")[0]
    line = next(l for l in week1.splitlines() if l.startswith(name + " /"))
    parts = [p.strip() for p in line.split(" / ")]
    groups, tail = [g.strip() for g in parts[1].split(",")], parts[2].split()
    out = []
    for g in groups:
        tokens = g.split()
        count, reps = tokens[0].rstrip("+").split("x")
        weight = tokens[1] if len(tokens) > 1 else tail[0]
        out += [(int(reps), float(weight.removesuffix("kg")))] * int(count)
    return out


def test_progress_agrees_with_liftosaurs_recorded_finish_workout():
    rec = json.loads((FIX / "playground_finish.json").read_text())
    plan = json.loads((FIX / "liftosaur_plan.json").read_text())
    plan["created"] = "2026-11-01"   # the run was recorded the day before the fixture's date
    # The logged sets are the ones Liftosaur recorded for the finished workout: every
    # target set completed, read back through the Liftosaur history parser.
    logged = liftohistory.parse_liftohistory(rec["response"]["workout"])
    assert len(logged) == 1 and logged[0]["week"] == 1
    new, rows, info = planmod.progress_plan(plan, logged)
    assert info["advanced"] and new["current_week"] == 2
    got = {r["exercise"]: r for r in rows}
    updated = rec["response"]["updatedProgramText"]
    for name in ("Bench Press", "Lat Pulldown"):
        wk2 = next(e for d in next(w for w in new["weeks"] if w["number"] == 2)["days"]
                   if d["name"] == "Upper A" for e in d["exercises"] if e["exercise"] == name)
        mine = [(s["reps"], s["load_kg"]) for s in wk2["sets"] if s["kind"] != "warmup"]
        assert mine == liftosaur_week1_exercise(updated, name), name
        assert got[name]["action"] == "progressed"
    # Liftosaur's concrete numbers, spelled out
    assert liftosaur_week1_exercise(updated, "Bench Press") == [(7, 80.0)] * 3
    assert liftosaur_week1_exercise(updated, "Lat Pulldown") == [
        (10, 57.5), (10, 57.5), (10, 47.5), (12, 42.5)]
    # The fixture's own authored week 2 (82.5 kg bench, two lat pulldown sets) is overwritten:
    old = next(w for w in plan["weeks"] if w["number"] == 2)["days"][0]["exercises"][0]["sets"]
    assert [s["load_kg"] for s in old] == [82.5] * 3
    # and a Native Rule exercise is left to the app
    assert got["Leg Press"]["why"] == "run by liftosaur"


# --- progress, then update moves a Hevy account to the next week ----------

def test_progress_then_update_pushes_the_next_week_to_hevy(store, api, tmp_path, monkeypatch, capsys):  # noqa: F811
    plan = {
        "schema": 1, "name": "Hevy Meso", "created": "2026-11-01", "current_week": 1,
        "weeks": [{"number": n, "phase": "accumulation", "deload": False, "days": [
            {"name": "Upper A", "exercises": [
                _ex("Bench Press", [_set(6, load)] * 3),
                _ex("Lat Pulldown", [_set(10, lat)] * 2)]},
            {"name": "Lower A", "exercises": [_ex("Squat", [_set(5, 100)] * 3)]}]}
            for n, load, lat in ((1, 80, 55), (2, 82.5, 57.5))],
        "progression": {"Bench Press": {"rule": "double", "increment_kg": 2.5, "low": 6, "high": 8},
                        "Lat Pulldown": {"rule": "linear", "increment_kg": 2.5}},
    }
    path = tmp_path / "meso-01.plan.json"
    path.write_text(json.dumps(plan))
    # Hevy's log has no `week`: the date window matches, and the workout title is the day name.
    write_log(monkeypatch, tmp_path, session(**{"Bench Press": [(6, 80)] * 3, "Lat Pulldown": [(10, 55)] * 2}),
              session(day="Lower A", date="2026-11-04", **{"Squat": [(5, 100)] * 3}))
    app.cmd_use(ns(name="hevy"))
    planmod.cmd_progress(ns(plan=str(path), since=None, dry_run=False))
    saved = json.loads(path.read_text())
    assert saved["current_week"] == 2
    # update compiles current_week (now 2): the progressed prescriptions, not the old week 2 plan
    serve_folder(api, [get_shape("r1", "Upper A"), get_shape("r2", "Lower A")])
    api.routes[("PUT", "/v1/routines/r1")] = {"routine": [{"id": "r1"}]}
    api.routes[("PUT", "/v1/routines/r2")] = {"routine": [{"id": "r2"}]}
    capsys.readouterr()
    app.cmd_update(ns(plan=str(path), id="3670546", week=None, yes=True))
    assert "Updated hevy plan 3670546" in capsys.readouterr().out
    puts = {p[1]: p[2]["routine"] for p in api.log if p[0] == "PUT"}
    bench, lat = puts["/v1/routines/r1"]["exercises"]
    assert [s["rep_range"] for s in bench["sets"]] == [{"start": 6, "end": 8}] * 3  # Hevy shows the range
    assert {s["weight_kg"] for s in bench["sets"]} == {80}            # double: reps up, load held
    assert {s["weight_kg"] for s in lat["sets"]} == {57.5}            # linear: +2.5 on 55
    assert hevy.compile(saved)["week"] == 2
