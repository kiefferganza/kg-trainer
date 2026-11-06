"""The shared Adapter contract suite.

Every module in scripts/adapters/ (found with contract.names(), so a new Adapter is
picked up with no edit here) runs the same checks on its recorded fixtures. The suite
knows nothing about any app; what is app-specific lives in a fixture provider,
tests/contract_fixtures/<name>.py, which supplies:

  respond(method, path, query, body) -> (status, json body) | None
      The recorded HTTP routes. None = no route recorded (the test fails and says so).
  EXPECT        what the fixtures contain: see references/adapters/README.md
  PLAN          a Canonical Plan (needed only when the Adapter declares a plan operation)
  setup(store)  optional: prepare the throwaway store (a cache the Adapter reads)

An Adapter is done when this file passes unchanged. Do not special-case an app here.
No test touches the network: every socket connect is refused, and the shared opener is
replaced by `respond`.
"""

import contextlib
import copy
import datetime as dt
import importlib
import inspect
import io
import json
import math
import os
import pathlib
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import app
import contract_fixtures
import paths
from adapters import contract
from adapters import http as apphttp

# Test seam for the dry run in references/adapters/README.md: a directory holding
# adapters/<name>.py and fixtures/<name>.py for a throwaway stub Adapter.
_EXTRA = os.environ.get("KG_CONTRACT_TEST_EXTRA_ROOT")
if _EXTRA:
    contract.add_adapter_dir(pathlib.Path(_EXTRA) / "adapters")
    contract_fixtures.__path__.append(str(pathlib.Path(_EXTRA) / "fixtures"))

ADAPTERS = contract.names()
SENTINEL = "kgtrainer-SENTINEL-key-9f3a1c7e"
GUIDE = "references/adapters/README.md"

# op -> (min positional args, max positional args)
ARITY = {"ping": (0, 0), "pull_history": (0, 0), "pull_bodyweight": (0, 0),
         "find_exercise": (1, 1), "compile": (1, 2), "push_plan": (2, 2),
         "update_plan": (2, 2), "get_plan": (1, 1), "check": (1, 1)}
PLAN_OPS = ("compile", "push_plan", "update_plan", "get_plan", "check")

WORKOUT_KEYS = {"start_time", "program", "day_name", "week", "day_in_week",
                "duration_seconds", "note", "exercises"}
EXERCISE_KEYS = {"title", "target", "sets", "note"}
SET_KEYS = {"type", "reps", "reps_left", "amrap", "weight_kg", "rpe", "rpe_logged", "duration"}
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


# --- harness ----------------------------------------------------------------

def vocabulary() -> set[str]:
    """Canonical Exercise Names: the shipped list plus the muscle map's keys."""
    names = json.loads((ROOT / "references" / "exercise-muscles.json").read_text())
    listed = (ROOT / "references" / "exercises.txt").read_text().splitlines()[1:]
    return set(names) | {n.strip() for n in listed if n.strip()}


def load_adapter(name):
    try:
        return contract.load(name)
    except SystemExit as exc:
        pytest.fail(f"Adapter {name!r} is not loadable: {exc.code}", pytrace=False)


def find_provider(name):
    """The fixture provider module, or None when the Adapter has none yet."""
    module = f"contract_fixtures.{name}"
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if exc.name != module:
            raise
    return None


