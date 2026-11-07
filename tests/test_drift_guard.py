"""The drift guard: `app.py update` on an app that progresses natively refuses when the
app's current week differs from the Canonical Plan. No network: the app is a stub."""
import copy
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import app
from adapters import contract
from adapters import hevy
from adapters import liftosaur as lift

FIX = pathlib.Path(__file__).parent / "fixtures"
LIVE = json.loads((FIX / "liftosaur_get_program.live.json").read_text())
LIVE_PLAN = json.loads((FIX / "liftosaur_get_program.plan.json").read_text())
PLAN = json.loads((FIX / "liftosaur_plan.json").read_text())
GOLDEN = (FIX / "liftosaur_compiled.golden.liftoscript").read_text()


def ns(**kw):
    return type("A", (), kw)()


# --- the declaration --------------------------------------------------------

def test_liftosaur_declares_native_progression_and_hevy_does_not():
    assert contract.native_progression(lift) is True
    assert contract.native_progression(hevy) is False


# --- parsing the app's program text -----------------------------------------

def week(text, n):
    return {(d, e): s for d, e, s in lift.parse_program(text)[n]}


def test_the_live_get_parses_to_the_plan_it_was_pushed_from_in_every_week():
    for n in (1, 2, 3):
        assert contract.drift(lift.prescriptions(LIVE, n),
                              contract.plan_prescriptions(LIVE_PLAN, n)) == [], n
    w1 = week(LIVE["text"], 1)
    assert w1[("Upper A", "Lat Pulldown")] == [(10, 55.0, False), (10, 55.0, False),
                                               (10, 45.0, False), (12, 40.0, True)]
    assert w1[("Lower A", "Pull Up")] == [(8, 0.0, False)] * 2


REFORMATTED = """# Week 1
## Upper A
// Target: 3 RIR
Bench Press / 3x7 / 80kg 180s / warmup: 1x8 60kg / progress: dp(2.5kg, 6, 8)
Lat Pulldown / 2x10 57.5kg, 1x10 47.5kg, 1x12+ 42.5kg / 90s / warmup: none / superset: A / progress: lp(2.5kg)
Bicep Curl, Cable / 2x12 / 20kg @8 60s / warmup: none / progress: sum(30, 2.5kg)
Squat / 3x5 / 220lb 240s / progress: custom() {~
  if (completedReps >= reps) { weights += 5kg }
~}
Leg Press / 2x10 / 160kg 120s
"""


def test_liftosaurs_reformatted_lines_parse_to_the_same_prescriptions():
    w = week(REFORMATTED, 1)
    assert w[("Upper A", "Bench Press")] == [(7, 80.0, False)] * 3          # weight in the tail part
    assert w[("Upper A", "Lat Pulldown")] == [(10, 57.5, False)] * 2 + [(10, 47.5, False),
                                                                        (12, 42.5, True)]
    assert w[("Upper A", "Bicep Curl, Cable")] == [(12, 20.0, False)] * 2    # `@8` and timer ignored
    assert w[("Upper A", "Squat")] == [(5, 99.79, False)] * 3                # lb converted
    assert w[("Upper A", "Leg Press")] == [(10, 160.0, False)] * 2           # the script body is skipped


def test_a_program_with_no_week_headers_is_week_one():
    assert lift.parse_program("## Day\nSquat / 3x5 100kg")[1] == [("Day", "Squat", [(5, 100.0, False)] * 3)]


# --- comparing ---------------------------------------------------------------

def test_drift_names_the_exercise_and_both_sides():
    other = copy.deepcopy(LIVE_PLAN)
    bench = other["weeks"][0]["days"][0]["exercises"][0]
    for s in bench["sets"]:
        if s["kind"] != "warmup":
            s["load_kg"] += 2.5
    rows = contract.drift(lift.prescriptions(LIVE, 1), contract.plan_prescriptions(other, 1))
    assert rows == [("Upper A", "Bench Press", "3x6 @ 85kg", "3x6 @ 87.5kg")]


def test_a_missing_or_extra_exercise_is_drift():
    text = LIVE["text"].replace("Lateral Raise / 2x15 8kg / 60s / warmup: none / progress: lp(1kg)\n",
                                "Face Pull / 3x15 20kg\n")
    rows = contract.drift(lift.prescriptions({"text": text}, 1),
                          contract.plan_prescriptions(LIVE_PLAN, 1))
    assert ("Upper A", "Lateral Raise", "(absent)", "2x15 @ 8kg") in rows
    assert ("Upper A", "Face Pull", "3x15 @ 20kg", "(absent)") in rows


def test_a_rep_a_set_or_an_amrap_marker_each_make_drift():
    for old, new in (("3x5 100kg", "3x6 100kg"), ("3x5 100kg", "4x5 100kg"),
                     ("1x12+ 40kg", "1x12 40kg")):
        text = LIVE["text"].replace(old, new)
        assert text != LIVE["text"]
        rows = contract.drift(lift.prescriptions({"text": text}, 1),
                              contract.plan_prescriptions(LIVE_PLAN, 1))
        assert len(rows) == 1, (old, new)


