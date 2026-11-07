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
from adapters import contract, hevy

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
    # the authored week 2 (57.5) takes the +2.5 too: the change is added to every later week
    assert week2(new, "Lat Pulldown") == [(10, 60, "working")] * 3
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
    # week 2 keeps its authored 82.5 kg and gains the rep
    assert week2(new, "Bench Press") == [(8, 60, "warmup")] + [(7, 82.5, "working")] * 3


def test_double_adds_a_rep_only_to_the_sets_that_hit_their_reps(plan):
    new, _, _ = run(plan, session(**all_done(**{"Bench Press": [(6, 80), (6, 80), (5, 80)]})))
    assert week2(new, "Bench Press")[1:] == [(7, 82.5, "working"), (7, 82.5, "working"),
                                             (6, 82.5, "working")]


def test_double_at_the_top_of_the_range_adds_load_and_resets_to_low(plan):
    for e in plan["weeks"][0]["days"][0]["exercises"][:1]:
        e["sets"] = [_set(8, 60, "warmup")] + [_set(8, 80)] * 3
    new, rows, _ = run(plan, session(**all_done(**{"Bench Press": [(8, 80)] * 3})))
    # at the top: reps are `low` (absolute) in every week and +2.5 kg is added to week 2's 82.5
    assert week2(new, "Bench Press")[1:] == [(6, 85, "working")] * 3
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
    assert week2(new, "Bicep Curl, Cable") == [(12, 22.5, "working")] * 2  # authored 20 + 2.5
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


def rows_by(rows):
    return {r["exercise"]: r for r in rows}


def test_a_fired_rule_reaches_a_deload_next_week_keeping_its_authored_offset(plan):
    plan["current_week"] = 2
    new, rows, info = planmod.progress_plan(plan, [session(week=2, **all_done(**{
        "Lat Pulldown": [(10, 57.5)] * 3}))])
    assert info["advanced"] and new["current_week"] == 3
    # week 3 is a deload (3 x 10 @ 45, rule overridden to linear): it gains the +2.5 as well
    assert [(s["reps"], s["load_kg"]) for s in week3(new, "Lat Pulldown")[0]] == [(10, 47.5)] * 3
    assert rows_by(rows)["Lat Pulldown"]["action"] == "progressed"
    # a held exercise does not repeat this week into the deload
    new, rows, _ = planmod.progress_plan(plan, [session(week=2, **all_done(**{
        "Lat Pulldown": [(10, 50)] * 3}))])
    assert rows_by(rows)["Lat Pulldown"]["action"] == "held"
    assert week3(new, "Lat Pulldown") == week3(plan, "Lat Pulldown")


def test_a_fired_rule_changes_the_week_just_done_and_every_later_week(plan):
    plan["weeks"].append(_week(4, (6, 85), 60, 20, 8, 160))
    new, _, _ = run(plan, session(**all_done()))
    for n, bench in ((1, 80), (2, 82.5), (3, 70), (4, 85)):
        wk = next(w for w in new["weeks"] if w["number"] == n)
        sets = [s for s in wk["days"][0]["exercises"][0]["sets"] if s["kind"] != "warmup"]
        assert {s["reps"] for s in sets} == {7} and {s["load_kg"] for s in sets} == {bench}, n
    # Liftosaur's updated program text also shows the week just done progressed (3x7 at 80 kg)
    assert new["weeks"][0] != plan["weeks"][0]


def test_a_none_rule_or_a_deload_current_week_triggers_nothing_in_later_weeks(plan):
    new, _, _ = run(plan, session(**all_done()))
    assert week2(new, "Lateral Raise") == week2(plan, "Lateral Raise")      # rule none
    assert week3(new, "Lateral Raise") == week3(plan, "Lateral Raise")
    plan["current_week"] = 3
    plan["weeks"].append(_week(4, (6, 82.5), 57.5, 20, 8, 160))
    new, rows, _ = planmod.progress_plan(plan, [session(week=3, date="2026-11-20", **{
        "Bench Press": [(6, 70)] * 3, "Lat Pulldown": [(10, 45)] * 3})])
    wk4 = lambda p: next(w for w in p["weeks"] if w["number"] == 4)["days"][0]["exercises"]  # noqa: E731
    by = {e["exercise"]: e["sets"] for e in wk4(new)}
    old = {e["exercise"]: e["sets"] for e in wk4(plan)}
    assert by["Bench Press"] == old["Bench Press"]                  # deload: rule off, nothing fires
    assert by["Bicep Curl, Cable"] == old["Bicep Curl, Cable"]
    # only the lift the deload names a rule for (Lat Pulldown, linear) carries on into week 4
    assert [s["load_kg"] for s in by["Lat Pulldown"]] == [60] * 3


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
    assert saved["current_week"] == 2 and week2(saved, "Lat Pulldown")[0][1] == 60
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

