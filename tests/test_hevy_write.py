"""Hevy write path: compile (golden), push, update, get, app.py wiring.
No network: the shared opener is replaced. Fixture-tested only; never run live."""

import copy
import io
import json
import pathlib
import sys
import urllib.error
import urllib.parse

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import app
import paths
from adapters import contract
from adapters import hevy
from adapters import http as apphttp

FIX = pathlib.Path(__file__).parent / "fixtures"
PLAN = json.loads((FIX / "hevy_plan.json").read_text())
GOLDEN = FIX / "hevy_compiled_week2.golden.json"

CATALOGUE = [
    {"id": "AAAA0001", "title": "Bench Press (Barbell)"},
    {"id": "AAAA0002", "title": "Squat (Barbell)"},
    {"id": "AAAA0003", "title": "Lat Pulldown (Cable)"},
    {"id": "AAAA0004", "title": "Bicep Curl (Cable)"},
    {"id": "AAAA0005", "title": "Pull Up"},
]


class Resp:
    status = 200

    def __init__(self, body, status=200):
        self._body, self.status = json.dumps(body).encode(), status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def store(monkeypatch, tmp_path):
    root = tmp_path / "kg-trainer-data"
    root.mkdir()
    monkeypatch.setattr(paths, "state_dir", lambda: root)
    monkeypatch.setenv("HEVY_API_KEY", "k-secret")
    (root / "hevy-exercise-templates.jsonl").write_text(
        "".join(json.dumps(t) + "\n" for t in CATALOGUE))
    return root


@pytest.fixture
def api(monkeypatch, store):
    """`api.routes[(METHOD, path)]` is a body, a (status, body), or a list of those
    consumed in order. Every request is logged in `api.log` as (method, path, body)."""
    state = type("Api", (), {})()
    state.routes, state.log, state.store = {}, [], store

    def opener(req, timeout=None):
        url = urllib.parse.urlparse(req.full_url)
        method = req.get_method()
        state.log.append((method, url.path, json.loads(req.data) if req.data else None))
        route = state.routes[(method, url.path)]
        if isinstance(route, list):
            route = route.pop(0)
        status, body = route if isinstance(route, tuple) else (200, route)
        if status >= 300:
            raise urllib.error.HTTPError(req.full_url, status, "err", {},
                                         io.BytesIO(json.dumps(body).encode()))
        return Resp(body, status)

    monkeypatch.setattr(apphttp.OPENER, "open", opener)
    return state


def writes(api):
    return [e for e in api.log if e[0] in ("POST", "PUT")]


# --- compile ---------------------------------------------------------------

def test_compile_matches_golden(store):
    compiled = hevy.compile(copy.deepcopy(PLAN))   # defaults to current_week = 2
    if not GOLDEN.exists():  # pragma: no cover  (approve by reading it, then commit)
        GOLDEN.write_text(json.dumps(compiled, indent=2) + "\n")
    assert compiled == json.loads(GOLDEN.read_text())


def test_compile_week_argument_picks_another_week(store):
    compiled = hevy.compile(copy.deepcopy(PLAN), week=1)
    assert compiled["week"] == 1 and len(compiled["routines"]) == 1
    with pytest.raises(SystemExit, match="no week 9"):
        hevy.compile(copy.deepcopy(PLAN), week=9)


def test_compile_set_types_ranges_supersets_notes(store):
    r = hevy.compile(copy.deepcopy(PLAN))["routines"]
    bench, pulldown, curl = r[0]["routine"]["exercises"]
    assert [s["type"] for s in bench["sets"]] == ["warmup", "normal", "normal", "dropset"]
    assert bench["sets"][1]["rep_range"] == {"start": 6, "end": 8}
    assert bench["sets"][1]["reps"] is None
    assert bench["sets"][0]["reps"] == 8 and bench["sets"][0]["rep_range"] is None
    assert pulldown["sets"][1]["type"] == "failure"
    assert pulldown["superset_id"] == curl["superset_id"] == 0
    assert bench["notes"] == "Pause one inch off the chest. Target: 2 RIR."
    assert curl["notes"] == "No swing. Target: 1 RIR. RPE 8."
    assert bench["rest_seconds"] == 180


