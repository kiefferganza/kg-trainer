"""Hevy Adapter and the contract, on hand-built fixtures shaped like the documented
Hevy responses. No network: the shared opener is replaced."""

import json
import pathlib
import sys
import urllib.parse

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import analyze
import app
import paths
from adapters import contract
from adapters import hevy
from adapters import http as apphttp
from contract_fixtures.hevy import (BENCH, MEASUREMENTS, WORKOUTS,  # noqa: E402
                                    _workout, workout_pages)

KEY = "00000000-hevy-secret-key"


class Resp:
    status = 200

    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def api(monkeypatch, tmp_path):
    """Serve canned pages; `api.pages` maps path -> list of page bodies."""
    store = tmp_path / "kg-trainer-data"
    store.mkdir()
    monkeypatch.setattr(paths, "state_dir", lambda: store)
    monkeypatch.setenv("HEVY_API_KEY", KEY)
    state = type("Api", (), {})()
    state.pages, state.requests, state.store = {}, [], store

    def opener(req, timeout=None):
        url = urllib.parse.urlparse(req.full_url)
        query = urllib.parse.parse_qs(url.query)
        state.requests.append((url.path, query, {k.lower(): v for k, v in req.header_items()}))
        pages = state.pages[url.path]
        if isinstance(pages, dict):
            return Resp(pages)
        return Resp(pages[int(query["page"][0]) - 1])

    monkeypatch.setattr(apphttp.OPENER, "open", opener)
    return state


# --- contract -------------------------------------------------------------

def test_undeclared_capability_prints_its_fallback_and_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        contract.require(hevy, "check")
    assert exc.value.code not in (0, None)
    assert "hevy does not support check" in str(exc.value.code)
    assert contract.FALLBACKS["check"] in str(exc.value.code)


def test_apps_lists_both_adapters_with_capabilities(capsys):
    app.cmd_apps(None)
    out = capsys.readouterr().out
    assert "liftosaur" in out and "hevy" in out
    assert "pull_bodyweight" in out
    assert "check: not supported" in out


def test_use_writes_the_app_block(api, capsys):
    app.cmd_use(type("A", (), {"name": "hevy"})())
    profile = json.loads((api.store / "profile.json").read_text())
    assert profile["app"] == {"name": "hevy", "key_source": "env"}
    assert contract.active_app() == "hevy"
    with pytest.raises(SystemExit):
        app.cmd_use(type("A", (), {"name": "nope"})())


def test_use_keeps_other_profile_keys_and_drops_foreign_program_id(api):
    (api.store / "profile.json").write_text(json.dumps(
        {"name": "K", "app": {"name": "liftosaur", "program_id": "p1"}}))
    app.cmd_use(type("A", (), {"name": "hevy"})())
    profile = json.loads((api.store / "profile.json").read_text())
    assert profile["name"] == "K" and "program_id" not in profile["app"]


# --- profile migration ----------------------------------------------------

def test_migration_is_idempotent_and_logs_once(api, capsys):
    path = api.store / "profile.json"
    path.write_text(json.dumps({"name": "K", "liftosaur": {
        "connected": True, "key_source": "dotenv", "program_id": "p9"}, "goal": "x"}))
    assert contract.active_app() == "liftosaur"
    first = capsys.readouterr().err
    assert "migrated" in first
    migrated = path.read_text()
    assert json.loads(migrated) == {"name": "K", "app": {
        "name": "liftosaur", "key_source": "dotenv", "program_id": "p9"}, "goal": "x"}
    assert contract.active_app() == "liftosaur"
    assert capsys.readouterr().err == ""
    assert path.read_text() == migrated


def test_no_profile_means_no_active_app(api):
    assert contract.active_app() is None


# --- Hevy ping, pull, auth ------------------------------------------------

def test_ping_uses_the_api_key_header_not_bearer(api):
    api.pages["/v1/user/info"] = {"data": {"id": "u1", "name": "Kieffer", "url": "x"}}
    assert hevy.ping() == {"account": "Kieffer", "units": "kg"}
    headers = api.requests[0][2]
    assert headers["api-key"] == KEY and "authorization" not in headers


def test_history_pages_through_every_page_of_ten(api):
    many = [_workout(f"w{i}", f"2026-09-{i:02d}T10:00:00+00:00", [BENCH]) for i in range(1, 26)]
    api.pages["/v1/workouts"] = workout_pages(many)
    log = hevy.pull_history()
    assert len(log) == 25
    assert [q["page"] for _, q, _ in api.requests] == [["1"], ["2"], ["3"]]
    assert all(q["pageSize"] == ["10"] for _, q, _ in api.requests)
    starts = [w["start_time"] for w in log]
    assert starts == sorted(starts, reverse=True)


def test_history_is_kg_canonical_and_logged_rpe_only(api):
    api.pages["/v1/workouts"] = workout_pages(WORKOUTS)
    log = hevy.pull_history()
    assert [w["id"] for w in log] == ["w3", "w2", "w1"]
    w2 = log[1]
    assert w2["start_time"] == "2026-10-02T10:00:00Z" and w2["duration_seconds"] == 3600
    bench, curl = w2["exercises"]
    assert bench["title"] == "Bench Press"           # canonical, via the shipped map
    assert curl["title"] == "Zottman Curl (Dumbbell)"  # unmatched: kept, not guessed
    warm, top, back = bench["sets"]
    assert warm["type"] == "warmup" and top["weight_kg"] == 80 and top["reps"] == 8
    assert top["rpe"] == 8.5 and top["rpe_logged"] is True
    assert back["rpe"] is None and back["rpe_logged"] is False


@pytest.mark.parametrize("break_it", ["missing", "bool", "huge", "empty_page", "moves",
                                      "wrong_page", "duplicate"])