def recorded_progression():
    """(our progressed plan, rows, Liftosaur's updatedProgramText) for the recorded run."""
    rec = json.loads((FIX / "playground_finish.json").read_text())
    plan = json.loads((FIX / "liftosaur_plan.json").read_text())
    plan["created"] = "2026-11-01"   # the run was recorded the day before the fixture's date
    # The logged sets are the ones Liftosaur recorded for the finished workout: every
    # target set completed, read back through the Liftosaur history parser.
    logged = liftohistory.parse_liftohistory(rec["response"]["workout"])
    assert len(logged) == 1 and logged[0]["week"] == 1
    new, rows, info = planmod.progress_plan(plan, logged)
    assert info["advanced"] and new["current_week"] == 2
    return new, {r["exercise"]: r for r in rows}, rec["response"]["updatedProgramText"]


def liftosaur_week(text, week):
    """{(day, exercise): [(reps, kg, amrap), ...]} for one week of Liftosaur's own program
    text, parsed from its reformatted lines (`3x7 / 82.5kg 180s`, `2x10 57.5kg, 1x12+ 42.5kg`)."""
    from adapters import liftosaur
    return {(d, e): sets for d, e, sets in liftosaur.parse_program(text)[week]}


def test_progress_agrees_with_liftosaurs_recorded_finish_workout_in_every_week():
    new, got, updated = recorded_progression()
    weeks = sorted(w["number"] for w in new["weeks"])
    assert weeks == [1, 2, 3]
    for n in weeks:
        mine = {(d, e): sets for d, e, sets in contract.plan_prescriptions(new, n)}
        theirs = liftosaur_week(updated, n)
        assert mine.keys() == theirs.keys(), n
        for key in mine:   # every exercise, not only the two that progressed
            assert contract._norm(mine[key]) == contract._norm(theirs[key]), (n, key)
    # Liftosaur's concrete numbers, spelled out: the weeks differ in their authored
    # offsets and each takes the same change.
    bench = {n: liftosaur_week(updated, n)[("Upper A", "Bench Press")] for n in weeks}
    assert bench[1] == [(7, 80.0, False)] * 3          # 3x6 80kg
    assert bench[2] == [(7, 82.5, False)] * 3          # 3x6 82.5kg
    assert bench[3] == [(7, 70.0, False)] * 2          # deload 2x6 70kg, `progress: none`
    lat = {n: liftosaur_week(updated, n)[("Upper A", "Lat Pulldown")] for n in weeks}
    assert lat[1] == [(10, 57.5, False)] * 2 + [(10, 47.5, False), (12, 42.5, True)]
    assert lat[2] == [(10, 60.0, False)] * 2           # 57.5 + 2.5
    assert lat[3] == [(10, 47.5, False)] * 2           # deload 45 + 2.5
    assert got["Bench Press"]["action"] == got["Lat Pulldown"]["action"] == "progressed"
    # Not logged in the recorded run, so nothing moved in either program, and a Native
    # Rule exercise is left to the app.
    assert got["Bicep Curl, Cable"]["action"] == "held"
    assert got["Leg Press"]["why"] == "run by liftosaur"


# --- agreement with the dp top/mid range and sum recordings ---------------

def plan_from_program(text):
    """A Canonical Plan from a recorded Liftoscript program: rules from each exercise's
    `progress:` in week 1, sets from the parser."""
    import re
    from adapters import liftosaur
    rules = {}
    for line in text.splitlines():
        m = re.search(r"progress: (lp|dp|sum)\(([^)]*)\)", line)
        if not m:
            continue
        args = [a.strip() for a in m[2].split(",")]
        kg = lambda x: float(x.removesuffix("kg"))  # noqa: E731
        rules[line.split(" / ")[0]] = (
            {"rule": "linear", "increment_kg": kg(args[0])} if m[1] == "lp" else
            {"rule": "double", "increment_kg": kg(args[0]), "low": int(args[1]), "high": int(args[2])}
            if m[1] == "dp" else
            {"rule": "reps_threshold", "total": int(args[0]), "increment_kg": kg(args[1])})
    weeks = []
    for n, rows in sorted(liftosaur.parse_program(text).items()):
        days = {}
        for day, name, sets in rows:
            days.setdefault(day, []).append(_ex(name, [_set(r, kg, "working") for r, kg, _ in sets]))
        weeks.append({"number": n, "phase": "x", "deload": False,
                      "days": [{"name": d, "exercises": e} for d, e in days.items()]})
    return {"schema": 1, "name": "rec", "created": "2026-11-01", "current_week": 1,
            "weeks": weeks, "progression": rules}


