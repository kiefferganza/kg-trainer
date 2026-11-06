"""Liftosaur write path: compile (golden), push, update, get, check, app.py wiring.
No network: `adapters.liftosaur.call` is replaced. Fixture-tested; live run is separate."""

import copy
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import app
import liftosaur as extras
from adapters import contract
from adapters import liftosaur as lift

FIX = pathlib.Path(__file__).parent / "fixtures"
PLAN = json.loads((FIX / "liftosaur_plan.json").read_text())
GOLDEN = FIX / "liftosaur_compiled.golden.liftoscript"


def ns(**kw):
    return type("A", (), kw)()


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(contract, "profile_path", lambda: tmp_path / "profile.json")
    (tmp_path / "profile.json").write_text(json.dumps({"app": {"name": "liftosaur"}}))
    return tmp_path


@pytest.fixture
def api(monkeypatch):
    """`api.routes[(METHOD, path)]` is a body, or (status, body); `api.log` records
    (method, path, body) for every call."""
    state = type("Api", (), {})()
    state.routes, state.log = {}, []

    def call(method, path, params=None, body=None):
        state.log.append((method, path, body))
        route = state.routes[(method, path)]
        status, payload = route if isinstance(route, tuple) else (200, route)
        return status, payload

    monkeypatch.setattr(lift, "call", call)
    return state


def writes(api):
    return [e for e in api.log if e[0] in ("POST", "PUT") and "stats" not in e[1]
            and "playground" not in e[1]]


# --- compile ---------------------------------------------------------------

def test_the_fixture_plan_is_a_valid_canonical_plan(tmp_path):
    import plan
    path = tmp_path / "meso-01.plan.json"
    path.write_text(json.dumps(PLAN))
    assert plan.validate_plan(PLAN, plan.load_muscles()) == []


def test_compile_matches_golden():
    compiled = lift.compile(copy.deepcopy(PLAN))
    if not GOLDEN.exists():  # pragma: no cover  (approve by reading it, then commit)
        GOLDEN.write_text(compiled)
    assert compiled == GOLDEN.read_text()


def test_compile_ignores_the_week_argument():
    assert lift.compile(copy.deepcopy(PLAN), week=2) == lift.compile(copy.deepcopy(PLAN))


def lines(text, prefix):
    return [l for l in text.splitlines() if l.startswith(prefix)]


def test_each_rule_type_maps_to_its_liftosaur_form():
    text = lift.compile(copy.deepcopy(PLAN))
    assert "progress: dp(2.5kg, 6, 8)" in lines(text, "Bench Press")[0]
    assert "progress: lp(2.5kg)" in lines(text, "Lat Pulldown")[0]
    assert "progress: sum(30, 2.5kg)" in lines(text, "Bicep Curl, Cable")[0]
    assert "progress: none" in lines(text, "Squat")[0]
    assert "progress: custom() {~\n  if (completedReps >= reps) { weights += 5kg }\n~}" in text


def test_progression_is_declared_once_and_changes_only_where_the_rule_changes():
    text = lift.compile(copy.deepcopy(PLAN))
    bench = lines(text, "Bench Press")
    assert ["progress" in l for l in bench] == [True, False, True]      # wk1, wk2 inherits, wk3 off
    assert bench[2].count("progress: none") == 1
    squat = lines(text, "Squat")
    assert ["progress" in l for l in squat] == [True, False, True]      # none, none, week-3 override
    assert "progress: lp(2.5kg)" in squat[2]                           # deload week, still progresses
    raise_ = lines(text, "Lateral Raise")
    assert "progress: lp(1kg)" in raise_[0] and "progress: none" in raise_[1]   # per-week override
    assert "progress" not in lines(text, "Pull Up")[0]                 # never had a rule


def test_description_is_cleared_when_an_exercise_loses_its_cue():
    text = lift.compile(copy.deepcopy(PLAN))
    assert "//\nBicep Curl, Cable" in text
    assert text.count("// Pause one inch off the chest.") == 2


