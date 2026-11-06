import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import dashboard
from liftohistory import parse_liftohistory

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "history_sample.txt"

PROGRAM = """# Week 1
## Upper A
Bench Press / 3x8 80kg / progress: lp(2.5kg)
Lat Pulldown, Cable / 3x10 50kg

## Lower A
Squat / 3x5 100kg / progress: lp(5kg)

# Week 2
## Upper A
Bench Press / 3x8 82.5kg
"""


def test_program_sessions_splits_weeks_and_days():
    sessions = dashboard.program_sessions(PROGRAM)
    assert [(s["week"], s["day"]) for s in sessions] == [
        ("Week 1", "Upper A"), ("Week 1", "Lower A"), ("Week 2", "Upper A")]


def test_program_sessions_lists_exercise_lines_without_the_progress_section():
    sessions = dashboard.program_sessions(PROGRAM)
    assert sessions[0]["exercises"] == ["Bench Press / 3x8 80kg",
                                        "Lat Pulldown, Cable / 3x10 50kg"]


def test_program_sessions_ignores_comments_and_blank_lines():
    assert dashboard.program_sessions("# Week 1\n\n// a note\n## Day\n\nSquat / 1x5 100kg\n") == [
        {"week": "Week 1", "day": "Day", "exercises": ["Squat / 1x5 100kg"]}]


def test_program_sessions_drops_trailing_comments_and_script_blocks():
    text = ("# Week 1\n## Day 1\n"
            "Squat / 3x8 / 135lb   /// explicit weight\n"
            "Bench Press / 3x8 / 95lb / progress: custom() {~\n"
            "  if (completedReps >= reps) {\n"
            "    weights += 5lb\n"
            "  }\n"
            "~}\n"
            "Lateral Raise / 3x12 / 10kg / warmup: none\n")
    assert dashboard.program_sessions(text)[0]["exercises"] == [
        "Squat / 3x8 / 135lb", "Bench Press / 3x8 / 95lb", "Lateral Raise / 3x12 / 10kg"]


LIVE_PROGRAM = """# Week 1
## Day 1
Bench Press / 1x10, 1x8 / 80kg / warmup: 1x8 40kg
Mystery Machine / 2x12 / 30kg / warmup: none
"""

CANON = {
    "schema": 1, "name": "Meso 1", "created": "2026-11-07", "current_week": 1,
    "weeks": [
        {"number": 1, "phase": "accumulation", "deload": False, "days": [
            {"name": "Upper A", "exercises": [
                {"exercise": "Bench Press", "cue": "Pause one inch off the chest.",
                 "sets": [{"reps": 8, "load_kg": 80, "rpe": None, "kind": "working"}] * 3},
                {"exercise": "Mystery Machine",
                 "sets": [{"reps": 12, "load_kg": 30, "rpe": None, "kind": "working"}] * 2}]},
            {"name": "Lower A", "exercises": [
                {"exercise": "Squat",
                 "sets": [{"reps": 5, "load_kg": 100, "rpe": None, "kind": "working"}] * 3}]}]},
        {"number": 2, "phase": "accumulation", "deload": False, "days": [
            {"name": "Upper A", "exercises": [
                {"exercise": "Bench Press",
                 "sets": [{"reps": 8, "load_kg": 82.5, "rpe": None, "kind": "working"}] * 3}]}]},
    ],
    "progression": {},
}


def _args():
    return type("A", (), {"name": "T", "goal": "", "no_images": True, "open": False})()


def _setup(tmp_path, monkeypatch):
    ws = parse_liftohistory(FIX.read_text())
    monkeypatch.setattr(dashboard, "OUT", tmp_path / "dashboard")
    monkeypatch.setattr(dashboard, "IMG", tmp_path / "dashboard" / "img")
    monkeypatch.setattr(dashboard, "STATE", tmp_path)
    monkeypatch.setattr(dashboard.analyze, "load_workouts", lambda: ws)
    monkeypatch.setattr(dashboard.analyze, "MEASUREMENTS", tmp_path / "none.json")
    monkeypatch.setattr(dashboard, "load_fedb", lambda: [])
    (tmp_path / "plans").mkdir()


def _data(tmp_path):
    js = (tmp_path / "dashboard" / "data.js").read_text()
    return json.loads(js[js.index("{"):js.rindex("}") + 1])


def _no_network(monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise AssertionError("network call during dashboard build")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", boom)
    monkeypatch.setattr(urllib.request, "build_opener", boom)


def test_build_renders_one_tab_per_week_and_day_from_the_canonical_plan(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "plans" / "meso-01.plan.json").write_text(json.dumps(CANON))
    _no_network(monkeypatch)

    dashboard.build(_args())

    data = _data(tmp_path)
    assert data["workoutCount"] == 2
    assert [r["title"] for r in data["routines"]] == [
        "Week 1 · Upper A", "Week 1 · Lower A", "Week 2 · Upper A"]
    bench, mystery = data["routines"][0]["exercises"]
    assert bench["name"] == "Bench Press"
    assert bench["rx"] == "3 x 8 @ 80 kg"
    assert bench["cue"] == "Pause one inch off the chest."
    assert bench["last"] == "last did 82.5 kg × 8 on 2026-09-08"
    assert mystery["last"] == "last did 30 kg × 12 on 2026-09-01"
    assert [c["exercise"] for c in data["e1rm"]] == ["Bench Press", "Lat Pulldown, Cable"]
    assert "no muscle mapping (Mystery Machine" in data["muscleNote"]


def test_newest_canonical_plan_wins_over_older_plan_and_liftoscript(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    old = dict(CANON, name="old", weeks=CANON["weeks"][:1])
    (tmp_path / "plans" / "meso-01.plan.json").write_text(json.dumps(old))
    (tmp_path / "plans" / "meso-02.plan.json").write_text(json.dumps(CANON))
    (tmp_path / "plans" / "meso-03.liftoscript").write_text(LIVE_PROGRAM)

    dashboard.build(_args())

    assert len(_data(tmp_path)["routines"]) == 3


def test_build_without_a_canonical_plan_falls_back_to_the_newest_liftoscript(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "plans" / "meso-01.liftoscript").write_text(PROGRAM)
    (tmp_path / "plans" / "meso-02.liftoscript").write_text(LIVE_PROGRAM)
    _no_network(monkeypatch)

    dashboard.build(_args())

    data = _data(tmp_path)
    assert [r["title"] for r in data["routines"]] == ["Week 1 · Day 1"]
    bench, mystery = data["routines"][0]["exercises"]
    assert bench["name"] == "Bench Press"
    assert bench["rx"] == "1x10, 1x8 / 80kg"
    assert bench["last"] == "last did 82.5 kg × 8 on 2026-09-08"
    assert mystery["last"] == "last did 30 kg × 12 on 2026-09-01"


def test_build_does_not_call_liftosaur_for_the_plan(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert not hasattr(dashboard, "liftosaur")


def test_build_with_no_plan_files_shows_an_empty_plan_not_an_error(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    dashboard.build(_args())

    assert _data(tmp_path)["routines"] == []


def test_build_output_contains_no_api_key(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("LIFTOSAUR_API_KEY", "lftsk_secret123")
    (tmp_path / "plans" / "meso-01.plan.json").write_text(json.dumps(CANON))

    dashboard.build(_args())

    for f in (tmp_path / "dashboard").rglob("*"):
        if f.is_file():
            assert "lftsk_secret123" not in f.read_text(errors="ignore")