def test_personal_override_id_wins_over_the_catalogue(store):
    (store / "exercise-map-hevy.json").write_text(json.dumps(
        {"Squat": {"title": "Squat (Barbell)", "id": "ZZZZ9999"}}))
    squat = hevy.compile(copy.deepcopy(PLAN))["routines"][1]["routine"]["exercises"][0]
    assert squat["exercise_template_id"] == "ZZZZ9999"


def test_unmatched_exercise_blocks_compile_and_is_listed(store):
    (store / "hevy-exercise-templates.jsonl").write_text(
        "".join(json.dumps(t) + "\n" for t in CATALOGUE if t["id"] != "AAAA0005"))
    with pytest.raises(SystemExit) as exc:
        hevy.compile(copy.deepcopy(PLAN))
    msg = str(exc.value.code)
    assert "Pull Up" in msg and "custom exercise" in msg and "Bench Press" not in msg


def test_unmatched_exercise_blocks_push_before_any_write(store, api):
    compiled = hevy.compile(copy.deepcopy(PLAN))
    compiled["routines"][0]["routine"]["exercises"][0]["exercise_template_id"] = None
    with pytest.raises(SystemExit, match="no Hevy template id"):
        hevy.push_plan(compiled, "Block")
    assert api.log == []


def test_native_rule_for_another_app_is_refused_naming_the_exercise(store):
    plan = copy.deepcopy(PLAN)
    plan["progression"]["Squat"] = {"native": "liftosaur", "text": "custom() {~ ~}"}
    with pytest.raises(SystemExit) as exc:
        hevy.compile(plan)
    assert "Squat" in str(exc.value.code) and "liftosaur" in str(exc.value.code)


def test_hevy_declares_no_check_and_the_cli_says_so(store, capsys):
    assert "check" not in hevy.CAPABILITIES
    with pytest.raises(SystemExit) as exc:
        contract.require(hevy, "check")
    assert "plan.py validate" in str(exc.value.code) and "stats" in str(exc.value.code)


# --- push ------------------------------------------------------------------

FOLDER = {"routine_folder": {"id": 3670546, "index": 0, "title": "Block"}}


def routine_reply(rid):
    return {"routine": {"id": rid, "title": "x"}}


def test_push_creates_folder_then_routines_and_returns_the_folder_id(store, api):
    api.routes[("POST", "/v1/routine_folders")] = (201, FOLDER)
    api.routes[("POST", "/v1/routines")] = [(201, routine_reply("r1")), (201, routine_reply("r2"))]
    plan_id = hevy.push_plan(hevy.compile(copy.deepcopy(PLAN)), "Block")
    assert plan_id == "3670546"
    w = writes(api)
    assert w[0] == ("POST", "/v1/routine_folders", {"routine_folder": {"title": "Block"}})
    assert [x[1] for x in w[1:]] == ["/v1/routines", "/v1/routines"]
    assert all(x[2]["routine"]["folder_id"] == 3670546 for x in w[1:])
    assert [x[2]["routine"]["title"] for x in w[1:]] == ["Upper A", "Lower A"]


def test_push_403_stops_and_reports_exactly_what_exists(store, api):
    api.routes[("POST", "/v1/routine_folders")] = (201, FOLDER)
    api.routes[("POST", "/v1/routines")] = [(201, routine_reply("r1")),
                                            (403, {"error": "routine limit"})]
    with pytest.raises(SystemExit) as exc:
        hevy.push_plan(hevy.compile(copy.deepcopy(PLAN)), "Block")
    msg = str(exc.value.code)
    assert "403" in msg and "routine limit" in msg and "NOT complete" in msg
    assert "folder 'Block' (id 3670546)" in msg and "routine 'Upper A' (id r1)" in msg
    assert "Lower A" not in msg.split("Already written")[1]
    assert "k-secret" not in msg