class Reply:
    def __init__(self, status, body):
        self.status, self._raw = status, json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Env:
    """One Adapter on its fixtures, with every request recorded and the key sentinel."""

    def __init__(self, adapter, store):
        self.adapter, self.store = adapter, store
        self.requests = []      # (method, url, headers, body)
        self.fault = None       # callable(req) -> exception to raise, or None
        self._provider = None

    @property
    def provider(self):
        """The fixture provider. Looked up on first use, so the declaration checks
        run (and report) before a provider exists."""
        if self._provider is None:
            found = find_provider(self.adapter.NAME)
            if found is None:
                pytest.fail(f"Adapter {self.adapter.NAME!r} has no fixture provider. Create "
                            f"tests/contract_fixtures/{self.adapter.NAME}.py with respond(), "
                            f"EXPECT and (for a plan-capable Adapter) PLAN. See {GUIDE}.",
                            pytrace=False)
            if hasattr(found, "setup"):
                found.setup(self.store)
            self._provider = found
        return self._provider

    @property
    def expect(self):
        return getattr(self.provider, "EXPECT", {})

    def opener(self, req, timeout=None):
        url = urllib.parse.urlparse(req.full_url)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        body = json.loads(req.data) if req.data else None
        method = req.get_method()
        self.requests.append((method, req.full_url, dict(req.header_items()), body))
        if self.fault is not None:
            raise self.fault(req)
        got = self.provider.respond(method, url.path, query, body)
        if got is None:
            raise AssertionError(
                f"{self.adapter.NAME} made a request the fixture provider has no response "
                f"for: {method} {url.path} {query or ''}. Add the route to "
                f"tests/contract_fixtures/{self.adapter.NAME}.py:respond().")
        status, payload = got
        if status >= 300:
            raise urllib.error.HTTPError(req.full_url, status, "err", {},
                                         io.BytesIO(json.dumps(payload).encode()))
        return Reply(status, payload)

    def fail_with(self, make):
        self.fault = make

    def plan(self):
        plan = getattr(self.provider, "PLAN", None)
        if plan is None:
            pytest.fail(f"{self.adapter.NAME} declares a plan operation but its fixture "
                        f"provider has no PLAN (a Canonical Plan dict). See {GUIDE}.",
                        pytrace=False)
        return copy.deepcopy(plan)

    def ops(self):
        return [op for op in contract.OPERATIONS if op in self.adapter.CAPABILITIES]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing in this module may open a socket."""
    def refuse(*a, **k):
        raise AssertionError("an Adapter tried to open a network connection. Every "
                             "request must go through adapters.http (apphttp.request).")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(params=ADAPTERS)
def env(request, tmp_path, monkeypatch):
    adapter = load_adapter(request.param)
    store = tmp_path / "kg-trainer-data"
    store.mkdir()
    monkeypatch.setattr(paths, "state_dir", lambda: store)
    for other in ADAPTERS:
        monkeypatch.delenv(apphttp.env_name(other), raising=False)
    monkeypatch.setenv(apphttp.env_name(adapter.NAME), SENTINEL)
    e = Env(adapter, store)
    if find_provider(adapter.NAME) is not None:
        e.provider  # prepares the store; a missing provider is reported by the tests that need it
    monkeypatch.setattr(apphttp.OPENER, "open", e.opener)
    return e


def expect(env, *path):
    """A required EXPECT entry, or a failure naming the fixture provider's gap."""
    node = env.expect
    for key in path:
        if not isinstance(node, dict) or key not in node:
            pytest.fail(f"{env.adapter.NAME}'s fixture provider EXPECT is missing "
                        f"{'/'.join(path)}. See {GUIDE}.", pytrace=False)
        node = node[key]
    return node


def op(env, name):
    return getattr(env.adapter, name)


# --- discovery and declarations ----------------------------------------------

def test_every_adapter_module_is_discovered():
    shipped = {p.stem for p in (ROOT / "scripts" / "adapters").glob("*.py")
               if p.stem not in ("__init__", "http", "contract")}
    assert shipped <= set(ADAPTERS), (
        f"adapters {sorted(shipped - set(ADAPTERS))} exist but contract.names() misses them")
    assert ADAPTERS, "no Adapters discovered"


def test_declares_name_and_capabilities(env):
    a = env.adapter
    assert a.NAME == a.__name__.rsplit(".", 1)[-1], (
        f"NAME is {a.NAME!r} but the module is {a.__name__!r}: they must match")
    assert isinstance(a.CAPABILITIES, (set, frozenset)), "CAPABILITIES must be a set of names"
    unknown = set(a.CAPABILITIES) - set(contract.OPERATIONS)
    assert not unknown, (f"CAPABILITIES names {sorted(unknown)}, which are not contract "
                         f"operations ({', '.join(contract.OPERATIONS)})")


def test_required_operations_are_declared_and_implemented(env):
    for name in contract.REQUIRED:
        assert name in env.adapter.CAPABILITIES, (
            f"{env.adapter.NAME} must declare {name!r} in CAPABILITIES: reading history "
            "is the only required Capability")
        assert callable(getattr(env.adapter, name, None)), f"{name}() is not defined"