@pytest.mark.parametrize("fixture,expected", [
    ("playground_top_of_range.json", {
        # dp at the top: reps -> low in every week, load += 2.5 on each week's own load;
        # sum fired: load -> logged 20 + 2.5 everywhere; lp(1kg): +1 on 8 / 9 / 6
        "Bench Press": [[(6, 82.5)] * 3, [(6, 85.0)] * 3, [(6, 72.5)] * 2],
        "Bicep Curl, Cable": [[(15, 22.5)] * 2, [(12, 22.5)] * 2, [(15, 22.5)] * 1],
        "Lateral Raise": [[(15, 9.0)] * 2, [(12, 10.0)] * 2, [(15, 7.0)] * 1]}),
    ("playground_mid_range.json", {
        # dp below the top: reps -> logged 6 + 1 in every week, loads untouched;
        # sum(15) fired on 100 kg: load -> 102.5 in every week, reps untouched
        "Bench Press": [[(7, 80.0)] * 3, [(7, 82.5)] * 3, [(7, 70.0)] * 2],
        "Squat": [[(5, 102.5)] * 3, [(3, 102.5)] * 3, [(5, 102.5)] * 2]}),
])
def test_progress_agrees_with_the_dp_and_sum_recordings_in_every_week(fixture, expected):
    from adapters import liftosaur
    rec = json.loads((FIX / fixture).read_text())
    updated = rec["response"]["updatedProgramText"]
    plan = plan_from_program(rec["program_text"])
    # The recorded workout is used as logged. In the top-of-range run the playground
    # rounded a prescribed 8 kg Lateral Raise to 7.5 kg and logged `target: ... 7.5kg`;
    # progress counts it as done because it reached the app's own target.
    logged = liftohistory.parse_liftohistory(rec["response"]["workout"])
    new, rows, info = planmod.progress_plan(plan, logged)
    assert info["advanced"]
    theirs_all = liftosaur.parse_program(updated)
    for n in (1, 2, 3):
        mine = {(d, e): sets for d, e, sets in contract.plan_prescriptions(new, n)}
        theirs = {(d, e): sets for d, e, sets in theirs_all[n]}
        assert mine.keys() == theirs.keys()
        for key in mine:
            assert contract._norm(mine[key]) == contract._norm(theirs[key]), (fixture, n, key)
    for name, per_week in expected.items():
        for n, want in zip((1, 2, 3), per_week):
            got = [(r, kg) for r, kg, _ in {e: s for d, e, s in theirs_all[n]}[name]]
            assert got == want, (fixture, name, n)
        assert {r["exercise"]: r["action"] for r in rows}[name] == "progressed"


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
    assert {s["weight_kg"] for s in bench["sets"]} == {82.5}          # authored week 2 load, rep added
    assert {s["weight_kg"] for s in lat["sets"]} == {60}              # authored week 2 57.5, +2.5
    assert hevy.compile(saved)["week"] == 2


# --- the app's own rounding --------------------------------------------------

def with_target(sess, exercise, target):
    for e in sess["exercises"]:
        if e["title"] == exercise:
            e["target"] = target
    return sess


def test_a_load_the_app_rounded_down_counts_as_done(plan):
    # Live 2026-11-07: Liftosaur rounded a prescribed 8 kg Lateral Raise to 7.5 kg,
    # logged its target as 7.5 kg, and counted the sets done. The skill must agree.
    plan["progression"]["Lat Pulldown"] = {"rule": "linear", "increment_kg": 2.5}
    sess = with_target(session(**all_done(**{"Lat Pulldown": [(10, 52.5)] * 3})),
                       "Lat Pulldown", "3x10 52.5kg 90s")
    _, rows, _ = run(plan, sess)
    assert rows["Lat Pulldown"]["action"] == "progressed"


def test_without_an_app_target_a_lighter_load_still_holds(plan):
    _, rows, _ = run(plan, session(**all_done(**{"Lat Pulldown": [(10, 52.5)] * 3})))
    assert rows["Lat Pulldown"]["action"] == "held"


def test_a_logged_load_below_the_apps_own_target_still_holds(plan):
    sess = with_target(session(**all_done(**{"Lat Pulldown": [(10, 52.5)] * 3})),
                       "Lat Pulldown", "3x10 55kg 90s")
    _, rows, _ = run(plan, sess)
    assert rows["Lat Pulldown"]["action"] == "held"


def test_an_app_target_above_the_prescription_never_raises_the_bar(plan):
    sess = with_target(session(**all_done()), "Lat Pulldown", "3x10 60kg 90s")
    _, rows, _ = run(plan, sess)
    assert rows["Lat Pulldown"]["action"] == "progressed"


def test_the_app_target_is_matched_set_by_set(plan):
    sess = with_target(session(**all_done(**{"Lat Pulldown": [(10, 55), (10, 55), (10, 52.5)]})),
                       "Lat Pulldown", "2x10 55kg 90s, 1x10 52.5kg 90s")
    _, rows, _ = run(plan, sess)
    assert rows["Lat Pulldown"]["action"] == "progressed"