def test_push_without_a_folder_id_is_a_failure(store, api):
    api.routes[("POST", "/v1/routine_folders")] = (201, {"routine_folder": {}})
    with pytest.raises(SystemExit, match="NOT complete"):
        hevy.push_plan(hevy.compile(copy.deepcopy(PLAN)), "Block")
    assert len(writes(api)) == 1


# --- get and update --------------------------------------------------------

def get_shape(rid, title, folder=3670546):
    """A routine as GET returns it: with exercise/set `index` and exercise `title`."""
    return {"id": rid, "title": title, "folder_id": folder, "created_at": "x", "updated_at": "y",
            "exercises": [{"index": 0, "title": "Squat (Barbell)", "exercise_template_id": "AAAA0002",
                           "rest_seconds": 60, "notes": "n", "superset_id": None,
                           "sets": [{"index": 0, "type": "normal", "weight_kg": 1, "reps": 1,
                                     "rep_range": None}]}]}


def serve_folder(api, routines):
    api.routes[("GET", "/v1/routine_folders/3670546")] = FOLDER
    api.routes[("GET", "/v1/routines")] = {"page": 1, "page_count": 1 if routines else 0, "routines": routines}


def test_get_plan_returns_the_folders_routines_only(store, api):
    serve_folder(api, [get_shape("r1", "Upper A"), get_shape("r9", "Other", folder=1)])
    got = hevy.get_plan("3670546")
    assert got["folder"]["id"] == 3670546
    assert [r["id"] for r in got["routines"]] == ["r1"]


def test_get_current_uses_the_recorded_program_id(store, api):
    contract.set_program_id("hevy", "3670546")
    serve_folder(api, [])
    assert hevy.get_plan("current")["folder"]["id"] == 3670546
    (store / "profile.json").write_text("{}")
    with pytest.raises(SystemExit, match="No current Hevy plan"):
        hevy.get_plan("current")


def test_update_puts_each_routine_by_title_and_handles_the_array_wrapper(store, api):
    serve_folder(api, [get_shape("r1", "Upper A"), get_shape("r2", "Lower A")])
    api.routes[("PUT", "/v1/routines/r1")] = {"routine": [{"id": "r1"}]}
    api.routes[("PUT", "/v1/routines/r2")] = {"routine": [{"id": "r2"}]}
    assert hevy.update_plan(hevy.compile(copy.deepcopy(PLAN)), "3670546") == "3670546"
    puts = [e for e in api.log if e[0] == "PUT"]
    assert [p[1] for p in puts] == ["/v1/routines/r1", "/v1/routines/r2"]
    assert puts[0][2]["routine"]["folder_id"] == 3670546
    assert puts[0][2]["routine"]["exercises"][0]["sets"][1]["rep_range"] == {"start": 6, "end": 8}


def test_update_with_an_unmatched_day_writes_nothing(store, api):
    serve_folder(api, [get_shape("r1", "Upper A")])
    with pytest.raises(SystemExit, match="Nothing was written"):
        hevy.update_plan(hevy.compile(copy.deepcopy(PLAN)), "3670546")
    assert writes(api) == []


def test_update_failure_midway_lists_what_was_updated(store, api):
    serve_folder(api, [get_shape("r1", "Upper A"), get_shape("r2", "Lower A")])
    api.routes[("PUT", "/v1/routines/r1")] = {"routine": [{"id": "r1"}]}
    api.routes[("PUT", "/v1/routines/r2")] = (400, {"error": "bad"})
    with pytest.raises(SystemExit) as exc:
        hevy.update_plan(hevy.compile(copy.deepcopy(PLAN)), "3670546")
    assert "updated routine 'Upper A'" in str(exc.value.code)