def test_no_declared_operation_is_missing(env):
    a = env.adapter
    declared_missing = [o for o in a.CAPABILITIES
                        if o in contract.OPERATIONS and not callable(getattr(a, o, None))]
    assert not declared_missing, (
        f"declared but not implemented: {declared_missing}. Define the function, or remove "
        "it from CAPABILITIES so the CLI prints its Fallback")


def test_no_implemented_operation_is_undeclared(env):
    a = env.adapter
    implemented = {o for o in contract.OPERATIONS
                   if callable(getattr(a, o, None))
                   and getattr(getattr(a, o), "__module__", None) == a.__name__}
    undeclared = sorted(implemented - set(a.CAPABILITIES))
    assert not undeclared, (
        f"implemented but not declared: {undeclared}. Add them to CAPABILITIES, or rename "
        "the functions if they are not contract operations")


def test_native_progression_is_a_bool_and_comes_with_a_drift_reader(env):
    a = env.adapter
    declared = getattr(a, "NATIVE_PROGRESSION", False)
    assert isinstance(declared, bool), (
        f"NATIVE_PROGRESSION must be True or False (or absent), got {declared!r}")
    if not declared:
        return
    missing = [o for o in ("compile", "update_plan", "get_plan") if o not in a.CAPABILITIES]
    assert not missing, (
        f"NATIVE_PROGRESSION = True needs {missing} in CAPABILITIES: the drift guard fetches "
        "the app's program with get_plan before update_plan writes")
    assert callable(getattr(a, "prescriptions", None)), (
        "NATIVE_PROGRESSION = True needs prescriptions(payload, week): the working sets "
        f"the app prescribes, as [(day, exercise, [(reps, load_kg, amrap)])] ({GUIDE})")


def test_a_native_progression_app_reads_back_what_it_was_sent_without_drift(env):
    if not contract.native_progression(env.adapter):
        pytest.skip("no native progression")
    plan = env.plan()
    week = plan["current_week"]
    sent = {"id": "x", "name": "n", "text": env.adapter.compile(plan)}
    for wk in plan["weeks"]:
        app_side = env.adapter.prescriptions(sent, wk["number"])
        assert app_side is not None, f"prescriptions() found no week {wk['number']} in the compile"
        assert contract.drift(app_side, contract.plan_prescriptions(plan, wk["number"])) == [], (
            f"week {wk['number']}: prescriptions(compile(plan)) must equal the plan's own working "
            "sets, or every update would be refused as drift")
    # one more rep on a working set is seen as drift, in that exercise only
    moved = copy.deepcopy(plan)
    ex = next(w for w in moved["weeks"] if w["number"] == week)["days"][0]["exercises"][0]
    next(s for s in ex["sets"] if s["kind"] != "warmup")["reps"] += 1
    changed = {"id": "x", "name": "n", "text": env.adapter.compile(moved)}
    rows = contract.drift(env.adapter.prescriptions(changed, week),
                          contract.plan_prescriptions(plan, week))
    assert [r[1] for r in rows] == [ex["exercise"]]


def test_operation_signatures_follow_the_contract(env):
    for name in env.ops():
        if not callable(getattr(env.adapter, name, None)):
            continue            # reported by test_no_declared_operation_is_missing
        low, high = ARITY[name]
        sig = inspect.signature(op(env, name))
        for n in range(low, high + 1):
            try:
                sig.bind(*[None] * n)
            except TypeError:
                pytest.fail(f"{env.adapter.NAME}.{name}{sig} cannot be called with {n} "
                            f"positional argument(s); the contract is {name}"
                            f"({', '.join(['x'] * low)}{'[, week]' if high > low else ''})")
        with pytest.raises(TypeError):
            sig.bind(*[None] * (high + 1))