def test_pagination_that_does_not_terminate_cleanly_fails_loudly(api, break_it):
    many = [_workout(f"w{i}", f"2026-09-{i:02d}T10:00:00+00:00", [BENCH]) for i in range(1, 16)]
    pages = workout_pages(many)
    if break_it == "missing":
        del pages[0]["page_count"]
    elif break_it == "bool":
        pages[0]["page_count"] = True
    elif break_it == "huge":
        pages[0]["page_count"] = 10 ** 9
    elif break_it == "empty_page":
        pages[1]["workouts"] = []
    elif break_it == "moves":
        pages[1]["page_count"] = 3
    elif break_it == "wrong_page":
        pages[1]["page"] = 1
    elif break_it == "duplicate":
        pages[1]["workouts"][0] = dict(pages[0]["workouts"][0])
    api.pages["/v1/workouts"] = pages
    with pytest.raises(SystemExit) as exc:
        hevy.pull_history()
    assert KEY not in str(exc.value)


def test_empty_account_is_an_empty_log(api):
    api.pages["/v1/workouts"] = [{"page": 1, "page_count": 0, "workouts": []}]
    assert hevy.pull_history() == []


def test_bodyweight_is_one_reading_per_day_latest_entered_wins(api):
    api.pages["/v1/body_measurements"] = [{"page": 1, "page_count": 1,
                                           "body_measurements": MEASUREMENTS}]
    rows = hevy.pull_bodyweight()
    import datetime as dt
    assert rows == [(dt.date(2026, 10, 3), 80.9), (dt.date(2026, 10, 5), 80.4)]


def test_http_failure_names_operation_and_status_not_key(api, monkeypatch):
    import urllib.error

    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "no", {}, None)

    monkeypatch.setattr(apphttp.OPENER, "open", refuse)
    with pytest.raises(SystemExit) as exc:
        hevy.ping()
    assert "GET /v1/user/info failed: HTTP 403" in str(exc.value)
    assert KEY not in str(exc.value)


# --- exercise map ---------------------------------------------------------

def test_personal_map_overrides_the_shipped_one(api):
    (api.store / "exercise-map-hevy.json").write_text(json.dumps({
        "Bench Press": "Flat Barbell Press",
        "Zottman Curl": {"title": "Zottman Curl (Dumbbell)", "id": "BBBB0002"}}))
    api.pages["/v1/workouts"] = workout_pages(WORKOUTS)
    log = hevy.pull_history()
    titles = {e["title"] for w in log for e in w["exercises"]}
    assert "Zottman Curl" in titles                    # matched by id
    assert "Bench Press" not in titles                 # shipped title no longer maps
    assert "Bench Press (Barbell)" in titles


def test_find_exercise_searches_the_map(api):
    hits = hevy.find_exercise("curl")
    assert {"canonical": "Bicep Curl, Cable", "app_id": None} in hits
    assert hevy.find_exercise("zzz") == []


def test_shipped_map_uses_only_canonical_names():
    shipped = json.loads(hevy.SHIPPED_MAP.read_text())["exercises"]
    canon = json.loads(hevy.MUSCLES.read_text())
    assert shipped and set(shipped) <= set(canon)


def test_build_map_writes_exact_matches_and_reviews_guesses():
    canon = {"Bench Press", "Bench Press, Dumbbell", "Squat", "Bicep Curl, Cable"}
    templates = [
        {"id": "A1", "title": "Bench Press (Dumbbell)"},
        {"id": "A2", "title": "Bicep Curl (Cable)"},
        {"id": "A3", "title": "Bench Press (Barbell)"},   # bare = default equipment: review
        {"id": "A4", "title": "Squat (Machine)"},          # must NOT become "Squat"
        {"id": "A5", "title": "Mystery Move"},
        {"id": "A6", "title": "My Custom", "is_custom": True},
    ]
    mapped, review = hevy.build_map(templates, canon)
    assert mapped == {"Bench Press, Dumbbell": {"title": "Bench Press (Dumbbell)", "id": "A1"},
                      "Bicep Curl, Cable": {"title": "Bicep Curl (Cable)", "id": "A2"}}
    assert {r["title"] for r in review} == {"Bench Press (Barbell)", "Squat (Machine)",
                                            "Mystery Move"}


# --- analyze pull + report on a Hevy store --------------------------------

def test_analyze_pull_and_report_on_a_hevy_store(api, monkeypatch, capsys):
    data = api.store / "data"
    monkeypatch.setattr(analyze, "DATA", data)
    monkeypatch.setattr(analyze, "WORKOUTS", data / "workouts.jsonl")
    monkeypatch.setattr(analyze, "SYNC", data / "sync.json")
    monkeypatch.setattr(analyze, "MEASUREMENTS", data / "measurements.json")
    (api.store / "profile.json").write_text(json.dumps({"app": {"name": "hevy"}}))
    api.pages["/v1/workouts"] = workout_pages(WORKOUTS)
    api.pages["/v1/body_measurements"] = [{"page": 1, "page_count": 1,
                                           "body_measurements": MEASUREMENTS}]

    analyze.cmd_pull(type("A", (), {"full": True})())
    assert "from hevy" in capsys.readouterr().out
    assert json.loads((data / "measurements.json").read_text()) == [
        {"date": "2026-10-03", "weight_kg": 80.9}, {"date": "2026-10-05", "weight_kg": 80.4}]

    report = analyze.build_report(analyze.load_workouts(), analyze.load_muscles())
    assert report["workout_count"] == 3
    assert report["balance"]["unclassified_exercises"] == {"Zottman Curl (Dumbbell)": 1}
    assert report["rpe"]["top_sets_with_rpe"] >= 1
    assert report["bodyweight"]["n"] == 2
    assert "Baseline" in analyze.fmt(report)