def test_to_put_shape_strips_what_get_returns_and_put_rejects():
    out = hevy.to_put_shape(get_shape("r1", "Upper A"))["routine"]
    assert set(out) == {"title", "folder_id", "exercises"}      # no id, timestamps
    ex = out["exercises"][0]
    assert "index" not in ex and "title" not in ex
    assert "index" not in ex["sets"][0] and ex["sets"][0]["type"] == "normal"
    assert ex["exercise_template_id"] == "AAAA0002"


# --- app.py ----------------------------------------------------------------

def ns(**kw):
    return type("A", (), kw)()


@pytest.fixture
def planfile(tmp_path, monkeypatch):
    path = tmp_path / "meso-01.plan.json"
    path.write_text(json.dumps(PLAN))
    monkeypatch.setattr(app, "load_plan", lambda p: copy.deepcopy(PLAN))
    return str(path)


def test_push_asks_first_and_records_the_program_id(store, api, planfile, monkeypatch, capsys):
    app.cmd_use(ns(name="hevy"))
    api.routes[("POST", "/v1/routine_folders")] = (201, FOLDER)
    api.routes[("POST", "/v1/routines")] = [(201, routine_reply("r1")), (201, routine_reply("r2"))]
    # No tty and no --yes: refuses, writes nothing.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit, match="without confirmation"):
        app.cmd_push(ns(plan=planfile, name="Block", week=None, yes=False))
    assert writes(api) == []
    # Answering no cancels.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    with pytest.raises(SystemExit, match="Cancelled"):
        app.cmd_push(ns(plan=planfile, name="Block", week=None, yes=False))
    assert writes(api) == []
    monkeypatch.setattr("builtins.input", lambda _: "y")
    app.cmd_push(ns(plan=planfile, name="Block", week=None, yes=False))
    assert "3670546" in capsys.readouterr().out
    profile = json.loads((store / "profile.json").read_text())
    assert profile["app"]["program_id"] == "3670546" and profile["app"]["name"] == "hevy"


def test_failed_push_records_no_program_id(store, api, planfile):
    app.cmd_use(ns(name="hevy"))
    api.routes[("POST", "/v1/routine_folders")] = (201, FOLDER)
    api.routes[("POST", "/v1/routines")] = (403, {"error": "limit"})
    with pytest.raises(SystemExit):
        app.cmd_push(ns(plan=planfile, name="Block", week=None, yes=True))
    assert "program_id" not in json.loads((store / "profile.json").read_text())["app"]


def test_check_on_hevy_prints_the_local_only_fallback(store, planfile):
    app.cmd_use(ns(name="hevy"))
    with pytest.raises(SystemExit) as exc:
        app.cmd_check(ns(plan=planfile))
    assert exc.value.code not in (0, None) and "plan.py validate" in str(exc.value.code)


def test_compile_prints_json_and_undeclared_ops_exit_nonzero(store, planfile, capsys):
    app.cmd_use(ns(name="hevy"))
    capsys.readouterr()
    app.cmd_compile(ns(plan=planfile, week=1))
    assert json.loads(capsys.readouterr().out)["week"] == 1
    app.cmd_use(ns(name="liftosaur"))
    with pytest.raises(SystemExit) as exc:
        app.cmd_push(ns(plan=planfile, name="B", week=None, yes=True))
    assert "does not support push_plan" in str(exc.value.code)


def test_update_via_the_cli_needs_confirmation_or_yes(store, api, planfile, capsys):
    app.cmd_use(ns(name="hevy"))
    serve_folder(api, [get_shape("r1", "Upper A"), get_shape("r2", "Lower A")])
    api.routes[("PUT", "/v1/routines/r1")] = {"routine": [{"id": "r1"}]}
    api.routes[("PUT", "/v1/routines/r2")] = {"routine": [{"id": "r2"}]}
    app.cmd_update(ns(plan=planfile, id="3670546", week=None, yes=True))
    assert "Updated hevy plan 3670546" in capsys.readouterr().out