def test_the_fixture_provider_is_complete(env):
    p = env.provider  # fails with the pointer to the guide when there is none
    assert callable(getattr(p, "respond", None)), (
        "the fixture provider needs respond(method, path, query, body) -> (status, body)")
    assert isinstance(getattr(p, "EXPECT", None), dict), "the fixture provider needs EXPECT"
    expect(env, "history", "count")
    expect(env, "history", "unclassified")
    expect(env, "history", "weights_kg")
    expect(env, "history", "rpe")
    if "pull_bodyweight" in env.adapter.CAPABILITIES:
        expect(env, "bodyweight")
    if "find_exercise" in env.adapter.CAPABILITIES:
        expect(env, "find")
    if set(PLAN_OPS) & set(env.adapter.CAPABILITIES):
        env.plan()
    if {"update_plan", "get_plan"} & set(env.adapter.CAPABILITIES):
        expect(env, "plan_id")


# --- ping ----------------------------------------------------------------------

def test_ping_names_the_account_and_units(env):
    info = env.adapter.ping()
    assert isinstance(info, dict) and set(info) >= {"account", "units"}, (
        f"ping() must return {{'account': str, 'units': 'kg'|'lb'}}, got {info!r}")
    assert isinstance(info["account"], str) and info["account"]
    assert info["units"] in ("kg", "lb")


# --- the Workout Log -----------------------------------------------------------

def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def test_workout_log_shape(env):
    log = env.adapter.pull_history()
    assert isinstance(log, list)
    assert len(log) == expect(env, "history", "count"), (
        f"pull_history() returned {len(log)} workouts, the fixtures hold "
        f"{env.expect['history']['count']}: a pull must read every page")
    json.dumps(log)  # it is written to workouts.jsonl
    for w in log:
        assert isinstance(w, dict) and WORKOUT_KEYS <= set(w), (
            f"a workout is missing {sorted(WORKOUT_KEYS - set(w))}: keep today's "
            "workouts.jsonl shape (spec decision 12)")
        assert STAMP.fullmatch(w["start_time"] or ""), (
            f"start_time {w['start_time']!r} must be UTC ISO like 2026-10-02T10:00:00Z")
        assert isinstance(w["exercises"], list)
        for ex in w["exercises"]:
            assert EXERCISE_KEYS <= set(ex), f"exercise missing {sorted(EXERCISE_KEYS - set(ex))}"
            assert isinstance(ex["title"], str) and ex["title"]
            for s in ex["sets"]:
                assert SET_KEYS <= set(s), f"set missing {sorted(SET_KEYS - set(s))}"
                assert isinstance(s["rpe_logged"], bool), "rpe_logged must be True or False"
                for field in ("reps", "weight_kg", "rpe"):
                    assert s[field] is None or number(s[field]), (
                        f"set {field} must be a number or None, got {s[field]!r}")


def test_workout_log_is_newest_first(env):
    starts = [w["start_time"] for w in env.adapter.pull_history()]
    assert starts == sorted(starts, reverse=True), (
        "pull_history() must return the Workout Log newest first")


def test_weights_are_kilograms(env):
    by_start = {}
    for w in env.adapter.pull_history():
        by_start.setdefault(w["start_time"], []).append(w)
    for start, title, index, kg in expect(env, "history", "weights_kg"):
        sets = [s for w in by_start.get(start, []) for ex in w["exercises"]
                if ex["title"] == title for s in [ex["sets"][index]] if index < len(ex["sets"])]
        assert sets, f"no set {index} of {title!r} on {start}; the fixtures say there is one"
        assert sets[0]["weight_kg"] == pytest.approx(kg, abs=0.01), (
            f"{title} set {index} on {start}: weight_kg is {sets[0]['weight_kg']}, expected "
            f"{kg} kg. Weights leave the Adapter in kilograms (convert pounds on read)")


def test_exercise_titles_are_canonical_or_unclassified(env):
    titles = {ex["title"] for w in env.adapter.pull_history() for ex in w["exercises"]}
    unclassified = titles - vocabulary()
    declared = set(expect(env, "history", "unclassified"))
    assert unclassified == declared, (
        f"titles outside the Canonical Exercise Names: {sorted(unclassified)}; the fixture "
        f"provider expects {sorted(declared)}. Translate app names to canonical ones in the "
        "Adapter; keep an unmatched title as the app wrote it (never fuzzy-match)")
    assert titles & vocabulary(), "no pulled exercise has a Canonical Exercise Name"