def test_warmups_comments_timers_and_progress_text_are_not_drift():
    text = (LIVE["text"].replace("/ 180s", "/ 60s").replace("warmup: 1x8 60kg", "warmup: 2x5 40kg")
            .replace("progress: lp(2.5kg)", "progress: none").replace("// Brace.", "// Different note"))
    assert text != LIVE["text"]
    assert contract.drift(lift.prescriptions({"text": text}, 1),
                          contract.plan_prescriptions(LIVE_PLAN, 1)) == []


def test_only_the_current_week_is_compared():
    text = LIVE["text"].replace("Squat / 3x5 102.5kg / 240s", "Squat / 3x5 105kg / 240s")
    assert text != LIVE["text"]
    assert contract.drift(lift.prescriptions({"text": text}, 1),
                          contract.plan_prescriptions(LIVE_PLAN, 1)) == []
    assert len(contract.drift(lift.prescriptions({"text": text}, 2),
                              contract.plan_prescriptions(LIVE_PLAN, 2))) == 1


# --- app.py update ----------------------------------------------------------

@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(contract, "profile_path", lambda: tmp_path / "profile.json")
    (tmp_path / "profile.json").write_text(json.dumps({"app": {"name": "liftosaur"}}))
    return tmp_path


@pytest.fixture
def api(monkeypatch):
    state = type("Api", (), {})()
    state.program, state.log = None, []

    def call(method, path, params=None, body=None):
        state.log.append((method, path, body))
        if method == "GET":
            return 200, {"data": state.program}
        return 200, {"data": {"id": "p1"}}

    monkeypatch.setattr(lift, "call", call)
    return state


def planfile(tmp_path, plan):
    path = tmp_path / "meso-01.plan.json"
    path.write_text(json.dumps(plan))
    return str(path)


def writes(api):
    return [e for e in api.log if e[0] in ("PUT", "POST")]


def test_update_proceeds_when_the_app_matches_the_plan(store, api, tmp_path, capsys):
    api.program = LIVE
    app.cmd_update(ns(plan=planfile(tmp_path, LIVE_PLAN), id="opwlyhxs", week=None, yes=True))
    assert len(writes(api)) == 1 and "Updated liftosaur plan p1" in capsys.readouterr().out


def drifted(tmp_path):
    """The app progressed Bench Press (3x6 85kg -> 3x7 85kg) behind the plan's back."""
    program = dict(LIVE, text=LIVE["text"].replace("Bench Press / 3x6 85kg / 180s",
                                                   "Bench Press / 3x7 85kg / 180s", 1))
    assert program["text"] != LIVE["text"]
    return program


def test_update_refuses_on_drift_shows_both_sides_and_writes_nothing(store, api, tmp_path, capsys):
    api.program = drifted(tmp_path)
    with pytest.raises(SystemExit) as exc:
        app.cmd_update(ns(plan=planfile(tmp_path, LIVE_PLAN), id="opwlyhxs", week=None, yes=True))
    out = capsys.readouterr().out
    assert exc.value.code not in (0, None) and "Nothing was written" in str(exc.value.code)
    assert "Bench Press" in out and "3x7 @ 85kg" in out and "3x6 @ 85kg" in out
    assert "liftosaur" in out and "Canonical Plan" in out
    assert writes(api) == []


def test_force_shows_the_difference_and_writes_anyway(store, api, tmp_path, capsys):
    api.program = drifted(tmp_path)
    app.cmd_update(ns(plan=planfile(tmp_path, LIVE_PLAN), id="opwlyhxs", week=None, yes=True,
                      force=True))
    out = capsys.readouterr().out
    assert "3x7 @ 85kg" in out and "--force" in out and "Updated liftosaur plan p1" in out
    assert len(writes(api)) == 1


def test_the_guard_compares_the_plans_current_week(store, api, tmp_path):
    plan = copy.deepcopy(LIVE_PLAN)
    plan["current_week"] = 2
    api.program = dict(LIVE, text=LIVE["text"].replace("Squat / 3x5 102.5kg", "Squat / 3x5 105kg"))
    with pytest.raises(SystemExit):
        app.cmd_update(ns(plan=planfile(tmp_path, plan), id="x", week=None, yes=True))
    api.program = drifted(tmp_path)    # drift in week 1 only: week 2 still matches
    app.cmd_update(ns(plan=planfile(tmp_path, plan), id="x", week=None, yes=True))
    assert len(writes(api)) == 1


def test_the_cli_flag_is_force(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["app.py", "update", "p.json", "--id", "1", "--force"])
    seen = {}
    monkeypatch.setattr(app, "cmd_update", lambda a: seen.update(force=a.force))
    app.main()
    assert seen == {"force": True}


def test_an_app_without_native_progression_is_never_fetched(monkeypatch, tmp_path):
    called = []
    stub = type("Stub", (), {"NAME": "stub", "CAPABILITIES": {"get_plan"},
                             "get_plan": staticmethod(lambda i: called.append(i))})
    assert app.drift_guard(stub, PLAN, "1") == []
    assert called == []
