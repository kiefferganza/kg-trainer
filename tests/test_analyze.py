import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import analyze
from liftohistory import parse_liftohistory

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "history_sample.txt"
MUSCLES = {"Bench Press": "chest", "Lat Pulldown, Cable": "lats"}


@pytest.fixture
def workouts():
    return parse_liftohistory(FIX.read_text())


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA", tmp_path)
    monkeypatch.setattr(analyze, "WORKOUTS", tmp_path / "workouts.jsonl")
    monkeypatch.setattr(analyze, "SYNC", tmp_path / "sync.json")
    monkeypatch.setattr(analyze, "MEASUREMENTS", tmp_path / "measurements.json")
    return tmp_path


def test_warmups_are_excluded_from_working_sets(workouts):
    sets = list(analyze.working_sets(workouts))
    assert len(sets) == 14          # (3+3+2) + (3+3), warmups dropped
    assert all(s["type"] != "warmup" for _, _, s in sets)


def test_epley_matches_the_logged_set(workouts):
    assert analyze.epley(80, 8) == pytest.approx(80 * (1 + 8 / 30))


def test_epley_refuses_high_rep_sets():
    assert analyze.epley(30, 12) is None


def test_balance_counts_sets_per_muscle(workouts):
    report = analyze.build_report(workouts, MUSCLES)
    by_muscle = report["balance"]["sets_by_muscle"]
    assert by_muscle["chest"] == 6
    assert by_muscle["lats"] == 6


def test_unmapped_exercise_is_named_not_silently_dropped(workouts):
    report = analyze.build_report(workouts, MUSCLES)
    assert report["balance"]["sets_by_muscle"].get("unknown") == 2
    assert report["balance"]["unclassified_exercises"] == {"Mystery Machine": 2}


def test_average_top_set_rpe_uses_the_heaviest_working_set(workouts):
    report = analyze.build_report(workouts, MUSCLES)
    # top sets: bench @8, pulldown @7, bench @9, pulldown @8  (Mystery has no RPE)
    assert report["rpe"]["top_sets_with_rpe"] == 4
    assert report["rpe"]["average_top_set_rpe"] == pytest.approx(8.0)


def test_best_sets_are_keyed_by_exercise_title(workouts):
    report = analyze.build_report(workouts, MUSCLES)
    bench = next(b for b in report["best_sets"] if b["exercise"] == "Bench Press")
    assert bench["sets_logged"] == 6
    assert bench["best_e1rm_from"] == "82.5kg x 8"


def test_pull_writes_records_with_their_api_id(store, monkeypatch):
    records = [{"id": "r1", "text": FIX.read_text()}]
    monkeypatch.setattr(analyze.liftosaur, "paginate", lambda *a, **k: records)

    analyze.cmd_pull(type("A", (), {"full": True})())

    lines = [json.loads(l) for l in (store / "workouts.jsonl").read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    assert {w["id"] for w in lines} == {"r1"}
    assert lines[0]["start_time"] < lines[1]["start_time"]
    assert json.loads((store / "sync.json").read_text())["last_pull"]


def test_incremental_pull_asks_for_only_new_workouts(store, monkeypatch):
    (store / "workouts.jsonl").write_text(
        json.dumps({"id": "old", "start_time": "2026-09-01T09:00:00Z", "exercises": []}) + "\n")
    seen = {}

    def fake_paginate(path, key, params=None, **kw):
        if path == "/history":
            seen.update(params or {})
        return []

    monkeypatch.setattr(analyze.liftosaur, "paginate", fake_paginate)
    analyze.cmd_pull(type("A", (), {"full": False})())
    assert seen["startDate"] == "2026-09-01"


WEIGHTS = [  # newest first, as the API returns them
    {"timestamp": 3, "date": "2026-10-11T20:12:28.292Z", "value": "69.9kg"},
    {"timestamp": 2, "date": "2026-10-11T07:00:00.000Z", "value": "70.4kg"},
    {"timestamp": 1, "date": "2026-10-09T23:39:58.878Z", "value": "154lb"},
]


def test_pull_writes_bodyweight_from_the_weight_series(store, monkeypatch):
    def fake_paginate(path, key, params=None, **kw):
        return WEIGHTS if path == "/measurements/weight" else []

    monkeypatch.setattr(analyze.liftosaur, "paginate", fake_paginate)
    analyze.cmd_pull(type("A", (), {"full": True})())

    meas = json.loads((store / "measurements.json").read_text())
    # one row per day, the latest weigh-in that day wins, pounds converted
    assert meas == [{"date": "2026-10-09", "weight_kg": pytest.approx(69.85, abs=0.01)},
                    {"date": "2026-10-11", "weight_kg": 69.9}]


def test_deep_dive_reads_every_logged_set_from_the_local_log(workouts, capsys):
    analyze.deep_dive(workouts, "bench")
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Bench Press: 8 logged sets, 6 working"
    assert "2026-09-08  82.5kg x 8  rpe 9" in out


def test_deep_dive_with_no_match_exits(workouts):
    with pytest.raises(SystemExit):
        analyze.deep_dive(workouts, "squat")


def test_personal_muscle_map_overrides_and_extends_the_shipped_one(tmp_path, monkeypatch):
    shipped = tmp_path / "shipped.json"
    shipped.write_text(json.dumps({"Bench Press": "chest", "Pullover": "lats"}))
    personal = tmp_path / "exercise-muscles.json"
    personal.write_text(json.dumps({"bayesian curl": "biceps", "Pullover": "chest"}))
    monkeypatch.setattr(analyze, "MUSCLE_MAP", shipped)
    monkeypatch.setattr(analyze, "PERSONAL_MUSCLE_MAP", personal)

    assert analyze.load_muscles() == {"Bench Press": "chest", "Pullover": "chest",
                                      "bayesian curl": "biceps"}


def test_personal_muscle_map_is_optional(tmp_path, monkeypatch):
    shipped = tmp_path / "shipped.json"
    shipped.write_text(json.dumps({"Bench Press": "chest"}))
    monkeypatch.setattr(analyze, "MUSCLE_MAP", shipped)
    monkeypatch.setattr(analyze, "PERSONAL_MUSCLE_MAP", tmp_path / "absent.json")

    assert analyze.load_muscles() == {"Bench Press": "chest"}


@pytest.mark.parametrize("bad", [{"Treadmill": "cardio"}, {"X": "unknown"}, ["not", "a", "map"]])
def test_personal_muscle_map_refuses_values_outside_the_vocabulary(tmp_path, monkeypatch, bad):
    shipped = tmp_path / "shipped.json"
    shipped.write_text(json.dumps({"Bench Press": "chest"}))
    personal = tmp_path / "exercise-muscles.json"
    personal.write_text(json.dumps(bad))
    monkeypatch.setattr(analyze, "MUSCLE_MAP", shipped)
    monkeypatch.setattr(analyze, "PERSONAL_MUSCLE_MAP", personal)

    with pytest.raises(SystemExit) as exc:
        analyze.load_muscles()
    assert str(personal) in str(exc.value)
