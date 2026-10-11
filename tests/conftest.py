import os
import pathlib
import tempfile
import time

import pytest

# scripts/paths.py deliberately refuses to place personal state inside the skill
# tree, and analyze.py, checkin.py and dashboard.py all call paths.state_dir()
# at import time. Without this, importing those modules under test raises
# SystemExit before any test runs. Set before test modules import. Assigned,
# not setdefault: a developer's real KG_TRAINER_HOME must never be used by tests.
_STORE = pathlib.Path(tempfile.mkdtemp(prefix="kg-trainer-test-")) / "kg-trainer-data"
_STORE.mkdir(parents=True, exist_ok=True)
os.environ["KG_TRAINER_HOME"] = str(_STORE)

# Weigh-in timestamps are converted to the machine's local date. Pin the zone so
# expected dates do not depend on where the suite runs; tests that care about
# the conversion pass an explicit zone.
os.environ["TZ"] = "UTC"
time.tzset()


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Start every test in an empty directory.

    liftosaur.api_key() falls back to the nearest .env, searching the cwd and
    then its parents. Run from anywhere inside a real checkout, that walk reaches
    the developer's own .env and the suite would read their live API key --
    which silently broke the key-resolution tests and is not something a test
    run should ever touch. Tests that want a .env chdir to one themselves.
    """
    monkeypatch.chdir(tmp_path)