def test_sets_warmups_failure_and_bodyweight():
    text = lift.compile(copy.deepcopy(PLAN))
    assert "Bench Press / 3x6 80kg / 180s / warmup: 1x8 60kg" in text
    assert "1x12+ 40kg" in text                       # failure set is AMRAP
    assert "Pull Up / 2x8 0kg / warmup: none" in text
    assert "superset: A" in lines(text, "Lat Pulldown")[0]


def test_a_native_rule_for_another_app_fails_naming_the_exercise():
    plan = copy.deepcopy(PLAN)
    plan["progression"]["Lat Pulldown"] = {"native": "hevy", "text": "x"}
    with pytest.raises(SystemExit) as exc:
        lift.compile(plan)
    message = str(exc.value.code)
    assert "Lat Pulldown" in message and "'hevy'" in message


def test_a_week_level_native_rule_for_another_app_also_fails():
    plan = copy.deepcopy(PLAN)
    plan["weeks"][2]["progression"]["Bench Press"] = {"native": "other", "text": "x"}
    with pytest.raises(SystemExit, match="Bench Press"):
        lift.compile(plan)


# --- push, update, get -----------------------------------------------------

def test_push_posts_name_and_text_and_returns_the_id(api):
    api.routes[("POST", "/programs")] = {"data": {"id": "p9", "name": "Block"}}
    assert lift.push_plan("# Week 1\n", "Block") == "p9"
    assert api.log == [("POST", "/programs", {"name": "Block", "text": "# Week 1\n"})]


def test_a_push_with_no_id_is_a_failure(api):
    api.routes[("POST", "/programs")] = {"data": {"name": "Block"}}
    with pytest.raises(SystemExit, match="no 'id'"):
        lift.push_plan("# Week 1\n", "Block")


def test_update_by_id_and_by_current(api):
    api.routes[("PUT", "/programs/p9")] = {"data": {"id": "p9"}}
    api.routes[("PUT", "/programs/current")] = {"data": {"id": "p3"}}
    assert lift.update_plan("T", "p9") == "p9"
    assert lift.update_plan("T", "current") == "p3"
    assert [e[1] for e in api.log] == ["/programs/p9", "/programs/current"]
    assert api.log[0][2] == {"text": "T"}


def test_get_by_id_and_by_current(api):
    api.routes[("GET", "/programs/p9")] = {"data": {"id": "p9", "name": "B", "text": "# Week 1"}}
    api.routes[("GET", "/programs/current")] = {"data": {"id": "p3", "name": "C", "text": "# Week 1"}}
    assert lift.get_plan("p9")["text"] == "# Week 1"
    assert lift.get_plan("current")["id"] == "p3"
    api.routes[("GET", "/programs/p1")] = {"data": {"id": "p1"}}
    with pytest.raises(SystemExit, match="no 'text'"):
        lift.get_plan("p1")


def test_every_plan_operation_is_declared():
    assert {"compile", "push_plan", "update_plan", "get_plan", "check"} <= lift.CAPABILITIES


# --- check -----------------------------------------------------------------

STATS = {"data": {"muscleGroups": [{"name": "Chest", "sets": 6.5}, {"name": "Abs", "sets": 3},
                                   {"name": "Back", "sets": 9}]}}


def serve_check(api, stats=STATS, playground=None, stats_status=200, play_status=200):
    api.routes[("POST", "/program-stats")] = (stats_status, stats)
    api.routes[("POST", "/playground")] = (play_status, playground or {"data": {"workout": ""}})


def test_check_returns_ok_and_weekly_sets_in_skill_muscle_names(api):
    serve_check(api)
    result = lift.check("# Week 1\n")
    assert result == {"ok": True, "errors": [],
                      "weekly_sets": {"chest": 6.5, "abdominals": 3, "back": 9}}
    assert [e[1] for e in api.log] == ["/program-stats", "/playground"]