def test_rpe_is_only_what_was_logged(env):
    by_start = {w["start_time"]: w for w in env.adapter.pull_history()}
    for start, title, index, logged, rpe in expect(env, "history", "rpe"):
        ex = next(e for e in by_start[start]["exercises"] if e["title"] == title)
        s = ex["sets"][index]
        assert "rpe_logged" in s, "every set needs rpe_logged (True only for RPE the athlete logged)"
        assert s["rpe_logged"] is logged, (
            f"{title} set {index} on {start}: rpe_logged is {s['rpe_logged']}, expected "
            f"{logged}. A prescribed target is never a logged RPE")
        if logged:
            assert s["rpe"] == pytest.approx(rpe)
    for w in by_start.values():
        for ex in w["exercises"]:
            for s in ex["sets"]:
                assert not s["rpe_logged"] or s["rpe"] is not None, (
                    "rpe_logged is True on a set with no rpe")


def test_the_log_feeds_the_report(env):
    import analyze
    report = analyze.build_report(env.adapter.pull_history(), analyze.load_muscles())
    assert report["workout_count"] == expect(env, "history", "count")
    assert analyze.fmt(report)


# --- bodyweight ----------------------------------------------------------------

def test_bodyweight_is_one_reading_per_day_in_kg(env):
    if "pull_bodyweight" not in env.adapter.CAPABILITIES:
        pytest.skip("pull_bodyweight not declared (Fallback: checkin.py log-weight)")
    rows = env.adapter.pull_bodyweight()
    assert all(isinstance(r, tuple) and len(r) == 2 and isinstance(r[0], dt.date)
               and not isinstance(r[0], dt.datetime) and number(r[1]) for r in rows), (
        "pull_bodyweight() returns [(datetime.date, kg)]")
    days = [d for d, _ in rows]
    assert len(days) == len(set(days)), (
        f"two readings for one day: {sorted(d for d in set(days) if days.count(d) > 1)}. "
        "Keep the latest reading of each day")
    got = {d.isoformat(): kg for d, kg in rows}
    want = expect(env, "bodyweight")
    assert got.keys() == want.keys(), f"days returned {sorted(got)}, expected {sorted(want)}"
    for day, kg in want.items():
        assert got[day] == pytest.approx(kg, abs=0.01), f"{day}: {got[day]} kg, expected {kg}"


# --- find_exercise -----------------------------------------------------------

def test_find_exercise_returns_canonical_names(env):
    if "find_exercise" not in env.adapter.CAPABILITIES:
        pytest.skip("find_exercise not declared (Fallback: the shipped canonical list)")
    pattern, canonical = expect(env, "find")
    hits = env.adapter.find_exercise(pattern)
    assert all(isinstance(h, dict) and isinstance(h.get("canonical"), str) and "app_id" in h
               for h in hits), "find_exercise() returns [{'canonical': str, 'app_id': str|None}]"
    assert canonical in {h["canonical"] for h in hits}
    assert {h["canonical"] for h in hits} <= vocabulary()
    assert env.adapter.find_exercise("zzz-no-such-exercise-zzz") == []


# --- plans -----------------------------------------------------------------------

def no_requests(env):
    """Fail loudly if the code under test makes any request."""
    def boom(req):
        raise AssertionError(f"{req.get_method()} {req.full_url}: compile must be local, "
                             "with no network call")
    env.fail_with(boom)


def test_compile_is_local_deterministic_and_does_not_touch_the_plan(env):
    if "compile" not in env.adapter.CAPABILITIES:
        pytest.skip("compile not declared (Fallback: meso-NN.md entered by hand)")
    plan = env.plan()
    before = copy.deepcopy(plan)
    no_requests(env)
    first = env.adapter.compile(plan)
    second = env.adapter.compile(plan)
    assert env.requests == [], "compile made a network call"
    assert first and isinstance(first, (str, dict))
    assert first == second, "compile is not deterministic"
    assert plan == before, "compile mutated the Canonical Plan"
    env.adapter.compile(plan, week=plan["current_week"])
    assert env.requests == []


def test_compile_refuses_another_apps_native_rule_naming_the_exercise(env):
    if "compile" not in env.adapter.CAPABILITIES:
        pytest.skip("compile not declared")
    plan = env.plan()
    name = plan["weeks"][0]["days"][0]["exercises"][0]["exercise"]
    plan["progression"][name] = {"native": "some-other-app", "text": "x"}
    with pytest.raises(SystemExit) as exc:
        env.adapter.compile(plan)
    message = str(exc.value.code)
    assert exc.value.code not in (0, None) and name in message and "some-other-app" in message, (
        f"compiling another app's Native Rule must fail naming the exercise ({name!r}) and "
        f"the app; got {message!r}")


