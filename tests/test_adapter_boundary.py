"""The Adapter boundary: analysis and check-in code only talk to the contract."""

import ast
import json
import pathlib
import subprocess
import sys
import urllib.parse

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import analyze
import checkin
from adapters import contract
from adapters import hevy
from adapters import liftosaur as client

# Derived from the shipped Adapters, so a new one is guarded with no edit here. An
# Adapter exposes its base URL as BASE.
APP_MODULES = {m for n in contract.names() for m in (n, f"adapters.{n}")}
APP_URLS = tuple(".".join(urllib.parse.urlparse(contract.load(n).BASE).hostname.split(".")[-2:])
                 for n in contract.names() if hasattr(contract.load(n), "BASE"))

# Outside scripts/adapters/, these two are app-specific on purpose. Both are
# standalone CLIs that nothing else imports.
ALLOWED = {
    # The Liftosaur extras CLI: raw reads, program-* shims and the playground
    # simulator, which is Liftosaur-only by design.
    "scripts/liftosaur.py",
    # Fetches Liftosaur's built-in exercise list and Liftoscript docs from its
    # public reference endpoint into references/.
    "scripts/fetch_reference.py",
}


def imported_modules(source: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{a.name}" for a in node.names}
    return found


def violations(name: str, source: str) -> list[str]:
    bad = ([f"imports {m}" for m in sorted(imported_modules(source)) if m in APP_MODULES]
           if name.endswith(".py") else [])
    bad += [f"contains {u}" for u in APP_URLS if u in source]
    return bad


def tracked_scripts() -> list[str]:
    out = subprocess.run(["git", "ls-files", "scripts"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    return [n for n in out if not n.startswith("scripts/adapters/")]


def test_the_detector_flags_what_it_should():
    assert violations("x.py", "import liftosaur\n") == ["imports liftosaur"]
    assert violations("x.py", "from adapters import hevy\n") == ["imports adapters.hevy"]
    assert violations("x.py", "from adapters.liftosaur import ping\n") == ["imports adapters.liftosaur"]
    assert violations("x.py", 'BASE = "https://api.hevyapp.com"\n') == ["contains hevyapp.com"]
    assert violations("x.py", "from adapters import contract\n") == []


def test_nothing_outside_the_adapters_names_an_app():
    offenders = {}
    for name in tracked_scripts():
        if name in ALLOWED:
            continue
        found = violations(name, (ROOT / name).read_text(errors="ignore"))
        if found:
            offenders[name] = found
    assert offenders == {}


def test_the_allowlist_has_no_stale_entries():
    assert ALLOWED <= set(tracked_scripts())
    for name in ALLOWED:
        assert violations(name, (ROOT / name).read_text()), f"{name} no longer needs allowing"


def test_dashboard_does_not_import_an_app():
    assert violations("dashboard.py", (ROOT / "scripts" / "dashboard.py").read_text()) == []


# --- an unset Active App, and switching apps --------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(analyze, "DATA", data)
    monkeypatch.setattr(analyze, "WORKOUTS", data / "workouts.jsonl")
    monkeypatch.setattr(analyze, "SYNC", data / "sync.json")
    monkeypatch.setattr(analyze, "MEASUREMENTS", data / "measurements.json")
    monkeypatch.setattr(checkin, "HOME", tmp_path)
    monkeypatch.setattr(checkin, "WEIGHTS", tmp_path / "weight-log.csv")
    monkeypatch.setattr(checkin, "CHECKINS", tmp_path / "checkins.jsonl")
    monkeypatch.setattr(contract, "profile_path", lambda: tmp_path / "profile.json")
    return tmp_path


def workout(day, title):
    return {"id": day, "start_time": f"2026-10-{day}T09:00:00Z", "title": "T",
            "exercises": [{"title": title, "sets": []}]}


def test_no_profile_at_all_asks_the_user_to_choose_an_app(store, capsys):
    for run in (lambda: analyze.cmd_pull(type("A", (), {"full": True})()),
                lambda: checkin.cmd_import_app(type("A", (), {})())):
        with pytest.raises(SystemExit) as exc:
            run()
        message = str(exc.value.code)
        assert "app.py use" in message and "hevy" in message and "liftosaur" in message
    assert not (store / "data").exists()


def test_an_old_liftosaur_block_with_no_app_resolves_to_liftosaur(store, monkeypatch):
    (store / "profile.json").write_text(json.dumps(
        {"liftosaur": {"connected": True, "key_source": "env"}}))
    monkeypatch.setattr(client, "pull_history", lambda: [workout("01", "Bench Press")])
    monkeypatch.setattr(client, "pull_bodyweight", lambda: [])

    analyze.cmd_pull(type("A", (), {"full": True})())

    assert contract.active_app() == "liftosaur"
    assert [w["id"] for w in analyze.load_workouts()] == ["01"]


def test_import_app_without_pull_bodyweight_prints_the_fallback(store, monkeypatch):
    (store / "profile.json").write_text(json.dumps({"app": {"name": "liftosaur"}}))
    monkeypatch.setattr(client, "CAPABILITIES", {"ping", "pull_history"})
    with pytest.raises(SystemExit) as exc:
        checkin.cmd_import_app(type("A", (), {})())
    assert "log-weight" in str(exc.value.code)


def test_import_liftosaur_is_gone():
    assert not hasattr(checkin, "cmd_import_liftosaur")
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "checkin.py"), "--help"],
                         capture_output=True, text=True).stdout
    assert "import-app" in out and "import-liftosaur" not in out


def test_switching_apps_replaces_the_cache_and_leaves_the_rest(store, monkeypatch):
    (store / "profile.json").write_text(json.dumps({"app": {"name": "liftosaur"}}))
    monkeypatch.setattr(client, "pull_history",
                        lambda: [workout("01", "Bench Press"), workout("02", "Squat")])
    monkeypatch.setattr(client, "pull_bodyweight", lambda: [])
    analyze.cmd_pull(type("A", (), {"full": True})())
    assert len(analyze.load_workouts()) == 2

    # the user's own records, which no pull may touch
    (store / "checkins.jsonl").write_text('{"date": "2026-10-05", "calories": 2500}\n')
    (store / "weight-log.csv").write_text("date,weight_kg,fat_percent,waist_cm\n2026-10-05,70.0,,\n")
    (store / "plans").mkdir()
    (store / "plans" / "meso-01.md").write_text("# plan\n")
    kept = {p.relative_to(store): p.read_bytes()
            for p in (store / "checkins.jsonl", store / "weight-log.csv",
                      store / "plans" / "meso-01.md")}

    contract.set_active_app("hevy")
    monkeypatch.setattr(hevy, "pull_history", lambda: [workout("09", "Deadlift")])
    monkeypatch.setattr(hevy, "pull_bodyweight", lambda: [])
    analyze.cmd_pull(type("A", (), {"full": True})())

    assert [w["id"] for w in analyze.load_workouts()] == ["09"]   # replaced, not merged
    assert {p: (store / p).read_bytes() for p in kept} == kept
