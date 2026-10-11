import os
import pathlib
import tempfile

# scripts/paths.py deliberately refuses to place personal state inside the skill
# tree, and analyze.py, checkin.py and dashboard.py all call paths.state_dir()
# at import time. Without this, importing those modules under test raises
# SystemExit before any test runs. Set before test modules import. Assigned,
# not setdefault: a developer's real KG_TRAINER_HOME must never be used by tests.
_STORE = pathlib.Path(tempfile.mkdtemp(prefix="kg-trainer-test-")) / "kg-trainer-data"
_STORE.mkdir(parents=True, exist_ok=True)
os.environ["KG_TRAINER_HOME"] = str(_STORE)