def test_plan_operations_round_trip(env):
    ops = env.ops()
    if not {"compile", "push_plan"} <= set(ops):
        pytest.skip("needs compile and push_plan")
    compiled = env.adapter.compile(env.plan())
    plan_id = env.adapter.push_plan(compiled, "Contract suite")
    assert isinstance(plan_id, str) and plan_id.strip(), (
        f"push_plan() must return the app's plan id as a non-empty string, got {plan_id!r}")
    contract.set_program_id(env.adapter.NAME, plan_id)
    writes = [r for r in env.requests if r[0] in ("POST", "PUT")]
    assert writes, "push_plan made no write request"
    if "update_plan" in ops:
        updated = env.adapter.update_plan(compiled, plan_id)
        assert isinstance(updated, str) and updated.strip(), "update_plan() must return the id"
    if "get_plan" in ops:
        for ref in (plan_id, "current"):
            got = env.adapter.get_plan(ref)
            assert isinstance(got, dict) and got, f"get_plan({ref!r}) must return the plan payload"


def test_check_returns_ok_weekly_sets_and_errors(env):
    if "check" not in env.adapter.CAPABILITIES:
        pytest.skip("check not declared (Fallback: local plan.py validate/stats)")
    result = env.adapter.check(env.adapter.compile(env.plan()))
    assert set(result) == {"ok", "weekly_sets", "errors"}, (
        f"check() returns {{'ok', 'weekly_sets', 'errors'}}, got {sorted(result)}")
    assert isinstance(result["ok"], bool) and isinstance(result["errors"], list)
    assert isinstance(result["weekly_sets"], dict)
    assert all(isinstance(m, str) and number(n) for m, n in result["weekly_sets"].items())
    assert result["ok"] == (not result["errors"])


# --- keys never leak ------------------------------------------------------------

def run_everything(env):
    """Call each declared operation once, collecting how each ended."""
    plan = env.plan() if set(PLAN_OPS) & set(env.adapter.CAPABILITIES) else None
    compiled = env.adapter.compile(plan) if "compile" in env.adapter.CAPABILITIES else None
    calls = {"ping": (), "pull_history": (), "pull_bodyweight": (),
             "find_exercise": ("curl",)}
    if {"update_plan", "get_plan"} & set(env.adapter.CAPABILITIES):
        known = expect(env, "plan_id")
        calls["get_plan"] = (known,)
    if compiled is not None:
        calls.update(push_plan=(compiled, "Contract suite"),
                     update_plan=(compiled, calls.get("get_plan", ("1",))[0]), check=(compiled,))
    outcomes = {}
    for name in env.ops():
        if name in calls:
            start = len(env.requests)
            try:
                result = ("returned", op(env, name)(*calls[name]))
            except SystemExit as exc:
                result = ("exited", str(exc.code))
            outcomes[name] = (*result, len(env.requests) > start)
    return outcomes


def test_the_key_is_sent_over_https_and_never_printed(env, capsys):
    run_everything(env)
    assert env.requests, "no request was made"
    for method, url, headers, _ in env.requests:
        assert url.startswith("https://"), f"{method} {url}: requests must use https"
    sent = [v for _, _, headers, _ in env.requests for v in headers.values()]
    assert any(SENTINEL in str(v) for v in sent), (
        f"the key from ${apphttp.env_name(env.adapter.NAME)} was never sent in a header: "
        "resolve it with adapters.http.api_key and send it with adapters.http.request")
    out = capsys.readouterr()
    assert SENTINEL not in out.out + out.err


