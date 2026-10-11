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


def test_build_renders_liftohistory_workouts_and_the_current_program(tmp_path, monkeypatch):
    ws = parse_liftohistory(FIX.read_text())
    monkeypatch.setattr(dashboard, "OUT", tmp_path)
    monkeypatch.setattr(dashboard, "IMG", tmp_path / "img")
    monkeypatch.setattr(dashboard, "STATE", tmp_path)
    monkeypatch.setattr(dashboard.analyze, "load_workouts", lambda: ws)
    monkeypatch.setattr(dashboard.analyze, "MEASUREMENTS", tmp_path / "none.json")
    monkeypatch.setattr(dashboard, "load_fedb", lambda: [])
    monkeypatch.setattr(dashboard.liftosaur, "call", lambda method, path, **k: (
        200, {"data": {"id": "p1", "name": "M1", "text": LIVE_PROGRAM}}))
    args = type("A", (), {"name": "T", "goal": "", "no_images": True, "open": False})()

    dashboard.build(args)

    js = (tmp_path / "data.js").read_text()
    data = json.loads(js[js.index("{"):js.rindex("}") + 1])
    assert data["workoutCount"] == 2
    assert [r["title"] for r in data["routines"]] == ["Week 1 · Day 1"]
    bench, mystery = data["routines"][0]["exercises"]
    assert bench["name"] == "Bench Press"
    assert bench["rx"] == "1x10, 1x8 / 80kg"
    assert bench["last"] == "last did 82.5 kg × 8 on 2026-09-08"
    assert mystery["last"] == "last did 30 kg × 12 on 2026-09-01"
    assert [c["exercise"] for c in data["e1rm"]] == ["Bench Press", "Lat Pulldown, Cable"]
    assert "no muscle mapping (Mystery Machine" in data["muscleNote"]


def test_build_survives_an_unreachable_program(tmp_path, monkeypatch):
    ws = parse_liftohistory(FIX.read_text())
    monkeypatch.setattr(dashboard, "OUT", tmp_path)
    monkeypatch.setattr(dashboard, "IMG", tmp_path / "img")
    monkeypatch.setattr(dashboard, "STATE", tmp_path)
    monkeypatch.setattr(dashboard.analyze, "load_workouts", lambda: ws)
    monkeypatch.setattr(dashboard.analyze, "MEASUREMENTS", tmp_path / "none.json")
    monkeypatch.setattr(dashboard, "load_fedb", lambda: [])
    monkeypatch.setattr(dashboard.liftosaur, "call", lambda method, path, **k: (404, None))
    args = type("A", (), {"name": "T", "goal": "", "no_images": True, "open": False})()

    dashboard.build(args)

    js = (tmp_path / "data.js").read_text()
    assert json.loads(js[js.index("{"):js.rindex("}") + 1])["routines"] == []