def test_check_reports_a_rejected_program_and_still_runs_both_calls(api):
    serve_check(api, stats={"error": {"code": "invalid", "message": "line 3"}}, stats_status=422,
                play_status=422, playground={"error": {"message": "line 3"}})
    result = lift.check("junk")
    assert result["ok"] is False and result["weekly_sets"] == {}
    assert any("program-stats: HTTP 422 invalid line 3" in e for e in result["errors"])
    assert any(e.startswith("playground: HTTP 422") for e in result["errors"])


def test_check_reports_an_unrecognised_stats_shape_instead_of_guessing(api):
    serve_check(api, stats={"data": {"days": []}})
    result = lift.check("# Week 1\n")
    assert result["ok"] is False and result["weekly_sets"] == {}
    assert "muscleGroups" in result["errors"][0]


def test_check_reports_a_playground_error_in_a_200(api):
    serve_check(api, playground={"data": {"error": "Unknown exercise Foo"}})
    result = lift.check("# Week 1\n")
    assert result["ok"] is False and "Unknown exercise Foo" in result["errors"][0]


@pytest.mark.parametrize("theirs,ours", [("Abs", "abdominals"), ("Upper Back", "upper_back"),
                                         ("lower_back", "lower_back"), ("Quads", "quadriceps"),
                                         ("Back", "back"), ("Neck", "neck")])
def test_muscle_mapping(theirs, ours):
    assert lift.skill_muscle(theirs) == ours


# --- app.py ----------------------------------------------------------------

@pytest.fixture
def planfile(tmp_path):
    path = tmp_path / "meso-01.plan.json"
    path.write_text(json.dumps(PLAN))
    return str(path)


def test_no_active_app_exits_telling_the_user_to_choose(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, "profile_path", lambda: tmp_path / "profile.json")
    with pytest.raises(SystemExit) as exc:
        app.cmd_ping(ns())
    assert "app.py use" in str(exc.value.code)
    assert not hasattr(app, "DEFAULT_APP")


def test_compile_prints_the_liftoscript_as_is(store, planfile, capsys):
    app.cmd_compile(ns(plan=planfile, week=None))
    assert capsys.readouterr().out == GOLDEN.read_text() + "\n"


def test_push_records_the_program_id(store, api, planfile, capsys):
    api.routes[("POST", "/programs")] = {"data": {"id": "p9", "name": "Block"}}
    app.cmd_push(ns(plan=planfile, name="Block", week=None, yes=True))
    assert "p9" in capsys.readouterr().out
    assert json.loads((store / "profile.json").read_text())["app"]["program_id"] == "p9"


def test_a_push_without_an_id_fails_and_records_nothing(store, api, planfile):
    api.routes[("POST", "/programs")] = {"data": {"name": "Block"}}
    with pytest.raises(SystemExit) as exc:
        app.cmd_push(ns(plan=planfile, name="Block", week=None, yes=True))
    assert exc.value.code not in (0, None)
    assert "program_id" not in json.loads((store / "profile.json").read_text())["app"]


def test_update_and_get_go_through_the_cli(store, api, planfile, capsys):
    api.routes[("PUT", "/programs/current")] = {"data": {"id": "p3"}}
    api.routes[("GET", "/programs/current")] = {"data": {"id": "p3", "name": "C", "text": "# Week 1"}}
    app.cmd_update(ns(plan=planfile, id="current", week=None, yes=True))
    app.cmd_get(ns(id="current"))
    out = capsys.readouterr().out
    assert "Updated liftosaur plan p3" in out and '"text": "# Week 1"' in out


def run_check(planfile, capsys):
    code = 0
    try:
        app.cmd_check(ns(plan=planfile))
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def local_week1(planfile):
    import plan
    return plan.weekly_sets(PLAN, plan.load_muscles())[1]


def stats_payload(local, tweak=None):
    names = {"abdominals": "Abs"}
    groups = [{"name": names.get(m, m), "sets": n} for m, n in local.items()]
    return {"data": {"muscleGroups": tweak(groups) if tweak else groups}}