FAULTS = {
    "401": lambda req: urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                              io.BytesIO(b'{"error": {"code": "unauthorized"}}')),
    "500": lambda req: urllib.error.HTTPError(req.full_url, 500, "boom", {},
                                              io.BytesIO(b"<html>oops</html>")),
    "302": lambda req: urllib.error.HTTPError(req.full_url, 302, "Found", {},
                                              io.BytesIO(b"<html>moved</html>")),
    "network": lambda req: urllib.error.URLError(f"no route, token={SENTINEL}"),
}


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_errors_name_the_operation_and_never_contain_the_key(env, fault, capsys):
    env.fail_with(FAULTS[fault])
    outcomes = run_everything(env)
    status = "" if fault == "network" else fault
    problems = []
    for name, (how, value, touched) in outcomes.items():
        if not touched:
            continue            # a local operation (compile, find_exercise)
        text = value if how == "exited" else json.dumps(value)
        if SENTINEL in text:
            problems.append(f"{name}(): the key appears in its failure: {text!r}")
        elif name == "check" and how == "returned":
            if value["ok"] is not False or not any(status in e for e in value["errors"]):
                problems.append(f"check() must report the HTTP {fault} in errors, got "
                                f"{value['errors']}")
        elif how != "exited":
            problems.append(f"{name}() swallowed an HTTP {fault} and returned {value!r}: a "
                            "failed call must exit loudly, never return a partial result")
        elif not (status in text or fault == "network"):
            problems.append(f"{name}(): the failure must name the HTTP status ({status}); "
                            f"got {text!r}")
    out = capsys.readouterr()
    if SENTINEL in out.out + out.err:
        problems.append("the key was printed to stdout/stderr")
    assert not problems, "\n".join(problems)


# --- an undeclared operation prints its Fallback --------------------------------

CLI = {"pull_bodyweight": ("cmd_bodyweight", {}),
       "find_exercise": ("cmd_exercises", {"pattern": "x"}),
       "compile": ("cmd_compile", {"plan": "plan.json", "week": None}),
       "push_plan": ("cmd_push", {"plan": "plan.json", "name": "n", "week": None, "yes": True}),
       "update_plan": ("cmd_update", {"plan": "plan.json", "id": "1", "week": None,
                                      "yes": True}),
       "get_plan": ("cmd_get", {"id": "1"}),
       "check": ("cmd_check", {"plan": "plan.json"})}


def test_an_undeclared_operation_prints_its_fallback_and_exits_nonzero(env, monkeypatch):
    (env.store / "profile.json").write_text(json.dumps({"app": {"name": env.adapter.NAME}}))
    monkeypatch.setattr(contract, "profile_path", lambda: env.store / "profile.json")
    for name in contract.OPERATIONS:
        if name in env.adapter.CAPABILITIES or name not in CLI:
            continue
        command, args = CLI[name]
        with pytest.raises(SystemExit) as exc:
            getattr(app, command)(type("Args", (), args)())
        message = str(exc.value.code)
        assert exc.value.code not in (0, None), f"app.py {command} must exit non-zero"
        assert f"{env.adapter.NAME} does not support {name}" in message, message
        assert contract.FALLBACKS[name] in message, (
            f"app.py {command} did not print the Fallback for {name}: {message!r}")
    assert env.requests == [], "a refused operation must not reach the network"


def test_app_py_exits_nonzero_in_a_real_process(env):
    """The same refusal, through the actual CLI process (shipped Adapters only)."""
    if pathlib.Path(env.adapter.__file__).parent != ROOT / "scripts" / "adapters":
        pytest.skip("not a shipped Adapter: a subprocess cannot see the test seam")
    missing = [o for o in contract.OPERATIONS if o not in env.adapter.CAPABILITIES and o in CLI]
    if not missing:
        pytest.skip("declares every operation")
    (env.store / "profile.json").write_text(json.dumps({"app": {"name": env.adapter.NAME}}))
    command = {"pull_bodyweight": ["bodyweight"], "find_exercise": ["exercises", "x"],
               "compile": ["compile", "p.json"], "push_plan": ["push", "p.json", "--name", "n",
                                                                "--yes"],
               "update_plan": ["update", "p.json", "--id", "1", "--yes"],
               "get_plan": ["get", "1"], "check": ["check", "p.json"]}[missing[0]]
    child = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    child["KG_TRAINER_HOME"] = str(env.store)
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "app.py"), *command],
                          capture_output=True, text=True, env=child, cwd=str(env.store))
    assert done.returncode != 0
    assert contract.FALLBACKS[missing[0]] in done.stderr
