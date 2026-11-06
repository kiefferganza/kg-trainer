"""STEP 1 to STEP 7 of SKILL.md, run as real script processes against recorded fixtures.

One throwaway store per Adapter. HTTP is answered by tests/walkthrough_site/
sitecustomize.py from the contract fixtures, so no socket is opened and no key is
real. Each command is the one SKILL.md tells the agent to run, in its order.
"""
import importlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))


class Walk:
    def __init__(self, app, tmp):
        self.app = app
        self.store = tmp / "kg-trainer-data"
        self.cwd = tmp
        self.env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
        self.env.update(KG_TRAINER_HOME=str(self.store), KG_WALK_APP=app, TZ="UTC",
                        PYTHONPATH=str(ROOT / "tests" / "walkthrough_site"),
                        **{f"{app.upper()}_API_KEY": "walkthrough-key"})

    def run(self, script, *args, ok=(0,)):
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args],
                              capture_output=True, text=True, env=self.env, cwd=str(self.cwd))
        out = done.stdout + done.stderr
        assert done.returncode in ok, f"{script} {' '.join(args)} -> {done.returncode}\n{out}"
        assert "walkthrough-key" not in out
        if os.environ.get("KG_WALK_VERBOSE"):
            print(f"\n$ {script} {' '.join(a.rsplit('/', 1)[-1] for a in args)} -> {done.returncode}\n{out}")
        return out

    @property
    def plan(self):
        return str(self.store / "plans" / "meso-01.plan.json")

    def profile(self):
        return json.loads((self.store / "profile.json").read_text())


@pytest.fixture(params=["liftosaur", "hevy"])
def walk(request, tmp_path):
    return Walk(request.param, tmp_path)


def test_step_1_to_7(walk):
    provider = importlib.import_module(f"contract_fixtures.{walk.app}")

    # Open the session: no Active App yet, so choose one.
    assert walk.app in walk.run("app.py", "apps")
    assert "No Active App" in walk.run("app.py", "apps")
    walk.run("app.py", "pull", ok=(1,))              # refuses until an app is chosen
    walk.run("app.py", "use", walk.app)
    assert walk.profile()["app"]["name"] == walk.app

    # STEP 1: pull and report.
    assert '"units": "kg"' in walk.run("app.py", "ping")
    assert "workouts pulled" in walk.run("app.py", "pull")
    assert (walk.store / "data" / "workouts.jsonl").exists()
    assert "Balance" in walk.run("analyze.py", "report")

    # STEP 4: a Canonical Plan on disk, checked and rendered.
    plan_dir = walk.store / "plans"
    plan_dir.mkdir(parents=True)
    block = {**provider.PLAN, "current_week": 1}   # a block with a later week to progress into
    pathlib.Path(walk.plan).write_text(json.dumps(block, indent=2))
    walk.run("plan.py", "validate", walk.plan)
    assert "chest" in walk.run("plan.py", "stats", walk.plan)
    walk.run("plan.py", "render", walk.plan)
    assert (plan_dir / "meso-01.md").exists()

    # STEP 5: names, compile, check, push.
    assert walk.run("app.py", "exercises", "curl")
    assert walk.run("app.py", "compile", walk.plan).strip()
    if walk.app == "liftosaur":
        walk.run("app.py", "check", walk.plan, ok=(0, 2))
    else:
        assert "validate" in walk.run("app.py", "check", walk.plan, ok=(1,))
    assert "Plan id" in walk.run("app.py", "push", walk.plan, "--name", "Walk", "--yes")
    plan_id = walk.profile()["app"]["program_id"]
    assert plan_id

    # STEP 6: dashboard from the Canonical Plan on disk.
    walk.run("dashboard.py", "build", "--name", "Walk", "--goal", "test", "--no-images")
    assert (walk.store / "dashboard" / "index.html").exists()

    # STEP 7: UPDATE, PROGRESS IT, update, and the weekly check-in.
    walk.run("app.py", "pull")
    walk.run("dashboard.py", "build", "--no-images")
    assert walk.run("plan.py", "progress", walk.plan, "--dry-run")
    walk.run("plan.py", "progress", walk.plan)
    if walk.app == "liftosaur":
        # The recorded program is static, so it is not the program this plan was pushed
        # as: the drift guard must refuse and write nothing.
        assert "Refusing to overwrite" in walk.run("app.py", "update", walk.plan, "--id", plan_id,
                                                   "--yes", ok=(1,))
    else:
        assert "Updated hevy plan" in walk.run("app.py", "update", walk.plan, "--id", plan_id,
                                               "--yes")
    walk.run("checkin.py", "import-app")
    walk.run("checkin.py", "log-weight", "2026-11-01", "80.2")
    walk.run("checkin.py", "trend", "--calories", "2700", ok=(0, 1))
    walk.run("checkin.py", "record", "--calories", "2700", "--protein", "170", "--carbs", "300",
             "--fat", "75", "--sessions", "3", "--planned", "4", "--notes", "walkthrough")
    assert (walk.store / "checkins.jsonl").exists()