def test_check_prints_both_tables_and_agrees(store, api, planfile, capsys):
    serve_check(api, stats_payload(local_week1(planfile)))
    code, out, _ = run_check(planfile, capsys)
    assert code == 0 and "Counts agree." in out and "DIFFERS" not in out
    assert "local" in out and "liftosaur" in out


def test_check_flags_differences_and_never_resolves_them(store, api, planfile, capsys):
    local = local_week1(planfile)

    def tweak(groups):
        groups[0]["sets"] += 1.5
        return groups

    serve_check(api, stats_payload(local, tweak))
    code, out, _ = run_check(planfile, capsys)
    assert code == 2
    assert out.count("<-- DIFFERS") == 1 and "1 muscle(s) differ" in out
    assert "decide which count to trust" in out


def test_check_compares_a_group_with_the_sum_of_the_muscles_it_spans(store, api, planfile, capsys):
    local = local_week1(planfile)
    back = sum(local.get(m, 0) for m in lift.MUSCLE_GROUPS["back"])
    assert back > 0

    def tweak(groups):
        return [g for g in groups if g["name"] not in lift.MUSCLE_GROUPS["back"]] + \
               [{"name": "Back", "sets": back}]

    serve_check(api, stats_payload(local, tweak))
    code, out, _ = run_check(planfile, capsys)
    assert code == 0 and "back (lats+upper_back+lower_back+traps)" in out


def test_check_exits_1_on_a_rejected_program_but_still_prints_local_counts(store, api, planfile, capsys):
    serve_check(api, stats={"error": {"message": "bad"}}, stats_status=422,
                play_status=422, playground={"error": {"message": "bad"}})
    code, out, err = run_check(planfile, capsys)
    assert code == 1 and "FAILED" in out and "HTTP 422" in err
    assert "no set counts to compare" in out and "quadriceps" in out


# --- the extras CLI --------------------------------------------------------

def test_playground_record_saves_a_fixture_for_ticket_07(api, tmp_path, monkeypatch, capsys):
    prog = tmp_path / "p.liftoscript"
    prog.write_text("# Week 1\n## Day 1\nSquat / 3x5 100kg / progress: lp(2.5kg)\n")
    out = tmp_path / "fixture.json"
    api.routes[("POST", "/playground")] = {"data": {"updatedProgramText": "NEW"}}
    monkeypatch.setattr(sys, "argv", [
        "liftosaur.py", "playground", str(prog), "--week", "1", "--day", "1",
        "--command", "complete_set(1, 1)", "--command", "finish_workout()",
        "--record", str(out)])
    extras.main()
    record = json.loads(out.read_text())
    assert record["commands"] == ["complete_set(1, 1)", "finish_workout()"]
    assert record["response"] == {"updatedProgramText": "NEW"}
    assert record["program_text"].startswith("# Week 1")
    assert "recorded" in record and "lftsk" not in out.read_text()


def test_record_without_finish_workout_warns(api, tmp_path, monkeypatch, capsys):
    prog = tmp_path / "p.liftoscript"
    prog.write_text("# Week 1\n")
    api.routes[("POST", "/playground")] = {"data": {}}
    monkeypatch.setattr(sys, "argv", ["liftosaur.py", "playground", str(prog),
                                      "--record", str(tmp_path / "f.json")])
    extras.main()
    assert "finish_workout" in capsys.readouterr().err


def test_program_stats_shim_prints_the_raw_response(api, tmp_path, monkeypatch, capsys):
    prog = tmp_path / "p.liftoscript"
    prog.write_text("# Week 1\n")
    api.routes[("POST", "/program-stats")] = {"data": {"muscleGroups": []}}
    monkeypatch.setattr(sys, "argv", ["liftosaur.py", "program-stats", str(prog)])
    extras.main()
    assert json.loads(capsys.readouterr().out) == {"muscleGroups": []}
